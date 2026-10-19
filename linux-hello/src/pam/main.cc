#include <cerrno>
#include <cstdlib>

#include <glob.h>
#include <libintl.h>
#include <pthread.h>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/syslog.h>
#include <sys/types.h>
#include <syslog.h>
#include <unistd.h>

#include <chrono>
#include <condition_variable>
#include <cstring>
#include <fstream>
#include <functional>
#include <future>
#include <mutex>
#include <string>
#include <tuple>

#include "layered_config.hh"

#include <security/pam_appl.h>
#include <security/pam_ext.h>
#include <security/pam_modules.h>

#include "daemon_client.hh"
#include "enter_device.hh"
#include "main.hh"
#include "optional_task.hh"
#include <paths.hh>

const auto DEFAULT_TIMEOUT =
    std::chrono::duration<int, std::chrono::milliseconds::period>(100);
const auto MAX_RETRIES = 5;

// RAII guard to ensure closelog() is called when identify() returns,
// regardless of which early-return path is taken.
struct SyslogGuard {
  ~SyslogGuard() { closelog(); }
};

#define S(msg) gettext(msg)

/**
 * Report a failed verification
 * @param  result        The result code of the Linux Hello service
 * @param  conv_function The PAM conversation function
 * @return               A PAM return code
 */
auto linux_hello_error(unsigned result,
                 const std::function<int(int, const char *)> &conv_function)
    -> int {
  switch (result) {
  case CompareError::NO_FACE_MODEL:
    syslog(LOG_NOTICE, "Failure, no face model known");
    return PAM_AUTHINFO_UNAVAIL;
  case CompareError::TIMEOUT_REACHED:
    conv_function(PAM_ERROR_MSG, S("Failure, timeout reached"));
    syslog(LOG_ERR, "Failure, timeout reached");
    break;
  case CompareError::ABORT:
    syslog(LOG_ERR, "Failure, general abort");
    break;
  case CompareError::TOO_DARK:
    conv_function(PAM_ERROR_MSG, S("Face detection image too dark"));
    syslog(LOG_ERR, "Failure, image too dark");
    break;
  case CompareError::INVALID_DEVICE:
    syslog(LOG_ERR, "Failure, the camera could not be opened");
    return PAM_AUTHINFO_UNAVAIL;
  case CompareError::RUBBERSTAMP:
    syslog(LOG_ERR, "Failure, a rubberstamp check failed");
    break;
  case CompareError::CANCELLED:
    syslog(LOG_INFO, "Face authentication cancelled");
    break;
  default:
    conv_function(PAM_ERROR_MSG,
                  (std::string(S("Unknown error: ")) + std::to_string(result))
                      .c_str());
    syslog(LOG_ERR, "Failure, unknown error %u", result);
  }

  return PAM_AUTH_ERR;
}

/**
 * Format the success message if the status is successful or log the error in
 * the other case
 * @param  username      Username
 * @param  status        Status code
 * @param  config        INI  configuration
 * @param  conv_function PAM conversation function
 * @return          Returns the conversation function return code
 */
auto linux_hello_status(char *username, unsigned result, const LayeredConfig &config,
                  const std::function<int(int, const char *)> &conv_function)
    -> int {
  if (result != 0) {
    return linux_hello_error(result, conv_function);
  }

  if (!config.GetBoolean("core", "no_confirmation", true)) {
    // Construct confirmation text from i18n string
    std::string confirm_text(S("Identified face as {}"));
    std::string identify_msg =
        confirm_text.replace(confirm_text.find("{}"), 2, std::string(username));
    conv_function(PAM_TEXT_INFO, identify_msg.c_str());
  }

  syslog(LOG_INFO, "Login approved");

  return PAM_SUCCESS;
}

/**
 * Check if Linux Hello should be enabled according to the configuration and the
 * environment.
 * @param  config INI configuration
 * @param  username Username
 * @return        Returns PAM_AUTHINFO_UNAVAIL if it shouldn't be enabled,
 * PAM_SUCCESS otherwise
 */
auto check_enabled(const LayeredConfig &config, const char *username) -> int {
  // Stop executing if Linux Hello has been disabled in the config
  if (config.GetBoolean("core", "disabled", false)) {
    syslog(LOG_INFO, "Skipped authentication, Linux Hello is disabled");
    return PAM_AUTHINFO_UNAVAIL;
  }

  // Stop if we're in a remote shell and configured to exit
  if (config.GetBoolean("core", "abort_if_ssh", true)) {
    if (checkenv("SSH_CONNECTION") || checkenv("SSH_CLIENT") ||
        checkenv("SSH_TTY") || checkenv("SSHD_OPTS")) {
      syslog(LOG_INFO, "Skipped authentication, SSH session detected");
      return PAM_AUTHINFO_UNAVAIL;
    }
  }

  // Try to detect the laptop lid state and stop if it's closed
  if (config.GetBoolean("core", "abort_if_lid_closed", true)) {
    glob_t glob_result;

    // Get any files containing lid state
    int return_value =
        glob("/proc/acpi/button/lid/*/state", 0, nullptr, &glob_result);

    if (return_value != 0) {
      syslog(LOG_ERR, "Failed to read files from glob: %d", return_value);
      if (errno != 0) {
        syslog(LOG_ERR, "Underlying error: %s (%d)", strerror(errno), errno);
      }
    } else {
      for (size_t i = 0; i < glob_result.gl_pathc; i++) {
        std::ifstream file(std::string(glob_result.gl_pathv[i]));
        std::string lid_state;
        std::getline(file, lid_state, static_cast<char>(file.eof()));

        if (lid_state.find("closed") != std::string::npos) {
          globfree(&glob_result);

          syslog(LOG_INFO, "Skipped authentication, closed lid detected");
          return PAM_AUTHINFO_UNAVAIL;
        }
      }
    }
    globfree(&glob_result);
  }

  // Skip users without a face model without starting the service. Only
  // conclusive as root: other callers, like the lock screen running as the
  // user, cannot look into the models directory and ask the service.
  auto model_path = std::string(USER_MODELS_DIR) + "/" + username + ".dat";
  struct stat stat_;
  if (stat(model_path.c_str(), &stat_) != 0 && errno == ENOENT) {
    return PAM_AUTHINFO_UNAVAIL;
  }

  return PAM_SUCCESS;
}

/**
 * The main function, runs the identification and authentication
 * @param  pamh     The handle to interface directly with PAM
 * @param  flags    Flags passed on to us by PAM, XORed
 * @param  argc     Amount of rules in the PAM config (disregarded)
 * @param  argv     Options defined in the PAM config
 * @param  ask_auth_tok True if we should ask for a password too
 * @return          Returns a PAM return code
 */
auto identify(pam_handle_t *pamh, int flags, int argc, const char **argv,
              bool ask_auth_tok) -> int {
  openlog("pam_linux_hello", 0, LOG_AUTHPRIV);
  SyslogGuard syslog_guard;

  // "no_password": never prompt for a password in parallel, for stacks
  // without one such as the lock screen's non-interactive kde-face
  bool no_password = false;
  for (int i = 0; i < argc; i++) {
    if (std::string(argv[i]) == "no_password") {
      no_password = true;
    }
  }

  LayeredConfig config(VENDOR_CONFIG_FILE_PATH, CONFIG_FILE_PATH);

  // Error out if we could not read the config files
  if (config.ParseError() != 0) {
    syslog(LOG_ERR, "Failed to parse the configuration: %s, %s (%d)",
           VENDOR_CONFIG_FILE_PATH, CONFIG_FILE_PATH, config.ParseError());
    return PAM_AUTHINFO_UNAVAIL;
  }

  // Will contain the responses from PAM functions
  int pam_res = PAM_IGNORE;

  // Get the username from PAM, needed to match correct face model
  char *username = nullptr;
  pam_res = pam_get_user(pamh, const_cast<const char **>(&username), nullptr);
  if (pam_res != PAM_SUCCESS) {
    syslog(LOG_ERR, "Failed to get username");
    return pam_res;
  }

  // Check if we should continue
  pam_res = check_enabled(config, username);
  if (pam_res != PAM_SUCCESS) {
    return pam_res;
  }

  Workaround workaround =
      get_workaround(config.GetString("core", "workaround", "input"));

  // Will contain PAM conversation structure
  struct pam_conv *conv = nullptr;
  const void **conv_ptr =
      const_cast<const void **>(reinterpret_cast<void **>(&conv));

  // Retrieve the PAM conversation structure
  pam_res = pam_get_item(pamh, PAM_CONV, conv_ptr);
  if (pam_res != PAM_SUCCESS) {
    syslog(LOG_ERR, "Failed to acquire conversation");
    return pam_res;
  }

  // Wrap the PAM conversation function in our own, easier function
  auto conv_function = [conv](int msg_type, const char *msg_str) {
    const struct pam_message msg = {.msg_style = msg_type, .msg = msg_str};
    const struct pam_message *msgp = &msg;

    struct pam_response res = {};
    struct pam_response *resp = &res;

    return conv->conv(1, &msgp, &resp, conv->appdata_ptr);
  };

  // Initialize gettext
  setlocale(LC_ALL, "");
  bindtextdomain(GETTEXT_PACKAGE, LOCALEDIR);
  textdomain(GETTEXT_PACKAGE);

  if (config.GetBoolean("core", "detection_notice", true)) {
    if ((conv_function(PAM_TEXT_INFO, S("Attempting facial authentication"))) !=
        PAM_SUCCESS) {
      syslog(LOG_ERR, "Failed to send detection notice");
    }
  }

  // The service gets the scan timeout plus time for a cold start (loading
  // the recognition models) and for opening the camera
  double scan_timeout = 4;
  try {
    scan_timeout = std::stod(config.GetString("video", "timeout", "4"));
  } catch (const std::exception &) {
  }
  VerifyRequest request(username, static_cast<unsigned>(scan_timeout) + 30);

  // NOTE: We should replace mutex and condition_variable by atomic wait, but
  // it's too recent (C++20)
  std::mutex mutx;
  std::condition_variable convar;
  ConfirmationType confirmation_type(ConfirmationType::Unset);

  // This task waits for the service's answer
  optional_task<VerifyRequest::Status> verify_task([&] {
    auto status = request.run();
    {
      std::unique_lock<std::mutex> lock(mutx);
      if (confirmation_type == ConfirmationType::Unset) {
        confirmation_type = ConfirmationType::LinuxHello;
      }
    }
    convar.notify_one();

    return status;
  });
  verify_task.activate();

  // This task waits for the password input (if the workaround wants it)
  optional_task<std::tuple<int, char *>> pass_task([&] {
    char *auth_tok_ptr = nullptr;
    int pam_res = pam_get_authtok(
        pamh, PAM_AUTHTOK, const_cast<const char **>(&auth_tok_ptr), nullptr);
    {
      std::unique_lock<std::mutex> lock(mutx);
      if (confirmation_type == ConfirmationType::Unset) {
        confirmation_type = ConfirmationType::Pam;
      }
    }
    convar.notify_one();

    return std::tuple<int, char *>(pam_res, auth_tok_ptr);
  });

  auto ask_pass = ask_auth_tok && !no_password && workaround != Workaround::Off;

  // We ask for the password if the function requires it and if a workaround is
  // set
  if (ask_pass) {
    pass_task.activate();
  }

  // Wait for the end either of the verification or the password input
  {
    std::unique_lock<std::mutex> lock(mutx);
    convar.wait(lock,
                [&] { return confirmation_type != ConfirmationType::Unset; });
  }

  // The password has been entered or an error has occurred
  if (confirmation_type == ConfirmationType::Pam) {
    // The service stops scanning once our connection is gone
    request.cancel();
    verify_task.stop(false);

    // We just wait for the thread to stop since it's this one which sent us the
    // confirmation type
    pass_task.stop(false);

    char *password = nullptr;
    std::tie(pam_res, password) = pass_task.get();

    if (pam_res != PAM_SUCCESS) {
      return pam_res;
    }

    // The password has been entered, we are passing it to PAM stack
    return PAM_IGNORE;
  }

  // The service has answered, or could not be reached
  verify_task.stop(false);
  auto status = verify_task.get();

  unsigned result = request.result();
  switch (status) {
  case VerifyRequest::Status::Answered:
    break;
  case VerifyRequest::Status::TimedOut:
    syslog(LOG_ERR, "The Linux Hello service did not answer in time");
    result = CompareError::TIMEOUT_REACHED;
    break;
  case VerifyRequest::Status::Unavailable:
    syslog(LOG_ERR, "The Linux Hello service is not available: %s",
           request.error().c_str());
    result = CompareError::ABORT;
    break;
  default:
    syslog(LOG_ERR, "Face verification failed: %s", request.error().c_str());
    result = CompareError::ABORT;
  }
  if (status == VerifyRequest::Status::Answered && request.certainty() >= 0) {
    syslog(LOG_DEBUG, "Best certainty %.2f", request.certainty());
  }

  // When the face was not verified and the user is being asked for a
  // password, let the password attempt proceed instead of failing
  if (ask_pass && result != 0) {
    // Wait for the password to be typed
    pass_task.stop(false);

    char *password = nullptr;
    std::tie(pam_res, password) = pass_task.get();

    if (pam_res != PAM_SUCCESS) {
      return linux_hello_status(username, result, config, conv_function);
    }

    // The password has been entered, we are passing it to PAM stack
    return PAM_IGNORE;
  }

  if (status == VerifyRequest::Status::Unavailable) {
    return PAM_AUTHINFO_UNAVAIL;
  }

  // We want to stop the password prompt, either by canceling the thread when
  // workaround is set to "native", or by emulating "Enter" input with
  // "input"

  // UNSAFE: We cancel the thread using pthread, pam_get_authtok seems to be
  // a cancellation point. Only when a prompt was started: without one there
  // is no thread to cancel and no prompt to send Enter to.
  if (!ask_pass) {
    // Nothing to stop
  } else if (workaround == Workaround::Native) {
    pass_task.stop(true);
  } else if (workaround == Workaround::Input) {
    // We check if we have the right permissions on /dev/uinput
    if (euidaccess("/dev/uinput", W_OK | R_OK) != 0) {
      syslog(LOG_WARNING, "Insufficient permissions to create the fake device");
      conv_function(PAM_ERROR_MSG,
                    S("Insufficient permissions to send Enter "
                      "press, waiting for user to press it instead"));
    } else {
      try {
        EnterDevice enter_device;
        int retries;

        // We try to send it
        enter_device.send_enter_press();

        for (retries = 0;
             retries < MAX_RETRIES &&
             pass_task.wait(DEFAULT_TIMEOUT) == std::future_status::timeout;
             retries++) {
          enter_device.send_enter_press();
        }

        if (retries == MAX_RETRIES) {
          syslog(LOG_WARNING,
                 "Failed to send enter input before the retries limit");
          conv_function(PAM_ERROR_MSG, S("Failed to send Enter press, waiting "
                                         "for user to press it instead"));
        }
      } catch (std::runtime_error &err) {
        syslog(LOG_WARNING, "Failed to send enter input: %s", err.what());
        conv_function(PAM_ERROR_MSG, S("Failed to send Enter press, waiting "
                                       "for user to press it instead"));
      }
    }

    // We stop the thread (will block until the enter key is pressed if the
    // input wasn't focused or if the uinput device failed to send keypress)
    pass_task.stop(false);
  }

  return linux_hello_status(username, result, config, conv_function);
}

// Called by PAM when a user needs to be authenticated, for example by running
// the sudo command
PAM_EXTERN auto pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  return identify(pamh, flags, argc, argv, true);
}

// Called by PAM when a session is started, such as by the su command
PAM_EXTERN auto pam_sm_open_session(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  return identify(pamh, flags, argc, argv, false);
}

// The functions below are required by PAM, but not needed in this module
PAM_EXTERN auto pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc,
                                 const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_close_session(pam_handle_t *pamh, int flags, int argc,
                                     const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_chauthtok(pam_handle_t *pamh, int flags, int argc,
                                 const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_setcred(pam_handle_t *pamh, int flags, int argc,
                               const char **argv) -> int {
  // Report success so display managers that call pam_setcred() strictly
  // (e.g. greetd, boltgolt/howdy#991) do not fail the session; this module
  // does not manage credentials itself, pam_unix establishes them.
  return PAM_SUCCESS;
}
