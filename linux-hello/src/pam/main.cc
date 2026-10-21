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
#include <fcntl.h>
#include <sys/socket.h>
#include <termios.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cmath>
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

// Services that unlock or start the user's own session. Every other service
// (sudo, su, polkit, run0, anything unknown) grants more than that, so a
// face only counts there after a physical Enter press, see needs_confirmation
// "login" is not one: telnet and rlogin use it too.
const std::array<const char *, 14> SESSION_SERVICES = {
    "kde",          "kde-fingerprint", "kde-face",     "kscreensaver",
    "plasmalogin",  "sddm",            "gdm-password", "lightdm",
    "greetd",       "xscreensaver",    "swaylock",     "hyprlock",
    "i3lock",       "sddm-greeter"};

// Login screens that take the password in their own form and start PAM only
// on submit, answering password prompts with what was typed. A typed
// password is used as is, so it is checked and unlocks KWallet; an empty
// form means the user wants their face.
const std::array<const char *, 2> PASSWORD_FIRST_SERVICES = {"plasmalogin",
                                                             "sddm"};

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
    std::string identify_msg(S("Identified face as {}"));
    // A translation without the placeholder must not throw inside sudo
    auto at = identify_msg.find("{}");
    if (at != std::string::npos) {
      identify_msg.replace(at, 2, username);
    }
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
 * Seconds the service may take: the scan timeout plus time for a cold start
 * (loading the recognition models) and for opening the camera
 */
auto service_timeout(const LayeredConfig &config) -> unsigned {
  double scan_timeout = 4;
  try {
    scan_timeout = std::stod(config.GetString("video", "timeout", "4"));
  } catch (const std::exception &) {
  }
  return static_cast<unsigned>(scan_timeout) + 30;
}

/**
 * Whether a face may only be used after Enter on a physical keyboard.
 *
 * A face match shows that someone is in front of the camera, not that they
 * want what is asked. Any program running as the user can start sudo in a
 * pseudo-terminal or replace the polkit agent and answer the prompt itself,
 * so for everything that elevates the service also waits for a key press no
 * such program can produce (daemon/confirm.py).
 */
auto needs_confirmation(pam_handle_t *pamh, const LayeredConfig &config,
                        bool no_password) -> bool {
  // A stack without a password prompt only unlocks the lock screen
  if (no_password || !config.GetBoolean("core", "confirm_elevation", true)) {
    return false;
  }
  const void *service = nullptr;
  if (pam_get_item(pamh, PAM_SERVICE, &service) != PAM_SUCCESS ||
      service == nullptr) {
    return true;
  }
  for (const char *session : SESSION_SERVICES) {
    if (strcmp(session, static_cast<const char *>(service)) == 0) {
      return false;
    }
  }
  return true;
}

auto takes_password_first(pam_handle_t *pamh) -> bool {
  const void *service = nullptr;
  if (pam_get_item(pamh, PAM_SERVICE, &service) != PAM_SUCCESS ||
      service == nullptr) {
    return false;
  }
  return std::any_of(PASSWORD_FIRST_SERVICES.begin(),
                     PASSWORD_FIRST_SERVICES.end(), [&](const char *name) {
                       return strcmp(name, static_cast<const char *>(service)) ==
                              0;
                     });
}

/**
 * The process that asked, for the service to check whose and from where:
 * sudo or su itself, or for polkit the authentication agent. polkit's helper
 * is started by systemd with the agent on its stdin socket, or older ones by
 * the agent directly. 0 when it cannot be told.
 */
auto requester_pid(pam_handle_t *pamh) -> pid_t {
  const void *service = nullptr;
  if (pam_get_item(pamh, PAM_SERVICE, &service) != PAM_SUCCESS ||
      service == nullptr) {
    return 0;
  }
  if (strcmp(static_cast<const char *>(service), "polkit-1") != 0) {
    return getpid();
  }
  if (getppid() != 1) {
    return getppid();
  }
  ucred peer{};
  socklen_t length = sizeof(peer);
  if (getsockopt(STDIN_FILENO, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0) {
    return 0;
  }
  return peer.pid;
}

/**
 * Keeps keys pressed during the face window off the screen and out of the
 * next prompt, like sudo does for its own: echo off while the window runs,
 * unread input dropped after it. Only for a terminal (sudo, su); polkit
 * dialogs have none.
 */
class QuietTerminal {
  static constexpr useconds_t TERMINAL_CATCH_UP_USEC = 200 * 1000;

public:
  explicit QuietTerminal(pam_handle_t *pamh) {
    const void *item = nullptr;
    if (pam_get_item(pamh, PAM_TTY, &item) != PAM_SUCCESS || item == nullptr) {
      return;
    }
    std::string tty(static_cast<const char *>(item));
    if (tty.rfind("/dev/", 0) != 0) {
      tty = "/dev/" + tty;
    }
    fd_ = open(tty.c_str(), O_RDWR | O_NOCTTY | O_CLOEXEC);
    if (fd_ >= 0 && (isatty(fd_) == 0 || tcgetattr(fd_, &saved_) != 0)) {
      close(fd_);
      fd_ = -1;
    }
  }
  QuietTerminal(const QuietTerminal &) = delete;
  auto operator=(const QuietTerminal &) -> QuietTerminal & = delete;

  void start() {
    if (fd_ < 0 || started_) {
      return;
    }
    termios quiet = saved_;
    quiet.c_lflag &= ~(ECHO | ECHONL);
    started_ = tcsetattr(fd_, TCSANOW, &quiet) == 0;
  }

  [[nodiscard]] auto is_terminal() const -> bool { return fd_ >= 0; }

  ~QuietTerminal() {
    if (fd_ < 0) {
      return;
    }
    if (started_) {
      // The service sees a key on the keyboard before the terminal app
      // passes it on; let it arrive so it is dropped, not echoed later
      usleep(TERMINAL_CATCH_UP_USEC);
      tcflush(fd_, TCIFLUSH);
      tcsetattr(fd_, TCSANOW, &saved_);
    }
    close(fd_);
  }

private:
  int fd_{-1};
  bool started_{false};
  termios saved_{};
};

/**
 * Admin prompts and sudo: for a few seconds the service waits for Enter on a
 * physical keyboard and only then scans. Nothing waits for typed input, so
 * without Enter the stack goes on as if this module were not there.
 */
auto identify_confirmed(pam_handle_t *pamh, char *username,
                        const LayeredConfig &config,
                        const std::function<int(int, const char *)> &conv_function)
    -> int {
  QuietTerminal terminal(pamh);
  // Esc would close a polkit dialog, so it is only offered in a terminal
  const char *hint = terminal.is_terminal()
                         ? S("Press Enter to use your face, or Esc to skip")
                         : S("Press Enter to use your face");

  pid_t requester = requester_pid(pamh);
  if (requester <= 0) {
    syslog(LOG_NOTICE, "Face not offered, cannot tell who asked");
    return PAM_IGNORE;
  }

  // The service waits up to 3 seconds for Enter before scanning
  VerifyRequest request(username, service_timeout(config) + 5,
                        "VerifyConfirmed", static_cast<uint32_t>(requester));
  request.on_window([&] {
    terminal.start();
    conv_function(PAM_TEXT_INFO, hint);
  });
  auto status = request.run();
  unsigned result = request.result();

  if (status != VerifyRequest::Status::Answered) {
    syslog(LOG_ERR, "Confirmed face verification failed: %s",
           request.error().c_str());
    return PAM_AUTHINFO_UNAVAIL;
  }
  switch (result) {
  case 0:
    return linux_hello_status(username, result, config, conv_function);
  case CompareError::SKIPPED:
    syslog(LOG_INFO, "Face skipped with Esc");
    return PAM_IGNORE;
  case CompareError::NOT_LOCAL:
    syslog(LOG_NOTICE, "Face not offered, asked from outside a local session "
                       "or by another user");
    return PAM_IGNORE;
  case CompareError::CONFIRM_CONFLICT:
    syslog(LOG_WARNING, "Face not used, another request asked at the same time");
    conv_function(PAM_ERROR_MSG,
                  S("Face not used: something else asked for it at the same "
                    "time"));
    return PAM_IGNORE;
  case CompareError::NOT_CONFIRMED:
    syslog(LOG_NOTICE, "Face not used, Enter was not pressed alone in time");
    return PAM_IGNORE;
  case CompareError::CONFIRM_PAUSED:
    syslog(LOG_NOTICE,
           "Face not offered, paused after requests without Enter");
    {
      // The service sends the seconds left in place of a certainty
      auto minutes = static_cast<unsigned long>(
          std::ceil(std::max(request.certainty(), 1.0) / 60));
      std::string paused(ngettext("Face is paused for %lu minute for security",
                                  "Face is paused for %lu minutes for security",
                                  minutes));
      auto at = paused.find("%lu");
      if (at != std::string::npos) {
        paused.replace(at, 3, std::to_string(minutes));
      }
      conv_function(PAM_TEXT_INFO, paused.c_str());
    }
    return PAM_IGNORE;
  default:
    return linux_hello_error(result, conv_function);
  }
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

  bool password_first = ask_auth_tok && takes_password_first(pamh);
  if (password_first) {
    const char *typed = nullptr;
    if (pam_get_authtok(pamh, PAM_AUTHTOK, &typed, nullptr) != PAM_SUCCESS) {
      return PAM_IGNORE;
    }
    if (typed != nullptr && typed[0] != '\0') {
      return PAM_IGNORE;
    }
  }

  if (needs_confirmation(pamh, config, no_password)) {
    // Not as a session module: there is no prompt to confirm with
    if (!ask_auth_tok) {
      return PAM_IGNORE;
    }
    return identify_confirmed(pamh, username, config, conv_function);
  }

  if (config.GetBoolean("core", "detection_notice", true)) {
    if ((conv_function(PAM_TEXT_INFO, S("Attempting facial authentication"))) !=
        PAM_SUCCESS) {
      syslog(LOG_ERR, "Failed to send detection notice");
    }
  }

  VerifyRequest request(username, service_timeout(config));

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

  // The form was already read above and left empty
  auto ask_pass = ask_auth_tok && !no_password && !password_first &&
                  workaround != Workaround::Off;

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
auto has_argument(int argc, const char **argv, const char *wanted) -> bool {
  for (int i = 0; i < argc; i++) {
    if (strcmp(argv[i], wanted) == 0) {
      return true;
    }
  }
  return false;
}

auto user_of(pam_handle_t *pamh) -> std::string {
  const char *user = nullptr;
  if (pam_get_user(pamh, &user, nullptr) != PAM_SUCCESS || user == nullptr) {
    return {};
  }
  return user;
}

/**
 * "kwallet", in the autologin stack in front of pam_kwallet5: no password
 * was typed, so hand pam_kwallet5 the one the service kept from the last
 * login with it, and KWallet opens as usual. Never decides anything.
 */
auto give_kwallet_password(pam_handle_t *pamh) -> int {
  std::string user = user_of(pamh);
  if (user.empty() || user == "root") {
    return PAM_IGNORE;
  }
  std::string secret = login_secret(user);
  if (!secret.empty()) {
    if (pam_set_item(pamh, PAM_AUTHTOK, secret.c_str()) != PAM_SUCCESS) {
      syslog(LOG_WARNING, "Could not hand the kept password to KWallet");
    }
    explicit_bzero(secret.data(), secret.size());
  }
  return PAM_IGNORE;
}

// PAM forgets the password once authentication ends, so the auth line
// holds on to it until a session opens, like pam_kwallet5 does
const char *const PASSWORD_DATA = "linux_hello_password";

void forget_password(pam_handle_t * /*pamh*/, void *data, int /*status*/) {
  auto *password = static_cast<char *>(data);
  explicit_bzero(password, strlen(password));
  free(password);
}

/**
 * "remember_password", last in the login screen's auth stack: hold on to
 * the typed password. Never decides anything.
 */
auto hold_password(pam_handle_t *pamh) -> int {
  const void *item = nullptr;
  if (pam_get_item(pamh, PAM_AUTHTOK, &item) != PAM_SUCCESS ||
      item == nullptr || static_cast<const char *>(item)[0] == '\0') {
    return PAM_IGNORE;
  }
  char *copy = strdup(static_cast<const char *>(item));
  if (copy != nullptr &&
      pam_set_data(pamh, PASSWORD_DATA, copy, forget_password) !=
          PAM_SUCCESS) {
    forget_password(pamh, copy, 0);
  }
  return PAM_IGNORE;
}

/**
 * "remember_password" in the session stack: a session only opens after the
 * password was checked, so hand it to the service for KWallet. The service
 * drops it unless auto-login is on for this user.
 */
auto remember_password(pam_handle_t *pamh) -> int {
  std::string user = user_of(pamh);
  const void *data = nullptr;
  if (user.empty() || user == "root" ||
      pam_get_data(pamh, PASSWORD_DATA, &data) != PAM_SUCCESS ||
      data == nullptr) {
    return PAM_IGNORE;
  }
  std::string secret(static_cast<const char *>(data));
  if (!remember_login_secret(user, secret)) {
    syslog(LOG_WARNING, "Could not keep the password for KWallet");
  }
  explicit_bzero(secret.data(), secret.size());
  // Not needed any more
  pam_set_data(pamh, PASSWORD_DATA, nullptr, nullptr);
  return PAM_IGNORE;
}

PAM_EXTERN auto pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  if (has_argument(argc, argv, "kwallet")) {
    return give_kwallet_password(pamh);
  }
  if (has_argument(argc, argv, "remember_password")) {
    return hold_password(pamh);
  }
  return identify(pamh, flags, argc, argv, true);
}

// Called by PAM when a session is started, such as by the su command
PAM_EXTERN auto pam_sm_open_session(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  if (has_argument(argc, argv, "remember_password")) {
    return remember_password(pamh);
  }
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
