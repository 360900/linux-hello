# Internal face verification API
# Based on PR #1125 by qilsklo (https://github.com/boltgolt/howdy/pull/1125)
# Wraps the compare.py subprocess contract also used by the PAM module,
# so callers get behaviour identical to PAM authentication

import enum
import os
import subprocess
import sys
import threading


class VerificationResult(enum.IntEnum):
	"""Exit status codes of the compare process, mirrors CompareError in pam/main.hh"""
	SUCCESS = 0
	NO_FACE_MODEL = 10
	TIMEOUT_REACHED = 11
	ABORT = 12
	TOO_DARK = 13
	INVALID_DEVICE = 14
	RUBBERSTAMP = 15
	# Not produced by compare.py itself: the caller cancelled or hard-killed it
	CANCELLED = 250
	UNKNOWN_ERROR = 251

	@classmethod
	def from_exit_code(cls, code):
		"""Map a compare.py exit status to a VerificationResult"""
		try:
			return cls(code)
		except ValueError:
			return cls.UNKNOWN_ERROR


class FaceVerifier:
	"""Runs Linux Hello face verification for a user by spawning the compare script

	The compare script only communicates through its exit code, which keeps
	this wrapper equivalent to the PAM module and free of side effects on it.
	A verifier instance runs at most one verification at a time and can be
	cancelled from another thread (used by the CTAPHID CANCEL command).
	"""

	def __init__(self, compare_script=None, python_executable=None):
		self.compare_script = compare_script or os.path.join(os.path.dirname(os.path.abspath(__file__)), "compare.py")
		self.python_executable = python_executable or sys.executable
		self._process = None
		self._lock = threading.Lock()
		self._cancelled = False

	def verify(self, user, timeout=None):
		"""Verify the face of the given user, blocking until a result is known

		timeout is a hard kill deadline in seconds on top of the internal
		timeout compare.py already applies from [video] timeout in the config.
		"""
		if not user or user == "root":
			return VerificationResult.ABORT

		with self._lock:
			if self._process is not None:
				# A verification is already running on this instance
				return VerificationResult.ABORT
			self._cancelled = False
			try:
				self._process = subprocess.Popen(
					[self.python_executable, self.compare_script, user],
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL)
			except OSError:
				self._process = None
				return VerificationResult.UNKNOWN_ERROR
			process = self._process

		try:
			exit_code = process.wait(timeout=timeout)
		except subprocess.TimeoutExpired:
			process.terminate()
			try:
				process.wait(timeout=2)
			except subprocess.TimeoutExpired:
				process.kill()
				process.wait()
			exit_code = VerificationResult.TIMEOUT_REACHED
		finally:
			with self._lock:
				self._process = None
				cancelled = self._cancelled

		if cancelled:
			return VerificationResult.CANCELLED

		# A negative return code means the process died from a signal
		if exit_code < 0:
			return VerificationResult.ABORT

		return VerificationResult.from_exit_code(exit_code)

	def cancel(self):
		"""Abort a running verification from another thread"""
		with self._lock:
			self._cancelled = True
			if self._process is not None:
				self._process.terminate()


class BoundVerifier:
	"""A FaceVerifier fixed to one user, the interface the authenticator expects"""

	def __init__(self, user, compare_script=None):
		self.user = user
		self._verifier = FaceVerifier(compare_script=compare_script)

	def verify(self, timeout=None):
		return self._verifier.verify(self.user, timeout=timeout)

	def cancel(self):
		self._verifier.cancel()


def verify_face(user, timeout=None):
	"""Verify the face of the given user, returns a VerificationResult"""
	return FaceVerifier().verify(user, timeout=timeout)
