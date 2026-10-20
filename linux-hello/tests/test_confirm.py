import os
import threading
import time

import pytest

from daemon import confirm

KEY_A, KEY_B, KEY_C = 30, 48, 46
BTN_LEFT = 0x110


def event(code, value=1, kind=confirm.EV_KEY):
	return confirm.EVENT.pack(0, 0, kind, code, value)


def keyboard(root, name, enter=True, virtual=False):
	"""A fake /sys/class/input/<name>, linked into a physical or virtual device tree"""
	device = root / "devices" / ("virtual" if virtual else "platform/i8042") / "input" / name
	(device / "capabilities").mkdir(parents=True)
	(device / "capabilities" / "key").write_text(("10000000" if enter else "0") + "\n")
	(root / "class" / name).mkdir(parents=True)
	(root / "class" / name / "device").symlink_to(device)


def test_only_physical_keyboards_count(tmp_path):
	keyboard(tmp_path, "event2")
	keyboard(tmp_path, "event5", virtual=True)
	keyboard(tmp_path, "event7", enter=False)
	assert confirm.keyboards(str(tmp_path / "class")) == ["/dev/input/event2"]


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
