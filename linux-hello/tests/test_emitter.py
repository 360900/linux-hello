import os
import uuid

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from recorders import uvc_xu  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "usb", "5986-118c.descriptors")
MSXU = str(uvc_xu.MS_CAMERA_CONTROL_XU)
VENDOR = "26b8105a-0713-4870-979d-da79444bb68e"

# The user's ThinkPad IR camera, as read from the device
FACE_AUTH_CUR = bytes.fromhex("010301000000000000")
FACE_AUTH_MAX = bytes.fromhex("010303000000000000")


@pytest.fixture
def emitter(fake_paths):
	import sys
	sys.modules.pop("emitter", None)
	import emitter
	return emitter


class FakeXu:
	"""Extension unit controls in memory, with an optional broken control"""

	def __init__(self, controls, unrestorable=None):
		# (unit, selector) -> dict(cur, max, info)
		self.controls_ = {key: dict(value, cur=bytes(value["cur"])) for key, value in controls.items()}
		self.unrestorable = unrestorable
		self.writes = []
		self.closed = False

	def __enter__(self):
		return self

	def __exit__(self, *_exc):
		self.close()

	def close(self):
		self.closed = True

	def _get(self, unit, selector):
		if (unit, selector) not in self.controls_:
			raise OSError(2, "no such control")
		return self.controls_[(unit, selector)]

	def length(self, unit, selector):
		return len(self._get(unit, selector)["cur"])

	def info(self, unit, selector):
		return self._get(unit, selector).get("info", 0x03)

	def get(self, unit, selector, code=uvc_xu.GET_CUR, length=None):
		control = self._get(unit, selector)
		if code == uvc_xu.GET_MAX:
			return control.get("max", bytes([255]) * len(control["cur"]))
		return control["cur"]

	def set(self, unit, selector, value):
		control = self._get(unit, selector)
		if (unit, selector) == self.unrestorable and self.writes:
			raise OSError(5, "device stopped responding")
		self.writes.append((unit, selector, bytes(value)))
		control["cur"] = bytes(value)

	def controls(self, units=None):
		found = []
		for (unit, selector), control in sorted(self.controls_.items()):
			if units is not None and unit not in units:
				continue
			found.append(uvc_xu.Control(
				unit, selector, len(control["cur"]), control.get("info", 0x03),
				cur=control["cur"], maximum=control.get("max")))
		return found


def install(monkeypatch, emitter, device, units):
	monkeypatch.setattr(emitter.uvc_xu, "XuDevice", lambda node: device)
	monkeypatch.setattr(emitter, "_units_by_guid", lambda node: (("5986:118c", 2), units))


# -- face authentication -----------------------------------------------------
def test_face_auth_payload(emitter):
	assert emitter.parse_face_auth(FACE_AUTH_CUR) == [(3, 0x1, 2, 7)]
	assert emitter.parse_face_auth(bytes(9)) == []


def test_face_auth_prefers_alternating_illumination(emitter):
	value, mode = emitter.face_auth_target(FACE_AUTH_CUR, FACE_AUTH_MAX)
	assert value == bytes.fromhex("010302000000000000")
	assert mode == emitter.MODE_ALTERNATIVE_FRAME_ILLUMINATION


def test_face_auth_background_subtraction(emitter):
	value, mode = emitter.face_auth_target(FACE_AUTH_CUR, bytes.fromhex("010305000000000000"))
	assert value == bytes.fromhex("010304000000000000")
	assert mode == emitter.MODE_BACKGROUND_SUBTRACTION


def test_face_auth_without_supported_mode(emitter):
	assert emitter.face_auth_target(FACE_AUTH_CUR, FACE_AUTH_CUR) is None


def test_two_streaming_interfaces(emitter):
	current = bytes.fromhex("02" "03" "01000000000000" "0b" "01000000000000")
	maximum = bytes.fromhex("02" "03" "03000000000000" "0b" "02000000000000")
	value, _mode = emitter.face_auth_target(current, maximum)
	assert emitter.parse_face_auth(value) == [(3, 0x2, 2, 7), (11, 0x2, 10, 7)]


# -- descriptors ---------------------------------------------------------------
def test_real_descriptors_map_units_per_interface():
	with open(FIXTURE, "rb") as file:
		parsed = uvc_xu.parse_video_control(file.read())
	# RGB control interface 0 streams on 1, IR control interface 2 on 3
	assert parsed[0]["streaming"] == [1]
	assert parsed[2]["streaming"] == [3]
	assert parsed[0]["units"][8] == uvc_xu.MS_CAMERA_CONTROL_XU
	assert parsed[2]["units"][14] == uvc_xu.MS_CAMERA_CONTROL_XU
	assert parsed[2]["units"][13] == uuid.UUID(VENDOR)


def test_query_struct_matches_kernel_abi():
	# struct uvc_xu_control_query is 16 bytes on 64 bit
	assert uvc_xu.UVCIOC_CTRL_QUERY == 0xC0107521


# -- sessions ----------------------------------------------------------------
def test_session_sets_face_auth_and_restores(emitter, monkeypatch):
	device = FakeXu({(14, 6): {"cur": FACE_AUTH_CUR, "max": FACE_AUTH_MAX}})
	install(monkeypatch, emitter, device, {MSXU: 14})

	session = emitter.activate("/dev/video2")
	assert session.alternating
	assert "alternative frame illumination" in session.method
	assert device.controls_[(14, 6)]["cur"] == bytes.fromhex("010302000000000000")

	session.close()
	assert device.controls_[(14, 6)]["cur"] == FACE_AUTH_CUR
	assert device.closed


def test_session_uses_stored_vendor_control(emitter, monkeypatch):
	emitter.remember(("5986:118c", 2), [{"guid": VENDOR, "selector": 14, "value": "0100"}])
	device = FakeXu({(13, 14): {"cur": b"\x00\x00"}})
	install(monkeypatch, emitter, device, {VENDOR: 13})

	session = emitter.activate("/dev/video2")
	assert session.method == "vendor control"
	assert not session.alternating
	assert device.controls_[(13, 14)]["cur"] == b"\x01\x00"
	session.close()
	assert device.controls_[(13, 14)]["cur"] == b"\x00\x00"


def test_nothing_known_means_no_session(emitter, monkeypatch):
	device = FakeXu({(13, 14): {"cur": b"\x00\x00"}})
	install(monkeypatch, emitter, device, {VENDOR: 13})
	assert emitter.activate("/dev/video2") is None
	assert device.writes == []
	assert device.closed


def test_broken_camera_is_used_as_is(emitter, monkeypatch):
	def unopenable(node):
		raise OSError(13, "permission denied")
	monkeypatch.setattr(emitter.uvc_xu, "XuDevice", unopenable)
	assert emitter.activate("/dev/video2") is None


# -- analyzer ------------------------------------------------------------------
def test_flash_analyzer(emitter):
	analyzer = emitter.FlashAnalyzer()
	for intensity in [1000.0, 1001.0, 1000.0, 1002.0] * 8:
		analyzer.feed(intensity)
	analyzer.set_reference()

	for intensity in [0.0, 6000.0] * 15:
		analyzer.feed(intensity)
	assert analyzer.lit()

	for intensity in [1000.0, 1001.0] * 15:
		analyzer.feed(intensity)
	assert not analyzer.lit()


def test_flash_analyzer_ignores_noise_on_a_still_scene(emitter):
	analyzer = emitter.FlashAnalyzer()
	for _ in range(30):
		analyzer.feed(1000.0)
	analyzer.set_reference()
	for intensity in [1000.0, 1003.0] * 15:
		analyzer.feed(intensity)
	assert not analyzer.lit()


# -- search --------------------------------------------------------------------
def candidates(emitter, cur, maximum=None):
	return list(emitter.candidate_values(uvc_xu.Control(1, 1, len(cur), 3, cur=cur, maximum=maximum)))


def test_candidate_values_respect_maximum(emitter):
	values = candidates(emitter, b"\x00\x00", maximum=b"\x02\x01")
	assert values == [b"\x01\x00", b"\x02\x00", b"\x00\x01"]


def test_candidate_values_per_byte_limit(emitter):
	assert len(candidates(emitter, b"\x00")) == 5


def make_search(emitter, monkeypatch, device, lights_when):
	install(monkeypatch, emitter, device, {MSXU: 14, VENDOR: 13})

	def read_frames(count):
		lit = any(device.controls_.get(key, {}).get("cur") == value for key, value in lights_when.items())
		return [0.0, 6000.0] * (count // 2) if lit else [1000.0] * count

	return read_frames


def test_search_finds_and_saves_the_control(emitter, monkeypatch):
	device = FakeXu({
		(14, 6): {"cur": FACE_AUTH_CUR},
		(13, 14): {"cur": b"\x00\x00"},
		(13, 16): {"cur": b"\x00", "info": 0x01},
	})
	read_frames = make_search(emitter, monkeypatch, device, {(13, 14): b"\x02\x00"})
	asked = []

	found = emitter.search("/dev/video2", read_frames, confirm=lambda c, v: asked.append(v) or True)
	assert found == {"guid": VENDOR, "selector": 14, "value": "0200"}
	assert asked == [b"\x02\x00"]
	# Microsoft's unit and read-only controls are never written
	assert all(unit == 13 and selector == 14 for unit, selector, _ in device.writes)
	# The control is back to its value, sessions apply the saved one
	assert device.controls_[(13, 14)]["cur"] == b"\x00\x00"
	assert emitter.stored_controls(("5986:118c", 2)) == [found]


def test_search_respects_a_rejection(emitter, monkeypatch):
	device = FakeXu({(13, 14): {"cur": b"\x00"}})
	read_frames = make_search(emitter, monkeypatch, device, {(13, 14): b"\x01"})
	assert emitter.search("/dev/video2", read_frames, confirm=lambda c, v: False) is None
	assert device.controls_[(13, 14)]["cur"] == b"\x00"
	assert emitter.stored_controls(("5986:118c", 2)) == []


def test_unrestorable_control_is_blacklisted(emitter, monkeypatch):
	device = FakeXu({(13, 14): {"cur": b"\x00"}, (13, 15): {"cur": b"\x00"}}, unrestorable=(13, 14))
	read_frames = make_search(emitter, monkeypatch, device, {})
	with pytest.raises(emitter.SearchAborted):
		emitter.search("/dev/video2", read_frames)
	assert emitter.blacklisted(("5986:118c", 2)) == {(VENDOR, 14)}

	# The next search skips it
	device = FakeXu({(13, 14): {"cur": b"\x00"}, (13, 15): {"cur": b"\x00"}})
	read_frames = make_search(emitter, monkeypatch, device, {})
	emitter.search("/dev/video2", read_frames)
	assert all(selector == 15 for _unit, selector, _value in device.writes)


# -- ambient pairing -------------------------------------------------------------
class Capture:
	def __init__(self, alternating):
		self.emitter = type("Session", (), {"alternating": alternating})()


def gray(level):
	return np.full((4, 4), level, np.uint8)


def test_pairing_subtracts_the_ambient_frame():
	from engine import AmbientPairing

	pairing = AmbientPairing(Capture(True))
	ambient = gray(30)
	assert pairing.lit(cv2.merge([ambient] * 3), ambient) == (None, None)
	lit = gray(90)
	frame, gsframe = pairing.lit(cv2.merge([lit] * 3), lit)
	assert int(gsframe[0, 0]) == 60
	assert int(frame[0, 0, 0]) == 60


def test_pairing_gives_up_without_alternation():
	from engine import AmbientPairing

	pairing = AmbientPairing(Capture(True))
	still = gray(40)
	results = [pairing.lit(cv2.merge([still] * 3), still) for _ in range(12)]
	assert results[0] == (None, None)
	assert results[-1][1] is still
	assert not pairing.enabled


def test_pairing_off_without_alternating_emitter():
	from engine import AmbientPairing

	frame = gray(40)
	assert AmbientPairing(Capture(False)).lit("frame", frame) == ("frame", frame)
	assert AmbientPairing(object()).lit("frame", frame) == ("frame", frame)


def test_flash_analyzer_survives_startup_pulse_in_reference(emitter):
	# Real reference stream of the user's camera: start-up pulse, then auto
	# exposure ramping up (per pixel means, 640x360)
	pixels = 640 * 360
	reference = [57.7, 0.0, 0.7, 0.0, 0.5, 0.5, 0.0, 0.0, 0.5, 0.5, 0.0, 0.1, 0.6, 0.6, 0.2, 0.6,
		1.4, 1.8, 1.3, 2.5, 2.8, 3.3, 3.1, 5.7, 5.2, 5.9, 6.2, 8.1, 11.4, 12.1]
	strobing = [48.8, 0.0, 48.4, 0.0, 47.8, 0.0, 47.2, 0.0, 46.7, 0.0, 44.0, 0.0, 43.2, 0.0, 42.5, 0.0]
	analyzer = emitter.FlashAnalyzer()
	for mean in reference:
		analyzer.feed(mean * pixels)
	analyzer.set_reference()
	for mean in strobing:
		analyzer.feed(mean * pixels)
	assert analyzer.lit()
