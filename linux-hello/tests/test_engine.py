import configparser
import itertools

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import engine as engine_module  # noqa: E402
from engine import DarkFilter, Engine  # noqa: E402
from recog.backend import FaceRectangle  # noqa: E402
from recorders.errors import CameraError  # noqa: E402
from verification import VerificationResult  # noqa: E402

HEIGHT, WIDTH = 120, 160
ME = [1.0, 0.0, 0.0]
STRANGER = [0.0, 1.0, 0.0]


def frame(dark_share=0.0):
	"""A BGR frame where dark_share of the columns are unlit"""
	image = np.full((HEIGHT, WIDTH, 3), 200, np.uint8)
	image[:, :int(WIDTH * dark_share)] = 5
	return image


def face(width_share=0.4):
	right = int(WIDTH * (0.3 + width_share))
	return FaceRectangle(30, int(WIDTH * 0.3), right, 100)


class Shot:
	"""What the camera sees in one frame: lighting and faces with encodings"""

	def __init__(self, faces=(), dark_share=0.0, black=False):
		self.faces = list(faces)
		self.image = np.zeros((HEIGHT, WIDTH, 3), np.uint8) if black else frame(dark_share)


class FakeInternal:
	def get(self, prop):
		return {cv2.CAP_PROP_FRAME_HEIGHT: HEIGHT, cv2.CAP_PROP_FRAME_WIDTH: WIDTH}.get(prop, 0)

	def set(self, prop, value):
		pass


class FakeCapture:
	"""Plays a list of shots, repeating the last one forever"""

	def __init__(self, shots, fail_after=None):
		self.shots = shots
		self.index = -1
		self.current = None
		self.released = False
		self.fail_after = fail_after
		self.internal = FakeInternal()

	def read_frame(self):
		self.index += 1
		if self.fail_after is not None and self.index >= self.fail_after:
			raise CameraError("unplugged")
		self.current = self.shots[min(self.index, len(self.shots) - 1)]
		image = self.current.image.copy()
		return image, cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

	def release(self):
		self.released = True


class StubBackend:
	"""Detects the faces scripted on the current shot of the capture"""

	detector = None

	def __init__(self, capture):
		self.capture = capture

	def detect_faces(self, gsframe, upsample=1):
		return [rect for rect, _encoding in self.capture.current.faces]

	def compute_encoding(self, frame, rect):
		for candidate, encoding in self.capture.current.faces:
			if candidate is rect:
				return None if encoding is None else np.array(encoding)
		return None

	def match(self, encodings, encoding):
		return np.linalg.norm(np.array(encodings) - encoding, axis=1)


def config(**values):
	parser = configparser.ConfigParser(interpolation=None)
	parser.read_dict({
		"core": {"use_cnn": "false"},
		"video": {
			"certainty": "3.5",
			"timeout": "2",
			"max_height": "120",
			"dark_threshold": "auto",
			"rotate": "0",
			"exposure": "-1",
		},
	})
	for key, value in values.items():
		parser.set("video", key, str(value))
	return parser


def make(shots, fail_after=None, open_error=None, **values):
	capture = FakeCapture(shots, fail_after)

	def capture_factory(_config):
		if open_error:
			raise CameraError(open_error)
		return capture

	created = []

	def backend_factory(**kwargs):
		created.append(kwargs)
		return StubBackend(capture)

	return Engine(config(**values), capture_factory, backend_factory), capture, created


# -- verification ------------------------------------------------------------
def test_verify_matches_after_misses():
	shots = [Shot(), Shot([(face(), STRANGER)]), Shot([(face(), ME)])]
	engine, capture, _ = make(shots)
	result, stats = engine.verify([[0.0, 0.0, 1.0], ME])
	assert result == VerificationResult.SUCCESS
	assert stats.model_index == 1
	assert stats.frames == 3
	assert stats.best == 0.0
	assert capture.released


def test_verify_times_out_with_best_certainty():
	engine, capture, _ = make([Shot([(face(), STRANGER)])], timeout=0.05)
	result, stats = engine.verify([ME])
	assert result == VerificationResult.TIMEOUT_REACHED
	assert stats.as_dict()["best_certainty"] == pytest.approx(14.142, abs=0.01)
	assert capture.released


def test_verify_only_black_frames_is_too_dark():
	engine, _capture, _ = make([Shot(black=True)], timeout=0.05)
	result, stats = engine.verify([ME])
	assert result == VerificationResult.TOO_DARK
	assert stats.black == stats.frames


def test_verify_fixed_threshold_rejects_dark_frames():
	# 70% dark is over a threshold of 60, the matching face is never seen
	engine, _capture, _ = make([Shot([(face(), ME)], dark_share=0.7)], timeout=0.05, dark_threshold=60)
	result, stats = engine.verify([ME])
	assert result == VerificationResult.TOO_DARK
	assert stats.dark == stats.valid > 0


def test_verify_without_encodings():
	engine, capture, created = make([Shot()])
	result, _stats = engine.verify([])
	assert result == VerificationResult.NO_FACE_MODEL
	assert capture.index == -1
	assert created == []


def test_verify_can_be_cancelled():
	engine, capture, _ = make([Shot()])
	calls = itertools.count()
	result, _stats = engine.verify([ME], cancelled=lambda: next(calls) >= 3)
	assert result == VerificationResult.CANCELLED
	assert capture.released


def test_verify_camera_missing():
	engine, _capture, _ = make([Shot()], open_error="No camera at /dev/video9")
	result, stats = engine.verify([ME])
	assert result == VerificationResult.INVALID_DEVICE
	assert stats.error == "No camera at /dev/video9"


def test_verify_camera_lost_midway():
	engine, capture, _ = make([Shot()], fail_after=2)
	result, stats = engine.verify([ME])
	assert result == VerificationResult.INVALID_DEVICE
	assert stats.error == "unplugged"
	assert capture.released


def test_after_match_can_veto():
	engine, capture, _ = make([Shot([(face(), ME)])])
	seen = []
	result, _stats = engine.verify([ME], after_match=lambda cap, backend: seen.append(cap) or False)
	assert result == VerificationResult.RUBBERSTAMP
	assert seen == [capture]


def test_keep_frames_collects_three():
	engine, _capture, _ = make([Shot(), Shot(), Shot(), Shot([(face(), ME)])])
	kept = []
	engine.verify([ME], keep_frames=kept)
	assert len(kept) == 3


def test_backend_is_loaded_once_per_dimension():
	engine, _capture, created = make([Shot([(face(), ME)])])
	engine.verify([ME])
	engine.verify([ME])
	assert len(created) == 1
	assert created[0]["enrolled_dims"] == {3}


def test_backend_error_releases_camera():
	capture = FakeCapture([Shot()])

	def broken_backend(**kwargs):
		raise FileNotFoundError("dlib data missing")

	engine = Engine(config(), lambda _config: capture, broken_backend)
	with pytest.raises(FileNotFoundError):
		engine.verify([ME])
	assert capture.released


# -- enrolment ---------------------------------------------------------------
def test_enroll_guides_until_samples_are_captured(monkeypatch):
	monkeypatch.setattr(engine_module, "SAMPLE_SPACING", 0)
	shots = [
		Shot(),
		Shot([(face(), ME), (face(), STRANGER)]),
		Shot([(face(width_share=0.1), ME)]),
		Shot(black=True),
		Shot([(face(), ME)]),
	]
	engine, capture, _ = make(shots)
	updates = []
	result, encodings, _stats = engine.enroll(samples=3, on_progress=updates.append)

	assert result == VerificationResult.SUCCESS
	assert encodings == [ME, ME, ME]
	states = [u["state"] for u in updates]
	assert states[:5] == ["searching", "no-face", "multiple-faces", "too-far", "too-dark"]
	assert states[-3:] == ["captured"] * 3
	assert updates[-1]["sample"] == 3 and updates[-1]["samples"] == 3
	left, top, right, bottom = updates[-1]["face"]
	assert 0 <= left < right <= 1 and 0 <= top < bottom <= 1
	assert capture.released


def test_frames_are_shown_before_their_progress(monkeypatch):
	monkeypatch.setattr(engine_module, "SAMPLE_SPACING", 0)
	shots = [Shot(black=True), Shot([(face(), ME)])]
	engine, _capture, _ = make(shots)
	events = []
	engine.enroll(samples=2, on_progress=lambda u: events.append(u["state"]),
		on_frame=lambda gsframe: events.append(gsframe.shape))
	# The black frame is not shown, each lit frame comes before its report
	assert events == ["searching", "too-dark", (HEIGHT, WIDTH), "captured", (HEIGHT, WIDTH), "captured"]


def test_verify_shows_frames_it_looks_at():
	shots = [Shot(black=True), Shot([(face(), STRANGER)]), Shot([(face(), ME)])]
	engine, _capture, _ = make(shots)
	shown = []
	result, _stats = engine.verify([ME], on_frame=shown.append)
	assert result == VerificationResult.SUCCESS
	assert len(shown) == 2 and shown[0].ndim == 2


def test_enroll_spaces_samples(monkeypatch):
	monkeypatch.setattr(engine_module, "SAMPLE_SPACING", 60)
	engine, _capture, _ = make([Shot([(face(), ME)])], timeout=0.05)
	result, encodings, _stats = engine.enroll(samples=2, timeout=0.05)
	# Only one sample: the second would come too soon after the first
	assert result == VerificationResult.TIMEOUT_REACHED
	assert encodings == [ME]


def test_enroll_in_the_dark():
	engine, _capture, _ = make([Shot(black=True)])
	result, encodings, _stats = engine.enroll(samples=1, timeout=0.05)
	assert result == VerificationResult.TOO_DARK
	assert encodings == []


# -- dark filter -------------------------------------------------------------
def test_auto_dark_filter_learns_baseline():
	dark = DarkFilter("auto")
	for _ in range(5):
		assert dark.accept(20.0)
	# Baseline 20: frames over 35 (20 * 1.25 + 10) are unlit emitter frames
	assert dark.accept(34.0)
	assert not dark.accept(60.0)


def test_auto_dark_filter_caps_at_95():
	dark = DarkFilter("auto")
	for _ in range(5):
		assert dark.accept(90.0)
	assert not dark.accept(96.0)


def test_numeric_dark_filter():
	dark = DarkFilter("60")
	assert dark.accept(60.0)
	assert not dark.accept(60.5)
