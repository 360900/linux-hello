# Blocking client for the io.github.LinuxHello1 system service, used by the
# CLI. Each call opens its own connection; the CLI makes only a few.

import asyncio
import os
import shutil
import subprocess
import sys

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

BUS_NAME = "io.github.LinuxHello1"
OBJECT_PATH = "/io/github/LinuxHello1"
INTERFACE = "io.github.LinuxHello1.Manager"

# How long an interactive call may take: polkit prompts wait for a human
CALL_TIMEOUT = 300


class ServiceError(Exception):
	def __init__(self, name, text):
		super().__init__(text)
		self.name = name

	@property
	def short_name(self):
		return self.name.rsplit(".", 1)[-1]


class Client:
	def __init__(self, bus_address=None):
		self.bus_address = bus_address or os.environ.get("LINUX_HELLO_BUS_ADDRESS")
		self._agent = None

	def call(self, member, signature="", body=(), on_signal=None):
		"""
		Call a method and return its body. on_signal(member, body) receives
		the service's signals while the call runs.
		"""
		return asyncio.run(self._call(member, signature, list(body), on_signal))

	async def _call(self, member, signature, body, on_signal):
		try:
			if self.bus_address:
				bus = await MessageBus(bus_address=self.bus_address).connect()
			else:
				bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
		except (OSError, ValueError) as error:
			raise ServiceError("Unavailable", "Cannot reach the system bus: {}".format(error))

		if on_signal is not None:
			def handler(message):
				if message.message_type == MessageType.SIGNAL and message.interface == INTERFACE:
					on_signal(message.member, message.body)
			bus.add_message_handler(handler)

		try:
			reply = await asyncio.wait_for(bus.call(Message(
				destination=BUS_NAME,
				path=OBJECT_PATH,
				interface=INTERFACE,
				member=member,
				signature=signature,
				body=body,
			)), CALL_TIMEOUT)
		finally:
			bus.disconnect()

		if reply.message_type == MessageType.ERROR:
			text = reply.body[0] if reply.body else reply.error_name
			if reply.error_name in ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner"):
				raise ServiceError("Unavailable", "The Linux Hello service is not installed or failed to start (journalctl -u linux-hello-daemon)")
			raise ServiceError(reply.error_name, text)
		return reply.body

	# -- polkit in a terminal ------------------------------------------------
	def start_tty_agent(self):
		"""
		Let polkit ask for a password in this terminal when no desktop agent
		is running (SSH, a TTY). A desktop agent keeps priority.
		"""
		if self._agent is not None or os.geteuid() == 0 or not sys.stdin.isatty():
			return
		agent = shutil.which("pkttyagent")
		if agent is None:
			return
		read_end, write_end = os.pipe()
		try:
			self._agent = subprocess.Popen(
				[agent, "--process", str(os.getpid()), "--fallback", "--notify-fd", str(write_end)],
				pass_fds=(write_end,),
			)
		except OSError:
			os.close(read_end)
			os.close(write_end)
			return
		os.close(write_end)
		# The agent closes the fd once it is registered
		os.read(read_end, 1)
		os.close(read_end)

	def stop_tty_agent(self):
		if self._agent is not None:
			self._agent.terminate()
			self._agent.wait()
			self._agent = None
