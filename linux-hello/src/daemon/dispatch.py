# Minimal D-Bus object dispatcher on top of dbus-fast's low level API.
#
# dbus-fast's ServiceInterface does not tell a method who called it, and
# every method here needs the caller for access checks. So incoming calls
# are routed by hand: each method is an async function receiving the
# Caller and the arguments, and the reply is sent when it finishes.

import asyncio
import logging

from dbus_fast import Message, MessageType
from dbus_fast.errors import DBusError

log = logging.getLogger("linux-hello.dispatch")

ERROR_PREFIX = "io.github.LinuxHello1.Error."


class Failed(DBusError):
	"""Error with a stable D-Bus name: io.github.LinuxHello1.Error.<name>"""

	def __init__(self, name, text):
		super().__init__(ERROR_PREFIX + name, text)


class Caller:
	def __init__(self, sender, uid):
		self.sender = sender
		self.uid = uid

	def __repr__(self):
		return "Caller({}, uid={})".format(self.sender, self.uid)


class Dispatcher:
	"""
	Serves one interface on one object path. methods maps a member name to
	(input signature, output signature, coroutine function). The coroutine
	gets (caller, *args) and returns the output body as a list.
	"""

	def __init__(self, bus, path, interface, methods, introspection_xml, resolve_caller):
		self.bus = bus
		self.path = path
		self.interface = interface
		self.methods = methods
		self.introspection_xml = introspection_xml
		self.resolve_caller = resolve_caller
		self.tasks = set()
		self.on_activity = None

	def install(self):
		self.bus.add_message_handler(self._handle)

	def emit(self, member, signature, body, destination=None):
		"""Send a signal, unicast when destination is given"""
		self.bus.send(Message(
			message_type=MessageType.SIGNAL,
			path=self.path,
			interface=self.interface,
			member=member,
			signature=signature,
			body=body,
			destination=destination,
		))

	def busy(self):
		return bool(self.tasks)

	# -- incoming ----------------------------------------------------------
	def _handle(self, message):
		if message.message_type != MessageType.METHOD_CALL or message.path != self.path:
			return None

		if message.interface == "org.freedesktop.DBus.Introspectable" and message.member == "Introspect":
			return Message.new_method_return(message, "s", [self.introspection_xml])
		if message.interface == "org.freedesktop.DBus.Peer":
			return None

		if message.interface not in (self.interface, None):
			return None
		entry = self.methods.get(message.member)
		if entry is None:
			return Message.new_error(message, "org.freedesktop.DBus.Error.UnknownMethod",
				"No method {} on {}".format(message.member, self.interface))
		in_signature, _out, _function = entry
		if message.signature != in_signature:
			return Message.new_error(message, "org.freedesktop.DBus.Error.InvalidArgs",
				"Expected signature '{}', got '{}'".format(in_signature, message.signature))

		task = asyncio.get_running_loop().create_task(self._run(message, entry))
		self.tasks.add(task)
		task.add_done_callback(self._finished)
		return True

	def _finished(self, task):
		self.tasks.discard(task)
		if self.on_activity is not None:
			self.on_activity()

	async def _run(self, message, entry):
		_in, out_signature, function = entry
		if self.on_activity is not None:
			self.on_activity()
		try:
			caller = await self.resolve_caller(message.sender)
			body = await function(caller, *message.body)
			reply = Message.new_method_return(message, out_signature, body if body is not None else [])
		except DBusError as error:
			reply = Message.new_error(message, error.type, error.text)
		except Exception as error:  # noqa: BLE001 - reported to the caller
			log.exception("%s failed", message.member)
			reply = Message.new_error(message, ERROR_PREFIX + "Internal", str(error) or type(error).__name__)
		if message.flags.value & 1:
			# NO_REPLY_EXPECTED
			return
		self.bus.send(reply)
