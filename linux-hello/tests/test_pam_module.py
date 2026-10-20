import os
import shutil
import subprocess
import time

import pytest

from test_cli import service  # noqa: F401
from test_daemon import ME, private_bus  # noqa: F401

HERE = os.path.dirname(__file__)
MODULE = os.environ.get("LINUX_HELLO_PAM_MODULE", os.path.join(
	HERE, "..", "..", "build-dev", "linux-hello", "src", "pam", "pam_linux_hello.so"))
VENDOR_CONFIG = "/usr/share/linux-hello/config.ini"
MODELS_DIR = "/var/lib/linux-hello/models"


def models_unreadable():
	# The module looks for the model file itself when it can read the models
	# directory (as root) and only asks the service otherwise
	try:
		os.stat(os.path.join(MODELS_DIR, ME + ".dat"))
	except PermissionError:
		return True
	except OSError:
		return False
	return False


MISSING = [reason for missing, reason in (
	(not os.path.isfile(MODULE), "PAM module not built"),
	(not os.path.isfile(VENDOR_CONFIG), "config defaults not installed"),
	(not models_unreadable(), "needs a root-only models directory"),
	(not shutil.which("cc"), "no C compiler"),
) if missing]

# CI sets this: these tests guard against lockouts and must not skip there
if MISSING and os.environ.get("LINUX_HELLO_REQUIRE_PAM_TESTS") == "1":
	raise RuntimeError("PAM module tests cannot run: " + ", ".join(MISSING))

pytestmark = pytest.mark.skipif(bool(MISSING), reason=", ".join(MISSING))


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
	binary = str(tmp_path_factory.mktemp("harness") / "harness")
	subprocess.run(["cc", "-o", binary, os.path.join(HERE, "pam", "harness.c"), "-lpam"], check=True)
	return binary


def stacks(directory):
	module = os.path.realpath(MODULE)
	stacks = {
		# The lock screen's own stack: nothing but a face match succeeds
		"face": "auth [success=ok default=die] {} no_password\n".format(module),
		# A target with the inserted block, followed by a stand-in password check
		"sudo": "auth [success=done default=ignore] {}\nauth required pam_deny.so\n".format(module),
	}
	for name, text in stacks.items():
		with open(os.path.join(directory, name), "w") as file:
			file.write(text)


def authenticate(harness, confdir, service_name, bus_address, answer=None, tty=None):
	env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=bus_address)
	if answer is not None:
		env["HARNESS_ANSWER"] = answer
	if tty is not None:
		env["HARNESS_TTY"] = tty
	started = time.monotonic()
	result = subprocess.run([harness, confdir, service_name, ME], capture_output=True, text=True, env=env, timeout=60)
	return result, time.monotonic() - started


def test_face_match_through_the_service(service, harness, tmp_path):  # noqa: F811
	import models as model_store

	cli, _allowed, _root = service
	model_store.add(ME, "office", [[0.5, 0.5]], 1)
	stacks(str(tmp_path))

	result, _seconds = authenticate(harness, str(tmp_path), "face", cli.private_bus)
	assert result.returncode == 0, result.stdout + result.stderr
	assert "result: Success" in result.stdout


def test_no_model_falls_through_to_the_password(service, harness, tmp_path):  # noqa: F811
	cli, _allowed, _root = service
	stacks(str(tmp_path))

	result, _seconds = authenticate(harness, str(tmp_path), "sudo", cli.private_bus)
	assert result.returncode == 1
	# pam_deny answered, so the module stepped aside instead of failing hard
	assert "result: Authentication failure" in result.stdout


def test_missing_service_fails_fast(harness, tmp_path, private_bus):  # noqa: F811
	stacks(str(tmp_path))
	result, seconds = authenticate(harness, str(tmp_path), "face", private_bus)
	assert result.returncode == 1
	assert seconds < 5


# -- admin prompts and sudo: a face only counts after a physical Enter --------
HINT = "Press Enter to use your face"
# A prompt waiting for typed input; none of these may wait
PROMPT = "message 1:"


def confirming(service, pressed):  # noqa: F811
	cli, _allowed, _root = service
	import models as model_store
	model_store.add(ME, "office", [[0.5, 0.5]], 1)
	cli.managers[0].confirm = lambda cancel: pressed
	return cli


@pytest.mark.as_root
def test_sudo_scans_after_enter(service, harness, tmp_path):  # noqa: F811
	cli = confirming(service, "enter")
	stacks(str(tmp_path))
	result, _seconds = authenticate(harness, str(tmp_path), "sudo", cli.private_bus)
	assert HINT in result.stdout and PROMPT not in result.stdout
	# No terminal here, like a polkit dialog, so Esc is not offered
	assert "Esc" not in result.stdout
	assert "result: Success" in result.stdout


@pytest.mark.as_root
@pytest.mark.parametrize("pressed", ["nothing", "typing", "skip"])
def test_without_enter_sign_in_goes_on(service, harness, tmp_path, pressed):  # noqa: F811
	# Also when a program that started sudo itself answers every prompt
	cli = confirming(service, pressed)
	stacks(str(tmp_path))
	result, _seconds = authenticate(harness, str(tmp_path), "sudo", cli.private_bus, answer="")
	assert PROMPT not in result.stdout
	# pam_deny, the stand-in for the password, had its turn
	assert "result: Authentication failure" in result.stdout
	assert cli.engine.verify_calls == []


@pytest.mark.as_root
def test_lock_screen_needs_no_enter(service, harness, tmp_path):  # noqa: F811
	cli = confirming(service, "nothing")
	stacks(str(tmp_path))
	result, _seconds = authenticate(harness, str(tmp_path), "face", cli.private_bus)
	assert HINT not in result.stdout
	assert "result: Success" in result.stdout


@pytest.mark.as_root
def test_keys_in_the_window_stay_hidden_and_unread(service, harness, tmp_path):  # noqa: F811
	# A password typed out of habit must not show, nor reach the next prompt
	import termios

	main, terminal = os.openpty()
	try:
		def typing(cancel):
			time.sleep(0.3)
			os.write(main, b"secret\n")
			time.sleep(0.2)
			return "typing"

		cli = confirming(service, "nothing")
		cli.managers[0].confirm = typing
		stacks(str(tmp_path))
		result, _seconds = authenticate(harness, str(tmp_path), "sudo", cli.private_bus, tty=os.ttyname(terminal))
		assert HINT + ", or Esc to skip" in result.stdout

		os.set_blocking(main, False)
		try:
			echoed = os.read(main, 1024)
		except BlockingIOError:
			echoed = b""
		assert b"secret" not in echoed
		os.set_blocking(terminal, False)
		with pytest.raises(BlockingIOError):
			os.read(terminal, 1024)
		assert termios.tcgetattr(terminal)[3] & termios.ECHO
	finally:
		os.close(main)
		os.close(terminal)


@pytest.mark.as_root
def test_a_key_the_terminal_passes_on_late_is_not_echoed(service, harness, tmp_path):  # noqa: F811
	# The service reads the keyboard before the terminal app writes the key
	import threading

	main, terminal = os.openpty()
	try:
		def esc(cancel):
			threading.Timer(0.05, lambda: os.write(main, b"\x1b")).start()
			return "skip"

		cli = confirming(service, "skip")
		cli.managers[0].confirm = esc
		stacks(str(tmp_path))
		authenticate(harness, str(tmp_path), "sudo", cli.private_bus, tty=os.ttyname(terminal))
		time.sleep(0.1)

		os.set_blocking(main, False)
		try:
			echoed = os.read(main, 1024)
		except BlockingIOError:
			echoed = b""
		assert b"^[" not in echoed and b"\x1b" not in echoed
	finally:
		os.close(main)
		os.close(terminal)


@pytest.mark.as_root
def test_pause_is_announced(service, harness, tmp_path):  # noqa: F811
	cli = confirming(service, "enter")
	cli.managers[0].confirm_paused[ME] = time.monotonic() + 60
	stacks(str(tmp_path))
	result, _seconds = authenticate(harness, str(tmp_path), "sudo", cli.private_bus)
	assert "Face is paused for 1 minute for security" in result.stdout and HINT not in result.stdout
	assert "result: Authentication failure" in result.stdout

	cli.managers[0].confirm_paused[ME] = time.monotonic() + 4 * 60 + 10
	result, _seconds = authenticate(harness, str(tmp_path), "sudo", cli.private_bus)
	assert "Face is paused for 5 minutes for security" in result.stdout
