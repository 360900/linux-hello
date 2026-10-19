import pytest

from recorders import device_discovery as discovery

DEVICES = {
	"/dev/video0": ["MJPG", "YUYV"],
	"/dev/video1": [],
	"/dev/video2": ["GREY"],
	"/dev/video3": [],
}


@pytest.fixture
def fake(monkeypatch):
	opened = []
	frames = {
		# A colour webcam in a dim room looks almost grayscale
		"/dev/video0": {"ok": True, "frames": 3, "brightness": 153.0, "saturation": 6.2},
		"/dev/video2": {"ok": True, "frames": 3, "brightness": 16.6, "saturation": 0.0},
	}

	def probe(node, timeout=2.0):
		opened.append(node)
		result = {"node": node}
		result.update(frames.get(node, {"ok": False, "frames": 0}))
		return result

	monkeypatch.setattr(discovery, "candidate_nodes", lambda: list(DEVICES))
	monkeypatch.setattr(discovery, "capture_formats", lambda node: DEVICES[node])
	monkeypatch.setattr(discovery, "probe_device", probe)
	monkeypatch.setattr(discovery, "stable_path", lambda node: "/dev/v4l/by-path/" + node[5:])
	monkeypatch.setattr(discovery.os.path, "exists", lambda path: True)
	return opened, frames


def test_autodetect_never_opens_the_webcam_when_ir_exists(fake):
	opened, _frames = fake
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video2"
	assert opened == ["/dev/video2"]


def test_reconnected_ir_camera_wins_again(fake, monkeypatch):
	opened, _frames = fake
	# Booted with the IR camera unplugged: the webcam is the only choice
	monkeypatch.setitem(DEVICES, "/dev/video2", None)
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: ["/dev/video0", "/dev/video1"])
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video0"

	# Plugged back in: the very next attempt uses it, without a restart
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: list(DEVICES))
	monkeypatch.setitem(DEVICES, "/dev/video2", ["GREY"])
	opened.clear()
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video2"
	assert opened == ["/dev/video2"]


def test_falls_back_to_rgb_when_ir_gives_no_frames(fake):
	opened, frames = fake
	frames["/dev/video2"] = {"ok": False, "frames": 0}
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video0"
	assert "/dev/video0" in opened


def test_listing_probes_capture_nodes_only(fake):
	opened, _frames = fake
	results = discovery.discover_cameras(probe_all=True)
	assert sorted(opened) == ["/dev/video0", "/dev/video2"]
	assert [r["node"] for r in results] == ["/dev/video2", "/dev/video0"]
	assert results[0]["infrared"] is True
	# Advertised formats beat the grayscale guess for the dim webcam
	assert results[1]["infrared"] is False


def test_unknown_formats_fall_back_to_grayscale_guess():
	assert discovery.is_infrared({"formats": None, "saturation": 3.0})
	assert not discovery.is_infrared({"formats": None, "saturation": 40.0})
	assert discovery.is_infrared({"formats": ["Y16 "], "saturation": 40.0})
