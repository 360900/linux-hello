import os
import threading
import time

import pytest

from daemon import confirm

KEY_A, KEY_B, KEY_C = 30, 48, 46
BTN_LEFT = 0x110


def event(code, value=1, kind=confirm.EV_KEY):
	return confirm.EVENT.pack(0, 0, kind, code, value)


def keyboard(root, name, enter=True, virtual=False, udev="E:ID_INPUT=1\nE:ID_INPUT_KEY=1\nE:ID_INPUT_KEYBOARD=1", mode=0o660):
	"""A fake input device: sysfs entry, udev database record and /dev node"""
	number = "13:" + name[len("event"):]
	device = root / "devices" / ("virtual" if virtual else "platform/i8042") / "input" / name
	(device / "capabilities").mkdir(parents=True)
	(device / "capabilities" / "key").write_text(("10000000" if enter else "0") + "\n")
	(root / "class" / name).mkdir(parents=True)
	(root / "class" / name / "device").symlink_to(device)
	(root / "class" / name / "dev").write_text(number + "\n")
	(root / "udev").mkdir(exist_ok=True)
	(root / "udev" / ("c" + number)).write_text(udev + "\n")
	(root / "dev").mkdir(exist_ok=True)
	(root / "dev" / name).write_text("")
	os.chmod(root / "dev" / name, mode)
	return str(root / "dev" / name)


def found(root):
	return [os.path.basename(node) for node in confirm.keyboards(str(root / "class"), str(root / "udev"), str(root / "dev"))]


def test_only_physical_keyboards_count(tmp_path):
	keyboard(tmp_path, "event2")
	keyboard(tmp_path, "event5", virtual=True)
	keyboard(tmp_path, "event7", enter=False)
	assert found(tmp_path) == ["event2"]


def test_joysticks_and_writable_devices_do_not_count(tmp_path):
	# Writing an event to a device node makes every reader see it, and
	# logind lets the user write to game controllers
	keyboard(tmp_path, "event2")
	keyboard(tmp_path, "event3", udev="E:ID_INPUT_KEYBOARD=1\nE:ID_INPUT_JOYSTICK=1")
	keyboard(tmp_path, "event4", udev="E:ID_INPUT_KEY=1")
	keyboard(tmp_path, "event6", mode=0o662)
	assert found(tmp_path) == ["event2"]


def test_a_user_acl_with_write_does_not_count(tmp_path):
	node = keyboard(tmp_path, "event2")
	header = confirm.ACL_HEADER.pack(2)
	entries = [(0x01, 6, 0xFFFFFFFF), (confirm.ACL_USER, 6, 1000), (0x04, 6, 0xFFFFFFFF), (0x10, 6, 0xFFFFFFFF), (0x20, 0, 0xFFFFFFFF)]
	try:
		os.setxattr(node, "system.posix_acl_access", header + b"".join(confirm.ACL_ENTRY.pack(*e) for e in entries))
	except OSError:
		pytest.skip("no ACL support here")
	assert found(tmp_path) == []


@pytest.fixture
def keys():
	"""Plays key events to wait_for_enter, as if typed after it started"""
	read_end, write_end = os.pipe()
	timers = []

	def press(*events, delay=0.1):
		timers.append(threading.Timer(delay, lambda: os.write(write_end, b"".join(events))))
		timers[-1].start()

	press.wait = lambda cancel=None, **kwargs: confirm.wait_for_enter(
		cancel or threading.Event(), devices=lambda: ["kbd"], opener=lambda node: os.dup(read_end), **kwargs)
	yield press
	for timer in timers:
		timer.cancel()
		timer.join()
	os.close(read_end)
	os.close(write_end)


def test_enter_alone(keys):
	keys(event(confirm.KEY_ENTER))
	assert keys.wait() == confirm.ENTER


def test_esc_skips(keys):
	keys(event(KEY_A), event(confirm.KEY_ESC))
	assert keys.wait() == confirm.SKIP


def test_one_slip_is_forgiven(keys):
	keys(event(KEY_A), event(KEY_A, 0), event(confirm.KEY_KPENTER))
	assert keys.wait() == confirm.ENTER


def test_typing_needs_the_password(keys):
	# "ab" then Enter: a word, a password or a chat message
	keys(event(KEY_A), event(KEY_B), event(confirm.KEY_ENTER))
	assert keys.wait() == confirm.TYPING


def test_releases_repeats_and_mouse_buttons_do_not_count(keys):
	keys(event(KEY_A), event(KEY_A, 2), event(KEY_A, 0), event(BTN_LEFT), event(BTN_LEFT, 0),
		event(KEY_C, kind=0x04), event(confirm.KEY_ENTER))
	assert keys.wait() == confirm.ENTER


def test_too_late(keys):
	keys(event(confirm.KEY_ENTER), delay=0.5)
	started = time.monotonic()
	assert keys.wait(window=0.2) == confirm.NOTHING
	assert time.monotonic() - started < 0.5


def test_no_keyboard_no_confirmation():
	started = time.monotonic()
	assert confirm.wait_for_enter(threading.Event(), devices=lambda: []) == confirm.NOTHING
	assert time.monotonic() - started < 0.1


def test_cancel(keys):
	cancel = threading.Event()
	threading.Timer(0.1, cancel.set).start()
	assert keys.wait(cancel=cancel) == confirm.NOTHING


def test_keys_are_never_logged():
	# The privacy promise in daemon/confirm.py: no way for a key to leave it
	import inspect
	source = inspect.getsource(confirm)
	assert "logging" not in source and "print(" not in source
