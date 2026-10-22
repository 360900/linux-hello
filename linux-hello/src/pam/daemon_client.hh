#ifndef DAEMON_CLIENT_H_
#define DAEMON_CLIENT_H_

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>

/**
 * One face verification by the Linux Hello service over the system bus
 * (io.github.LinuxHello1). The call runs on the thread that calls run();
 * cancel() may be called from any other thread and makes run() return
 * within a fraction of a second. Closing the connection on cancel tells
 * the service to stop scanning and release the camera.
 *
 * method is Verify, or VerifyConfirmed for admin prompts and sudo with the
 * process that asked as requester. For the latter, on_window runs (on the
 * calling thread) when the service starts waiting for Enter.
 */
class VerifyRequest {
public:
  /** Outcome of run() */
  enum class Status {
    Answered,    // the service replied, see result() and certainty()
    Unavailable, // the service is not installed, not running or refused
    TimedOut,    // no reply in time
    Cancelled,   // cancel() was called
    Failed       // anything else, see error()
  };

  VerifyRequest(std::string user, unsigned timeout_seconds,
                std::string method = "Verify", uint32_t requester = 0);

  auto run() -> Status;
  void cancel();
  void on_window(std::function<void()> callback) {
    on_window_ = std::move(callback);
  }

  /** The service's result code, the compare status codes in main.hh */
  [[nodiscard]] auto result() const -> unsigned { return result_; }
  /** Best distance seen times 10, -1 when no face was seen */
  [[nodiscard]] auto certainty() const -> double { return certainty_; }
  [[nodiscard]] auto error() const -> const std::string & { return error_; }

private:
  std::string user_;
  std::string method_;
  uint32_t requester_;
  std::function<void()> on_window_;
  unsigned timeout_seconds_;
  std::atomic<bool> cancelled_{false};
  unsigned result_{0};
  double certainty_{-1};
  std::string error_;
};

/**
 * KWallet after logging in automatically: the password typed at a login is
 * handed to the service when the session opens, and asked back when
 * logging in automatically. Root only; the service keeps it only while
 * auto-login is on for user. login_secret leaves secret empty when there is
 * none or the service cannot be reached; it fills the caller's string, so
 * no copy is left behind to wipe. Messages are wiped when freed.
 */
auto remember_login_secret(const std::string &user, const char *secret)
    -> bool;
void login_secret(const std::string &user, std::string &secret);

#endif // DAEMON_CLIENT_H_
