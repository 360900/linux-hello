#ifndef DAEMON_CLIENT_H_
#define DAEMON_CLIENT_H_

#include <atomic>
#include <string>

/**
 * One face verification by the Linux Hello service over the system bus
 * (io.github.LinuxHello1). The call runs on the thread that calls run();
 * cancel() may be called from any other thread and makes run() return
 * within a fraction of a second. Closing the connection on cancel tells
 * the service to stop scanning and release the camera.
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

  VerifyRequest(std::string user, unsigned timeout_seconds);

  auto run() -> Status;
  void cancel();

  /** The service's result code, the compare status codes in main.hh */
  [[nodiscard]] auto result() const -> unsigned { return result_; }
  /** Best distance seen times 10, -1 when no face was seen */
  [[nodiscard]] auto certainty() const -> double { return certainty_; }
  [[nodiscard]] auto error() const -> const std::string & { return error_; }

private:
  std::string user_;
  unsigned timeout_seconds_;
  std::atomic<bool> cancelled_{false};
  unsigned result_{0};
  double certainty_{-1};
  std::string error_;
};

#endif // DAEMON_CLIENT_H_
