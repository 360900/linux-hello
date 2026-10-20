# Internal face verification API
# Based on PR #1125 by qilsklo (https://github.com/boltgolt/howdy/pull/1125)
# Asks the Linux Hello service (io.github.LinuxHello1) to verify a face
# with VerifyNow: the same scan the PAM module gets, but started by the
# user, so the lock screen's pause never applies. Passkeys use
# VerifyConfirmed instead: any program can ask the virtual security key for
# a sign-in, so the scan waits for Enter like sudo does.

import asyncio
import enum
import threading


class VerificationResult(enum.IntEnum):
	"""Result codes of the service's Verify method, mirrors CompareError in pam/main.hh"""
	SUCCESS = 0
	NO_FACE_MODEL = 10
	TIMEOUT_REACHED = 11
	ABORT = 12
	TOO_DARK = 13
	INVALID_DEVICE = 14
	RUBBERSTAMP = 15
	# VerifyConfirmed: no Enter in time or typing, of which 3 within a
	# minute pause face for a while; then paused; or skipped with Esc
	NOT_CONFIRMED = 16
	CONFIRM_PAUSED = 17
	SKIPPED = 18
	# Asked by another user or from outside a local session
	NOT_LOCAL = 19
	# Another confirmed verification overlapped this one
	CONFIRM_CONFLICT = 20
	CANCELLED = 250
	# Not produced by the service: it could not be asked or did not answer
	UNKNOWN_ERROR = 251

	@classmethod
	def from_exit_code(cls, code):
		"""Map a result code to a VerificationResult"""
		try:
			return cls(code)
		except ValueError:
			return cls.UNKNOWN_ERROR


class FaceVerifier:
	"""Runs Linux Hello face verification for a user through the service

	A verifier instance runs at most one verification at a time and can be
	cancelled from another thread (used by the CTAPHID CANCEL command).
	Cancelling drops the bus connection, which makes the service stop
	scanning and release the camera.
	"""

	BUS_NAME = "io.github.LinuxHello1"
	OBJECT_PATH = "/io/github/LinuxHello1"
	INTERFACE = "io.github.LinuxHello1.Manager"

	def __init__(self, bus_address=None):
		self.bus_address = bus_address
		self._lock = threading.Lock()
		self._running = None

	def verify(self, user, timeout=None, confirmed=False):
		"""Verify the face of the given user, blocking until a result is known

		timeout is a hard deadline in seconds on top of the scan timeout the
		service applies from [video] timeout in the config. confirmed waits
		for Enter on a physical keyboard first (root callers only).
		"""
		if not user or user == "root":
			return VerificationResult.ABORT

		with self._lock:
			if self._running is not None:
				return VerificationResult.ABORT
			loop = asyncio.new_event_loop()
			self._running = {"loop": loop, "bus": None, "cancelled": False}
			state = self._running

		try:
			return loop.run_until_complete(self._verify(user, timeout, state, confirmed))
		finally:
			loop.close()
			with self._lock:
				self._running = None

	async def _verify(self, user, timeout, state, confirmed):
		from dbus_fast import BusType, Message, MessageType
		from dbus_fast.aio import MessageBus

		try:
			if self.bus_address:
				bus = await MessageBus(bus_address=self.bus_address).connect()
			else:
				bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
		except (OSError, ValueError):
			return VerificationResult.UNKNOWN_ERROR
		state["bus"] = bus
		if state["cancelled"]:
			bus.disconnect()
			return VerificationResult.CANCELLED

		try:
			reply = await asyncio.wait_for(bus.call(Message(
				destination=self.BUS_NAME,
				path=self.OBJECT_PATH,
				interface=self.INTERFACE,
				member="VerifyConfirmed" if confirmed else "VerifyNow",
				# A passkey request comes from the browser: who asked is unknown
				signature="su" if confirmed else "s",
				body=[user, 0] if confirmed else [user],
			)), timeout)
		except asyncio.TimeoutError:
			return VerificationResult.TIMEOUT_REACHED
		except Exception:  # noqa: BLE001 - the connection was dropped by cancel()
			return VerificationResult.CANCELLED if state["cancelled"] else VerificationResult.UNKNOWN_ERROR
		finally:
			bus.disconnect()

		if state["cancelled"]:
			return VerificationResult.CANCELLED
		if reply.message_type == MessageType.ERROR:
			return VerificationResult.UNKNOWN_ERROR
		return VerificationResult.from_exit_code(reply.body[0])

	def cancel(self):
		"""Abort a running verification from another thread"""
		with self._lock:
			state = self._running
			if state is None:
				return
			state["cancelled"] = True
			bus = state["bus"]
			loop = state["loop"]
		if bus is not None:
			try:
				loop.call_soon_threadsafe(bus.disconnect)
			except RuntimeError:
				# The verification ended in the meantime
				pass


class BoundVerifier:
	"""
	A FaceVerifier fixed to one user, the interface the authenticator
	expects. Confirmed with Enter, as a sign-in with a passkey can be asked
	for by any program.
	"""

	def __init__(self, user, bus_address=None):
		self.user = user
		self._verifier = FaceVerifier(bus_address=bus_address)

	def verify(self, timeout=None):
		return self._verifier.verify(self.user, timeout=timeout, confirmed=True)

	def cancel(self):
		self._verifier.cancel()


def verify_face(user, timeout=None):
	"""Verify the face of the given user, returns a VerificationResult"""
	return FaceVerifier().verify(user, timeout=timeout)
