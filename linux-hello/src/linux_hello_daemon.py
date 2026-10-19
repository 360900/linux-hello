# Linux Hello system service: owns the camera, the face models, the config
# and the PAM integration, and serves them on the system bus as
# io.github.LinuxHello1. Started on demand by D-Bus activation, exits again
# after a while without requests.
import sys as _sys
if __name__ == "__mp_main__":
	_sys.exit(0)

import argparse
import asyncio
import logging
import os
import signal
import sys

# Force single threaded BLAS: dlib's OpenBLAS/OpenMP matrix code can livelock
# when raced against other threads. Set LINUX_HELLO_BLAS_THREADS to opt out.
if "LINUX_HELLO_BLAS_THREADS" not in os.environ:
	os.environ["OPENBLAS_NUM_THREADS"] = "1"
	os.environ["OMP_NUM_THREADS"] = "1"
	os.environ["GOTO_NUM_THREADS"] = "1"

from dbus_fast import BusType, Message, MessageType, RequestNameReply
from dbus_fast.aio import MessageBus

from daemon.access import Access
from daemon.dispatch import Dispatcher
from daemon.manager import Manager

BUS_NAME = "io.github.LinuxHello1"
OBJECT_PATH = "/io/github/LinuxHello1"
INTERFACE = "io.github.LinuxHello1.Manager"
INTROSPECTION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon", "io.github.LinuxHello1.xml")

log = logging.getLogger("linux-hello")


async def serve(bus_type=BusType.SYSTEM, idle_timeout=600, bus_address=None, stop=None, ready=None,
		manager_factory=Manager):
	"""
	Serve until stop is set, SIGINT/SIGTERM arrive or the service was idle
	for idle_timeout seconds. The other arguments are for tests.
	"""
	if bus_address:
		bus = await MessageBus(bus_address=bus_address).connect()
	else:
		bus = await MessageBus(bus_type=bus_type).connect()
	access = Access(bus)

	dispatcher = Dispatcher(bus, OBJECT_PATH, INTERFACE, {}, _introspection(), access.resolve)
	manager = manager_factory(access, dispatcher.emit)
	dispatcher.methods = manager.methods()

	loop = asyncio.get_running_loop()
	last_activity = [loop.time()]

	def touch():
		last_activity[0] = loop.time()

	dispatcher.on_activity = touch
	dispatcher.install()

	# A caller that leaves the bus (Ctrl+C, closed window) cancels its
	# request, so an abandoned enrolment is never saved
	def caller_gone(message):
		if (message.message_type == MessageType.SIGNAL and message.member == "NameOwnerChanged"
				and message.sender == "org.freedesktop.DBus"):
			name, _old, new = message.body
			if not new and name in manager.running:
				log.info("%s left, cancelling its request", name)
				manager.running[name].set()

	bus.add_message_handler(caller_gone)
	await bus.call(Message(
		destination="org.freedesktop.DBus", path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
		member="AddMatch", signature="s",
		body=["type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg2=''"],
	))

	reply = await bus.request_name(BUS_NAME)
	if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
		log.error("Could not own %s (%s), is another instance running?", BUS_NAME, reply)
		return 1
	log.info("Serving %s", BUS_NAME)

	if ready is not None:
		ready.set()
	if stop is None:
		stop = asyncio.Event()
		for signum in (signal.SIGINT, signal.SIGTERM):
			loop.add_signal_handler(signum, stop.set)

	while not stop.is_set():
		try:
			await asyncio.wait_for(stop.wait(), timeout=15)
		except asyncio.TimeoutError:
			pass
		idle = loop.time() - last_activity[0]
		if idle_timeout and idle > idle_timeout and not dispatcher.busy():
			log.info("Idle for %ds, exiting", idle)
			break

	# Let running requests finish (they release the camera themselves)
	for event in list(manager.running.values()):
		event.set()
	if dispatcher.tasks:
		await asyncio.wait(dispatcher.tasks, timeout=10)
	await bus.release_name(BUS_NAME)
	bus.disconnect()
	return 0


def _introspection():
	try:
		with open(INTROSPECTION) as file:
			return file.read()
	except OSError:
		return "<node/>"


def main():
	parser = argparse.ArgumentParser(prog="linux-hello-daemon")
	parser.add_argument("--session", action="store_true", help="use the session bus (development)")
	parser.add_argument("--idle-timeout", type=int, default=600, help="exit after this many idle seconds, 0 never")
	parser.add_argument("--verbose", action="store_true")
	args = parser.parse_args()

	logging.basicConfig(
		level=logging.DEBUG if args.verbose else logging.INFO,
		format="%(name)s: %(message)s",
		stream=sys.stderr,
	)
	bus_type = BusType.SESSION if args.session else BusType.SYSTEM
	sys.exit(asyncio.run(serve(bus_type, args.idle_timeout)))


if __name__ == "__main__":
	main()
