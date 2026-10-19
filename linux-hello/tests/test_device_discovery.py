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


def test_single_ir_camera_is_used_without_test_streaming(fake):
	opened, _frames = fake
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video2"
	assert opened == []


def test_several_ir_cameras_are_ranked_without_the_webcam(fake, monkeypatch):
	opened, frames = fake
	monkeypatch.setitem(DEVICES, "/dev/video4", ["Y16 "])
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: list(DEVICES))
	frames["/dev/video4"] = {"ok": True, "frames": 3, "brightness": 90.0, "saturation": 0.0}
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video2"
	assert sorted(opened) == ["/dev/video2", "/dev/video4"]


def test_reconnected_ir_camera_wins_again(fake, monkeypatch):
	opened, _frames = fake
	# Booted with the IR camera unplugged: the webcam is the only choice
	monkeypatch.setitem(DEVICES, "/dev/video2", None)
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: ["/dev/video0", "/dev/video1"])
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video0"

	# Plugged back in: the very next attempt uses it, without a restart
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: list(DEVICES))
	monkeypatch.setitem(DEVICES, "/dev/video2", ["GREY"])
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video2"


def test_broken_ir_camera_never_falls_back_to_the_webcam(fake, monkeypatch):
	from recorders.errors import CameraError

	opened, frames = fake
	# A single IR camera is handed over as is, opening it for the scan fails
	frames["/dev/video2"] = {"ok": False, "frames": 0}
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video2"

	# Several broken IR cameras: an error, still never the webcam
	monkeypatch.setitem(DEVICES, "/dev/video4", ["GREY"])
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: list(DEVICES))
	with pytest.raises(CameraError) as error:
		discovery.autodetect_device()
	assert "infrared camera" in str(error.value)
	assert "/dev/video0" not in opened


def test_webcam_is_used_without_any_ir_camera(fake, monkeypatch):
	opened, _frames = fake
	monkeypatch.setattr(discovery, "candidate_nodes", lambda: ["/dev/video0", "/dev/video1"])
	assert discovery.autodetect_device() == "/dev/v4l/by-path/video0"


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


def test_video_index_follows_stable_links(tmp_path, monkeypatch):
	pytest.importorskip("cv2")
	from recorders import video_capture

	link = "/dev/v4l/by-path/pci-0000:00:14.0-usbv2-0:8:1.2-video-index0"
	real = {link: "/dev/video2", "/dev/video2": "/dev/video2", "/dev/v4l/by-id/broken-video-index0": "/dev/v4l/by-id/broken-video-index0"}
	monkeypatch.setattr(video_capture.os.path, "realpath", lambda path: real[path])
	# The name ends in index0, but the device behind it is video2
	assert video_capture.video_index(link) == 2
	assert video_capture.video_index("/dev/video2") == 2
	assert video_capture.video_index("/dev/v4l/by-id/broken-video-index0") is None


def test_by_path_wins_over_ambiguous_by_id(monkeypatch):
	# The user's ThinkPad: RGB (interface 1.0) and IR (1.2) share one USB
	# serial, so the single by-id "index0" link points at whichever node
	# udev handled last, here the IR camera
	links = {
		"/dev/v4l/by-id": {"usb-Generic_Integrated_Camera_200901010001-video-index0": "/dev/video2"},
		"/dev/v4l/by-path": {
			"pci-0000:00:14.0-usb-0:8:1.0-video-index0": "/dev/video0",
			"pci-0000:00:14.0-usb-0:8:1.2-video-index0": "/dev/video2",
		},
	}

	def listdir(directory):
		if directory == "/dev":
			return ["video0", "video2"]
		return list(links[directory])

	def realpath(path):
		for directory, entries in links.items():
			if path.startswith(directory + "/"):
				return entries[path[len(directory) + 1:]]
		return path

	monkeypatch.setattr(discovery.os, "listdir", listdir)
	monkeypatch.setattr(discovery.os.path, "realpath", realpath)
	monkeypatch.setattr(discovery, "_stable_paths", {})

	assert sorted(discovery.candidate_nodes()) == ["/dev/video0", "/dev/video2"]
	assert discovery.stable_path("/dev/video2") == "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:8:1.2-video-index0"
	assert discovery.stable_path("/dev/video0") == "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:8:1.0-video-index0"
