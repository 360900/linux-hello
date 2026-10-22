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

namespace {

// Logging in waits for these, so not long
const uint64_t SECRET_TIMEOUT_USEC = 10 * 1000 * 1000;

} // namespace

auto remember_login_secret(const std::string &user, const char *secret)
    -> bool {
  sd_bus *bus = nullptr;
  if (sd_bus_open_system(&bus) < 0) {
    return false;
  }
  sd_bus_set_method_call_timeout(bus, SECRET_TIMEOUT_USEC);
  sd_bus_message *message = nullptr;
  int r = sd_bus_message_new_method_call(bus, &message, BUS_NAME, OBJECT_PATH,
                                         INTERFACE, "RememberLoginSecret");
  if (r >= 0) {
    // Wiped when freed: it holds a copy of the password
    sd_bus_message_sensitive(message);
    r = sd_bus_message_append(message, "s", user.c_str());
  }
  if (r >= 0) {
    r = sd_bus_message_append_array(message, 'y', secret, strlen(secret));
  }
  if (r >= 0) {
    sd_bus_error error = SD_BUS_ERROR_NULL;
    r = sd_bus_call(bus, message, 0, &error, nullptr);
    sd_bus_error_free(&error);
  }
  sd_bus_message_unref(message);
  sd_bus_flush_close_unref(bus);
  return r >= 0;
}

void login_secret(const std::string &user, std::string &secret) {
  sd_bus *bus = nullptr;
  if (sd_bus_open_system(&bus) < 0) {
    return;
  }
  sd_bus_set_method_call_timeout(bus, SECRET_TIMEOUT_USEC);
  sd_bus_error error = SD_BUS_ERROR_NULL;
  sd_bus_message *reply = nullptr;
  int r = sd_bus_call_method(bus, BUS_NAME, OBJECT_PATH, INTERFACE,
                             "LoginSecret", &error, &reply, "s", user.c_str());
  if (r >= 0) {
    // Wiped when freed: it holds the password
    sd_bus_message_sensitive(reply);
    const void *data = nullptr;
    size_t size = 0;
    if (sd_bus_message_read_array(reply, 'y', &data, &size) >= 0 &&
        size > 0) {
      secret.assign(static_cast<const char *>(data), size);
    }
  }
  sd_bus_error_free(&error);
  sd_bus_message_unref(reply);
  sd_bus_flush_close_unref(bus);
}
