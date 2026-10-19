import os
import threading
import time

import pytest

from daemon import presence


def device(tmp_path, name, **bitmaps):
	path = tmp_path / name / "device" / "capabilities"
	path.mkdir(parents=True)
	for kind in ("ev", "key", "rel", "abs", "sw"):
		(path / kind).write_text(bitmaps.get(kind, "0") + "\n")
	return str(path)


def test_device_kinds(tmp_path):
	keyboard = device(tmp_path, "kbd", ev="120013", key="402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe")
	mouse = device(tmp_path, "mouse", ev="17", key="1f0000 0 0 0 0", rel="903")
	touchpad = device(tmp_path, "touchpad", ev="b", key="e520 10000 0 0 0 0", abs="2e0800000000003")
	lid = device(tmp_path, "lid", ev="21", sw="1")
	accelerometer = device(tmp_path, "accel", ev="9", abs="7")
	power_button = device(tmp_path, "power", ev="3", key="10000000000000 0")

	assert presence.is_activity_device(keyboard)
	assert presence.is_activity_device(mouse)
	assert presence.is_activity_device(touchpad)
	assert presence.is_activity_device(lid)
	assert not presence.is_activity_device(accelerometer)
	assert not presence.is_activity_device(power_button)


def test_activity_devices_lists_event_nodes(tmp_path):
	device(tmp_path, "event3", ev="17", rel="3")
	device(tmp_path, "event4", ev="9", abs="7")
	device(tmp_path, "mouse0", ev="17", rel="3")
	assert presence.activity_devices(str(tmp_path)) == ["/dev/input/event3"]


@pytest.fixture
def pipe():
	read_end, write_end = os.pipe()
	yield read_end, write_end
	os.close(write_end)


def test_wakes_on_an_event(pipe):
	read_end, write_end = pipe
	threading.Timer(0.2, lambda: os.write(write_end, b"x")).start()
	started = time.monotonic()
	assert presence.wait_for_activity(threading.Event(), 5, devices=lambda: ["kbd"], opener=lambda node: read_end)
	assert time.monotonic() - started < 2


def test_cancel_and_timeout(pipe):
	read_end, _write_end = pipe
	cancel = threading.Event()
	threading.Timer(0.2, cancel.set).start()
	assert not presence.wait_for_activity(cancel, 5, devices=lambda: ["kbd"], opener=lambda node: os.dup(read_end))

	started = time.monotonic()
	assert not presence.wait_for_activity(threading.Event(), 0.3, devices=lambda: [], opener=None)
	assert time.monotonic() - started < 2


def test_picks_up_plugged_in_devices(pipe, monkeypatch):
	read_end, write_end = pipe
	monkeypatch.setattr(presence, "RESCAN_SECONDS", 0.05)
	plugged = []
	threading.Timer(0.2, lambda: plugged.append("mouse")).start()
	threading.Timer(0.4, lambda: os.write(write_end, b"x")).start()
	assert presence.wait_for_activity(threading.Event(), 5, devices=lambda: list(plugged), opener=lambda node: read_end)
