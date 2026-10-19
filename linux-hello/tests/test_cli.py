import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

pytest.importorskip("dbus_fast")
pytest.importorskip("cv2")

from test_daemon import ME, FakeEngine, private_bus  # noqa: E402,F401

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class WaitingEngine(FakeEngine):
	"""Enrolment that only ends when it is cancelled"""

	def enroll(self, samples, timeout, on_progress, cancelled, enrolled_dims):
		import time
		from verification import VerificationResult

		self.started = True
		deadline = time.monotonic() + 10
		while time.monotonic() < deadline:
			if cancelled():
				self.cancelled = True
				return VerificationResult.CANCELLED, [], None
			time.sleep(0.01)
		return VerificationResult.SUCCESS, [[0.5]], None


@pytest.fixture
def service(fake_paths, private_bus, tmp_path, request):  # noqa: F811
	"""The daemon on a private bus in a thread, polkit replaced by a set"""
	for name in ("paths_factory", "daemon.manager", "linux_hello_daemon"):
		sys.modules.pop(name, None)
	import linux_hello_daemon
	from daemon.access import Access
	from daemon.manager import Manager
	from integration.pam import PamIntegration

	root = str(tmp_path / "pamroot")
	shutil.copytree(os.path.join(FIXTURES, "arch"), root)
	allowed = set()

	class TestAccess(Access):
		async def authorized(self, caller, action):
			return caller.uid == 0 or action in allowed

	engine = WaitingEngine() if request.node.get_closest_marker("waiting_engine") else FakeEngine()
	managers = []

	def factory(access, emit):
		managers.append(Manager(TestAccess(access.bus), emit, engine, PamIntegration(root)))
		return managers[0]

	loop = asyncio.new_event_loop()
	stop = asyncio.Event()
	ready = threading.Event()

	async def main():
		started = asyncio.Event()
		task = asyncio.ensure_future(linux_hello_daemon.serve(
			bus_address=private_bus, idle_timeout=0, stop=stop, ready=started, manager_factory=factory))
		await started.wait()
		ready.set()
		await task

	thread = threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True)
	thread.start()
	assert ready.wait(10)

	def cli(*arguments):
		env = dict(os.environ, LINUX_HELLO_BUS_ADDRESS=private_bus)
		return subprocess.run(
			[sys.executable, os.path.join(SRC, "cli.py")] + list(arguments),
			capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL,
		)

	cli.private_bus = private_bus
	cli.engine = engine
	cli.managers = managers
	yield cli, allowed, root
	loop.call_soon_threadsafe(stop.set)
	thread.join(15)


def test_status_and_doctor(service):
	cli, _allowed, _root = service
	status = cli("status")
	assert status.returncode == 0, status.stderr
	assert "not enrolled" in status.stdout

	doctor = cli("doctor", "--json")
	checks = {c["check"]: c["state"] for c in json.loads(doctor.stdout)}
	assert checks["models"] == "warning"
	# Data files are missing in the test tree, which fails the doctor
	assert doctor.returncode == 1


def test_enrol_and_manage_models(service):
	cli, allowed, _root = service
	denied = cli("add", "office")
	assert denied.returncode == 1
	assert "Not authorized" in denied.stderr

	allowed.add("io.github.linuxhello.enroll-self")
	added = cli("add", "office")
	assert added.returncode == 0, added.stderr
	assert "Added face model 0 for " + ME in added.stdout

	listed = json.loads(cli("list", "--json").stdout)
	assert [m["label"] for m in listed] == ["office"]
	assert cli("rename", "0", "home", "desk").returncode == 0
	assert cli("list", "--plain").stdout.strip().endswith(",home desk")

	assert cli("remove", "0", "-y").returncode == 0
	missing = cli("list")
	assert missing.returncode == 1
	assert "No face model" in missing.stderr


def test_targets_and_settings(service):
	cli, allowed, root = service
	allowed.add("io.github.linuxhello.configure")

	assert cli("enable", "sudo").returncode == 0
	with open(os.path.join(root, "etc/pam.d/sudo")) as file:
		assert "pam_linux_hello.so" in file.read()
	targets = cli("targets").stdout
	assert any(line.startswith("sudo") and " on " in line for line in targets.splitlines())

	assert cli("disable", "sudo").returncode == 0
	with open(os.path.join(root, "etc/pam.d/sudo")) as file:
		assert "pam_linux_hello.so" not in file.read()

	assert cli("set", "certainty", "2.5").returncode == 0
	ambiguous = cli("set", "enabled", "true")
	assert ambiguous.returncode == 1 and "--section" in ambiguous.stderr
	assert cli("set", "--section", "webauthn", "enabled", "true").returncode == 0

	# Old style global switch
	assert cli("disable", "1").returncode == 0
	import settings
	config = settings.load()
	assert config.get("video", "certainty") == "2.5"
	assert config.getboolean("webauthn", "enabled")
	assert config.getboolean("core", "disabled")


def test_service_missing(private_bus):  # noqa: F811
	env = dict(os.environ, LINUX_HELLO_BUS_ADDRESS=private_bus)
	result = subprocess.run(
		[sys.executable, os.path.join(SRC, "cli.py"), "status"],
		capture_output=True, text=True, env=env, timeout=60,
	)
	assert result.returncode == 1
	assert "not installed or failed to start" in result.stderr


def test_root_commands_refuse_users():
	if os.geteuid() == 0:
		pytest.skip("running as root")
	result = subprocess.run(
		[sys.executable, os.path.join(SRC, "cli.py"), "reset-system"],
		capture_output=True, text=True, timeout=60,
	)
	assert result.returncode == 1
	assert "sudo linux-hello-cli reset-system" in result.stderr


@pytest.mark.waiting_engine
def test_abandoned_enrolment_is_not_saved(service):
	import time
	from dbus_fast import Message
	from dbus_fast.aio import MessageBus
	import models as model_store

	cli, allowed, _root = service
	allowed.add("io.github.linuxhello.enroll-self")

	async def start_and_leave():
		bus = await MessageBus(bus_address=cli.private_bus).connect()
		bus.send(Message(
			destination="io.github.LinuxHello1", path="/io/github/LinuxHello1",
			interface="io.github.LinuxHello1.Manager", member="Enroll",
			signature="ss", body=[ME, "abandoned"],
		))
		for _ in range(200):
			if getattr(cli.engine, "started", False):
				break
			await asyncio.sleep(0.01)
		bus.disconnect()

	asyncio.run(start_and_leave())
	for _ in range(300):
		if getattr(cli.engine, "cancelled", False) and not cli.managers[0].running:
			break
		time.sleep(0.01)
	assert cli.engine.cancelled
	assert cli.managers[0].running == {}
	assert model_store.load(ME) == []
