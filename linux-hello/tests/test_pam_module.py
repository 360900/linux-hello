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


def authenticate(harness, confdir, service_name, bus_address):
	env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=bus_address)
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
