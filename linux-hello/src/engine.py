# Face capture and matching, shared by the daemon and the CLI.
#
# The engine owns the frame loop: dark frame rejection, scaling, rotation,
# detection, encoding and matching. Callers pass encodings in and get a
# result plus statistics back, and can follow along with a progress
# callback (used for live hints during enrolment), watch the frames it
# looks at and cancel at any time.

import threading
import time

import cv2
import numpy as np

from recog import create_backend
from recorders.errors import CameraError
from recorders.video_capture import VideoCapture
from verification import VerificationResult

# Enrolment
DEFAULT_SAMPLES = 5
SAMPLE_SPACING = 0.35
# A face narrower than this share of the frame is "too far away"
MIN_FACE_SHARE = 0.18


class DarkFilter:
	"""
	Rejects unlit frames. Flashing IR emitters produce near-black frames
	between lit ones. With a number as threshold, frames whose darkest
	histogram bucket holds more than that percentage are skipped. With
	"auto", a baseline is learned from accepted frames and frames much
	darker than it are skipped, which needs no tuning per camera.
	"""

	def __init__(self, threshold):
		raw = str(threshold).strip().lower()
		self.auto = raw in ("auto", "none", "")
		self.threshold = None if self.auto else float(raw)
		self.history = []

	def darkness(self, gsframe):
		# ravel: OpenCV 4 returns an (8, 1) array, OpenCV 5 a flat one
		hist = cv2.calcHist([gsframe], [0], None, [8], [0, 256]).ravel()
		total = float(np.sum(hist))
		if total == 0:
			return 100.0
		return float(hist[0]) / total * 100

	def accept(self, darkness):
		if self.auto:
			# The cap at 95 keeps fully unlit frames out even in very dim
			# rooms where the lit baseline is high
			if len(self.history) >= 5:
				baseline = sorted(self.history)[len(self.history) // 2]
				if darkness > min(95.0, baseline * 1.25 + 10):
					return False
			self.history.append(darkness)
			if len(self.history) > 30:
				self.history.pop(0)
			return True
		return darkness <= self.threshold


class AmbientPairing:
	"""
	With an emitter that lights every other frame (face authentication's
	alternative frame illumination), the unlit frame shows only ambient
	light. Subtracting it from the next lit frame leaves what the emitter
	lit, so daylight and lamps stop mattering. Cameras without such an
	emitter pass straight through.
	"""

	# Lit frames are clearly brighter than the ambient frame before them
	RATIO = 1.15
	MARGIN = 2.0
	# Give up pairing when no lit frame turns up for this many frames
	PATIENCE = 10

	def __init__(self, capture):
		session = getattr(capture, "emitter", None)
		self.enabled = bool(getattr(session, "alternating", False))
		self.ambient = None
		self.unpaired = 0

	def lit(self, frame, gsframe):
		"""(frame, gsframe) to use, or (None, None) for an ambient frame"""
		if not self.enabled:
			return frame, gsframe
		level = float(gsframe.mean())
		if self.ambient is not None and level > self.ambient[2] * self.RATIO + self.MARGIN:
			ambient_frame, ambient_gray, _level = self.ambient
			self.ambient = None
			self.unpaired = 0
			return cv2.subtract(frame, ambient_frame), cv2.subtract(gsframe, ambient_gray)

		self.ambient = (frame, gsframe, level)
		self.unpaired += 1
		if self.unpaired > self.PATIENCE:
			# The emitter does not alternate after all
			self.enabled = False
			return frame, gsframe
		return None, None


class Stats:
	def __init__(self):
		self.frames = 0
		self.black = 0
		self.dark = 0
		self.valid = 0
		self.darkness_total = 0.0
		self.best = None
		self.started = time.monotonic()
		self.elapsed = 0.0
		self.width = 0
		self.height = 0
		self.used_width = 0
		self.used_height = 0
		self.model_index = None
		self.error = None

	def as_dict(self):
		return {
			"frames": self.frames,
			"black_frames": self.black,
			"dark_frames": self.dark,
			"best_certainty": None if self.best is None else round(self.best * 10, 3),
			"seconds": round(self.elapsed, 3),
			"fps": round(self.frames / self.elapsed, 2) if self.elapsed else 0.0,
		}


class Engine:
	"""
	One engine per process. The recognition backend is loaded once and kept,
	the camera is opened per request and released when it ends.
	"""

	def __init__(self, config, capture_factory=VideoCapture, backend_factory=create_backend):
		self.config = config
		self.capture_factory = capture_factory
		self.backend_factory = backend_factory
		self._backends = {}

	# -- setup -------------------------------------------------------------
	def backend(self, enrolled_dims=None, mismatch_hint=""):
		# Long-lived callers (the daemon) swap self.config between requests,
		# so the detector setting is part of the key
		use_cnn = self.config.getboolean("core", "use_cnn", fallback=False)
		key = (use_cnn, frozenset(enrolled_dims or ()))
		if key not in self._backends:
			self._backends[key] = self.backend_factory(
				use_cnn=use_cnn,
				enrolled_dims=set(key[1]),
				mismatch_hint=mismatch_hint,
			)
		return self._backends[key]

	def open_camera(self):
		"""Open the configured camera. Raises CameraError."""
		return self.capture_factory(self.config)

	def _open_with_backend(self, enrolled_dims, mismatch_hint):
		"""
		Open the camera while the backend loads, the two slowest steps of a
		cold start. Returns (capture, backend). Raises CameraError, or the
		backend's error (FileNotFoundError when dlib data is missing).
		"""
		opened = {}

		def opener():
			try:
				opened["capture"] = self.open_camera()
			except CameraError as error:
				opened["error"] = error

		thread = threading.Thread(target=opener, daemon=True)
		thread.start()
		try:
			backend = self.backend(enrolled_dims, mismatch_hint)
		except BaseException:
			thread.join()
			if "capture" in opened:
				opened["capture"].release()
			raise
		thread.join()
		if "error" in opened:
			raise opened["error"]
		return opened["capture"], backend

	def _scaling(self, capture):
		max_height = self.config.getfloat("video", "max_height", fallback=320.0)
		rotate = self.config.getint("video", "rotate", fallback=0)
		height = capture.internal.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1
		if rotate == 2:
			height = capture.internal.get(cv2.CAP_PROP_FRAME_WIDTH) or 1
		return (max_height / height) or 1

	def _apply_exposure(self, capture):
		exposure = self.config.getint("video", "exposure", fallback=-1)
		if exposure != -1:
			capture.internal.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
			capture.internal.set(cv2.CAP_PROP_EXPOSURE, float(exposure))

	def _frames(self, capture, stats, keep=None):
		"""
		Yield (frame, gsframe, darkness) for every usable frame, scaled and
		rotated as configured. Black and dark frames are counted and skipped.
		"""
		dark_filter = DarkFilter(self.config.get("video", "dark_threshold", fallback="auto"))
		clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
		rotate = self.config.getint("video", "rotate", fallback=0)
		scaling = self._scaling(capture)
		pairing = AmbientPairing(capture)

		while True:
			stats.frames += 1
			frame, gsframe = capture.read_frame()
			stats.height, stats.width = frame.shape[:2]
			frame, gsframe = pairing.lit(frame, gsframe)
			if frame is None:
				# The unlit half of an alternating emitter pair
				stats.black += 1
				yield None, None, 100.0
				continue
			gsframe = clahe.apply(gsframe)
			if keep is not None and len(keep) < 3:
				keep.append(frame)

			darkness = dark_filter.darkness(gsframe)
			if darkness >= 100:
				stats.black += 1
				yield None, None, darkness
				continue

			stats.valid += 1
			stats.darkness_total += darkness
			if not dark_filter.accept(darkness):
				stats.dark += 1
				yield None, None, darkness
				continue

			if scaling != 1:
				frame = cv2.resize(frame, None, fx=scaling, fy=scaling, interpolation=cv2.INTER_AREA)
				gsframe = cv2.resize(gsframe, None, fx=scaling, fy=scaling, interpolation=cv2.INTER_AREA)

			# Portrait checks alternate orientation per frame
			turn = None
			if rotate == 1:
				turn = {1: cv2.ROTATE_90_COUNTERCLOCKWISE, 2: cv2.ROTATE_90_CLOCKWISE}.get(stats.frames % 3)
			elif rotate == 2:
				turn = cv2.ROTATE_90_COUNTERCLOCKWISE if stats.frames % 2 == 0 else cv2.ROTATE_90_CLOCKWISE
			if turn is not None:
				frame = cv2.rotate(frame, turn)
				gsframe = cv2.rotate(gsframe, turn)

			stats.used_height, stats.used_width = frame.shape[:2]
			yield frame, gsframe, darkness

	def _timed_out_result(self, stats):
		# Only black or only too dark frames: point at lighting, not the face
		if stats.dark == stats.valid:
			return VerificationResult.TOO_DARK
		return VerificationResult.TIMEOUT_REACHED

	# -- verification ------------------------------------------------------
	def verify(self, encodings, timeout=None, on_progress=None, cancelled=None,
			keep_frames=None, after_match=None, capture=None, on_frame=None):
		"""
		Look for a face matching one of encodings until timeout seconds
		pass. Returns (VerificationResult, Stats).

		on_frame(gsframe) gets every frame the detector looks at.

		after_match(capture, backend) runs with the camera still open after
		a match and may veto it by returning False (rubberstamps).
		"""
		stats = Stats()
		if not encodings:
			return VerificationResult.NO_FACE_MODEL, stats

		if timeout is None:
			timeout = self.config.getfloat("video", "timeout", fallback=4.0)
		certainty = self.config.getfloat("video", "certainty", fallback=3.5) / 10
		dims = {len(e) for e in encodings}
		hint = "Re-run 'linux-hello-cli add' to re-enroll for OpenVINO."

		owned = capture is None
		try:
			if owned:
				capture, backend = self._open_with_backend(dims, hint)
			else:
				backend = self.backend(dims, hint)
		except CameraError as error:
			stats.error = str(error)
			return VerificationResult.INVALID_DEVICE, stats

		try:
			stats.started = time.monotonic()
			for frame, gsframe, _darkness in self._frames(capture, stats, keep_frames):
				stats.elapsed = time.monotonic() - stats.started
				if cancelled is not None and cancelled():
					return VerificationResult.CANCELLED, stats
				if stats.elapsed > timeout:
					return self._timed_out_result(stats), stats
				if on_progress is not None:
					on_progress({"frames": stats.frames, "dark_frames": stats.dark})
				if frame is None:
					continue
				if on_frame is not None:
					on_frame(gsframe)

				for location in backend.detect_faces(gsframe, 1):
					encoding = backend.compute_encoding(frame, location)
					if encoding is None:
						continue
					distances = backend.match(encodings, encoding)
					index = int(np.argmin(distances))
					distance = float(distances[index])
					if stats.best is None or distance < stats.best:
						stats.best = distance
					if 0 <= distance < certainty:
						stats.model_index = index
						stats.elapsed = time.monotonic() - stats.started
						if after_match is not None and after_match(capture, backend) is False:
							return VerificationResult.RUBBERSTAMP, stats
						return VerificationResult.SUCCESS, stats

				self._apply_exposure(capture)
		except CameraError as error:
			stats.error = str(error)
			return VerificationResult.INVALID_DEVICE, stats
		finally:
			if owned:
				capture.release()

	# -- enrolment ---------------------------------------------------------
	def enroll(self, samples=DEFAULT_SAMPLES, timeout=20.0, on_progress=None,
			cancelled=None, enrolled_dims=None, capture=None, on_frame=None):
		"""
		Capture samples encodings of a single face. Between samples the
		engine waits a moment so they differ a little (blinks, small head
		movements), which makes the model more robust than one frame.

		on_progress gets {"state", "sample", "samples", "face", "darkness"}
		where state is one of: searching, no-face, multiple-faces,
		too-far, too-dark, captured. face is [left, top, right, bottom]
		relative to the frame (0..1) or None. on_frame(gsframe) gets each
		frame before its progress report.

		Returns (VerificationResult, encodings, Stats). TIMEOUT_REACHED
		and TOO_DARK return the samples captured so far.
		"""
		stats = Stats()
		hint = "Remove the old models first to switch to OpenVINO."
		captured = []
		# Not 0: the monotonic clock starts at boot
		last_capture = float("-inf")

		def report(state, face=None, darkness=None):
			if on_progress is not None:
				on_progress({
					"state": state,
					"sample": len(captured),
					"samples": samples,
					"face": face,
					"darkness": None if darkness is None else round(darkness, 1),
				})

		owned = capture is None
		try:
			if owned:
				capture, backend = self._open_with_backend(enrolled_dims, hint)
			else:
				backend = self.backend(enrolled_dims, hint)
		except CameraError as error:
			stats.error = str(error)
			return VerificationResult.INVALID_DEVICE, [], stats

		try:
			stats.started = time.monotonic()
			report("searching")
			for frame, gsframe, darkness in self._frames(capture, stats):
				stats.elapsed = time.monotonic() - stats.started
				if cancelled is not None and cancelled():
					return VerificationResult.CANCELLED, captured, stats
				if stats.elapsed > timeout:
					return self._timed_out_result(stats), captured, stats
				if frame is None:
					report("too-dark", darkness=darkness)
					continue
				if on_frame is not None:
					on_frame(gsframe)

				locations = backend.detect_faces(gsframe, 1)
				height, width = gsframe.shape[:2]
				if not locations:
					report("no-face", darkness=darkness)
					continue
				if len(locations) > 1:
					report("multiple-faces", darkness=darkness)
					continue

				location = locations[0]
				face = [
					round(max(0, location.left()) / width, 3),
					round(max(0, location.top()) / height, 3),
					round(min(width, location.right()) / width, 3),
					round(min(height, location.bottom()) / height, 3),
				]
				if face[2] - face[0] < MIN_FACE_SHARE:
					report("too-far", face, darkness)
					continue
				if time.monotonic() - last_capture < SAMPLE_SPACING:
					report("searching", face, darkness)
					continue

				encoding = backend.compute_encoding(frame, location)
				if encoding is None:
					report("no-face", face, darkness)
					continue

				captured.append([float(v) for v in encoding])
				last_capture = time.monotonic()
				report("captured", face, darkness)
				if len(captured) >= samples:
					stats.elapsed = time.monotonic() - stats.started
					return VerificationResult.SUCCESS, captured, stats

				self._apply_exposure(capture)
		except CameraError as error:
			stats.error = str(error)
			return VerificationResult.INVALID_DEVICE, captured, stats
		finally:
			if owned:
				capture.release()
