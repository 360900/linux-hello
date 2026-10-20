#include "daemon_client.hh"

#include <cerrno>
#include <cstring>
#include <utility>

#include <systemd/sd-bus.h>

namespace {

const char *const BUS_NAME = "io.github.LinuxHello1";
const char *const OBJECT_PATH = "/io/github/LinuxHello1";
const char *const INTERFACE = "io.github.LinuxHello1.Manager";

// How often the waiting loop looks at the cancel flag
const uint64_t POLL_USEC = 100 * 1000;

struct Reply {
  bool done{false};
  int error_errno{0};
  std::string error_name;
  std::string error_message;
  unsigned result{0};
  double certainty{-1};
};

auto on_reply(sd_bus_message *message, void *userdata,
              sd_bus_error * /*ret_error*/) -> int {
  auto *reply = static_cast<Reply *>(userdata);
  reply->done = true;

  if (sd_bus_message_is_method_error(message, nullptr) != 0) {
    const sd_bus_error *error = sd_bus_message_get_error(message);
    reply->error_errno = sd_bus_message_get_errno(message);
    reply->error_name = error->name != nullptr ? error->name : "";
    reply->error_message = error->message != nullptr ? error->message : "";
    return 0;
  }

  uint32_t result = 0;
  double certainty = -1;
  int r = sd_bus_message_read(message, "ud", &result, &certainty);
  if (r < 0) {
    reply->error_errno = -r;
    reply->error_name = "invalid-reply";
    reply->error_message = std::strerror(-r);
    return 0;
  }
  reply->result = result;
  reply->certainty = certainty;
  return 0;
}

auto on_window_signal(sd_bus_message * /*message*/, void *userdata,
                      sd_bus_error * /*ret_error*/) -> int {
  auto *callback = static_cast<std::function<void()> *>(userdata);
  if (*callback) {
    (*callback)();
  }
  return 0;
}

auto unavailable(const std::string &name) -> bool {
  return name == SD_BUS_ERROR_SERVICE_UNKNOWN ||
         name == SD_BUS_ERROR_NAME_HAS_NO_OWNER ||
         name == "org.freedesktop.DBus.Error.Spawn.ChildExited" ||
         name == "org.freedesktop.DBus.Error.Spawn.ExecFailed" ||
         name == "org.freedesktop.systemd1.UnitFailed" ||
         name == "io.github.LinuxHello1.Error.PermissionDenied";
}

} // namespace

VerifyRequest::VerifyRequest(std::string user, unsigned timeout_seconds,
                             std::string method, uint32_t requester)
    : user_(std::move(user)), method_(std::move(method)),
      requester_(requester), timeout_seconds_(timeout_seconds) {}

void VerifyRequest::cancel() { cancelled_ = true; }

auto VerifyRequest::run() -> Status {
  sd_bus *bus = nullptr;
  int r = sd_bus_open_system(&bus);
  if (r < 0) {
    error_ = std::string("cannot connect to the system bus: ") +
             std::strerror(-r);
    return Status::Unavailable;
  }

  sd_bus_set_method_call_timeout(bus, uint64_t{timeout_seconds_} * 1000000);

  // Sent only to this connection, before the reply
  sd_bus_slot *window_slot = nullptr;
  if (on_window_) {
    r = sd_bus_match_signal(bus, &window_slot, BUS_NAME, OBJECT_PATH,
                            INTERFACE, "ConfirmWindow", on_window_signal,
                            &on_window_);
    if (r < 0) {
      error_ = std::string("cannot watch for the confirmation window: ") +
               std::strerror(-r);
      sd_bus_flush_close_unref(bus);
      return Status::Failed;
    }
  }

  Reply reply;
  sd_bus_slot *slot = nullptr;
  if (method_ == "VerifyConfirmed") {
    r = sd_bus_call_method_async(bus, &slot, BUS_NAME, OBJECT_PATH, INTERFACE,
                                 method_.c_str(), on_reply, &reply, "su",
                                 user_.c_str(), requester_);
  } else {
    r = sd_bus_call_method_async(bus, &slot, BUS_NAME, OBJECT_PATH, INTERFACE,
                                 method_.c_str(), on_reply, &reply, "s",
                                 user_.c_str());
  }
  if (r < 0) {
    error_ = std::string("cannot send the request: ") + std::strerror(-r);
    sd_bus_slot_unref(window_slot);
    sd_bus_flush_close_unref(bus);
    return Status::Failed;
  }

  while (!reply.done) {
    if (cancelled_) {
      // Closing the connection makes the service cancel the scan
      sd_bus_slot_unref(slot);
      sd_bus_slot_unref(window_slot);
      sd_bus_flush_close_unref(bus);
      return Status::Cancelled;
    }
    r = sd_bus_process(bus, nullptr);
    if (r < 0) {
      error_ = std::string("bus error: ") + std::strerror(-r);
      sd_bus_slot_unref(slot);
      sd_bus_slot_unref(window_slot);
      sd_bus_flush_close_unref(bus);
      return Status::Failed;
    }
    if (r > 0) {
      continue;
    }
    r = sd_bus_wait(bus, POLL_USEC);
    if (r < 0 && r != -EINTR) {
      error_ = std::string("bus error: ") + std::strerror(-r);
      sd_bus_slot_unref(slot);
      sd_bus_slot_unref(window_slot);
      sd_bus_flush_close_unref(bus);
      return Status::Failed;
    }
  }

  sd_bus_slot_unref(slot);
  sd_bus_slot_unref(window_slot);
  sd_bus_flush_close_unref(bus);

  if (!reply.error_name.empty()) {
    error_ = reply.error_name + ": " + reply.error_message;
    if (reply.error_name == SD_BUS_ERROR_NO_REPLY ||
        reply.error_name == SD_BUS_ERROR_TIMEOUT ||
        reply.error_errno == ETIMEDOUT) {
      return Status::TimedOut;
    }
    if (unavailable(reply.error_name)) {
      return Status::Unavailable;
    }
    return Status::Failed;
  }

  result_ = reply.result;
  certainty_ = reply.certainty;
  return Status::Answered;
}
