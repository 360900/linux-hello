import asyncio
import getpass
import os
import pwd
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("dbus_fast")
pytest.importorskip("cv2")

from dbus_fast import Message, MessageType  # noqa: E402

ME = getpass.getuser()
MY_UID = os.getuid()
# Any other real account works, the tests never touch its files
OTHER = "nobody"
OTHER_UID = pwd.getpwnam(OTHER).pw_uid
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class FakeAccess:
	"""Access with a fixed answer per polkit action"""

	def __init__(self, allowed=()):
		self.allowed = set(allowed)
		self.asked = []

	async def resolve(self, sender):
		from daemon.dispatch import Caller
		return Caller(sender, MY_UID)

	async def authorized(self, caller, action):
		self.asked.append(action)
		return caller.uid == 0 or action in self.allowed

	async def require(self, caller, action):
		from daemon.dispatch import Failed
		if not await self.authorized(caller, action):
			raise Failed("PermissionDenied", action)

	user_name = staticmethod(lambda uid: pwd.getpwuid(uid).pw_name)

	def is_self(self, caller, user):
		return caller.uid != 0 and self.user_name(caller.uid) == user

	async def require_self_or_admin(self, caller, user, action_self):
		from daemon.access import Access
		await Access.require_self_or_admin(self, caller, user, action_self)


class FakeStats:
	def __init__(self, best=None):
		self.best = best

	def as_dict(self):
		return {"best_certainty": None if self.best is None else round(self.best * 10, 3)}


class FakeEngine:
	def __init__(self):
		self.config = None
		self.verify_calls = []

	def verify(self, encodings, cancelled=None, keep_frames=None, after_match=None):
		from verification import VerificationResult
		self.verify_calls.append(encodings)
		if after_match is not None and after_match(None, None) is False:
			return VerificationResult.RUBBERSTAMP, FakeStats()
		return VerificationResult.SUCCESS, FakeStats(0.21)

	def enroll(self, samples, timeout, on_progress, cancelled, enrolled_dims):
		from verification import VerificationResult
		on_progress({"state": "captured", "sample": 1, "samples": samples, "face": [0.1, 0.2, 0.5, 0.7], "darkness": 3.0})
		return VerificationResult.SUCCESS, [[0.5, 0.5]] * samples, FakeStats()


@pytest.fixture
def env(fake_paths, tmp_path, monkeypatch):
	for name in ("paths_factory", "daemon.manager", "linux_hello_daemon"):
		sys.modules.pop(name, None)
	root = tmp_path / "pamroot"
	shutil.copytree(os.path.join(FIXTURES, "arch"), root)
	return str(root)


def caller(uid):
	from daemon.dispatch import Caller
	return Caller(":1.42", uid)


def make_manager(pam_root, allowed=()):
	from daemon.manager import Manager
	from integration.pam import PamIntegration

	signals = []
	access = FakeAccess(allowed)
	manager = Manager(access, lambda *args: signals.append(args), FakeEngine(), PamIntegration(pam_root))
	return manager, access, signals


def run(coroutine):
	return asyncio.run(coroutine)


# -- access rules --------------------------------------------------------------
def test_enroll_self_needs_own_authorization(env):
	from daemon.dispatch import Failed

	manager, access, _ = make_manager(env)
	with pytest.raises(Failed) as error:
		run(manager.enroll(caller(MY_UID), ME, "office"))
	assert error.value.type.endswith("PermissionDenied")
	assert access.asked == ["io.github.linuxhello.enroll-self"]


def test_enroll_other_user_needs_admin(env):
	from daemon.dispatch import Failed

	manager, access, _ = make_manager(env, allowed={"io.github.linuxhello.enroll-self"})
	with pytest.raises(Failed):
		run(manager.enroll(caller(MY_UID), OTHER, "x"))
	assert access.asked == ["io.github.linuxhello.manage-users"]


def test_enroll_stores_samples_and_signals_caller_only(env):
	import models as model_store

	manager, _, signals = make_manager(env, allowed={"io.github.linuxhello.enroll-self"})
	result, model_id = run(manager.enroll(caller(MY_UID), ME, ""))
	assert result == 0 and model_id == 0

	stored = model_store.load(ME)
	assert stored[0]["label"] == "Face 1"
	assert len(stored[0]["data"]) == 5

	progress = [s for s in signals if s[0] == "EnrollProgress"]
	assert progress and all(s[3] == ":1.42" for s in progress)
	assert progress[0][2][1]["state"].value == "captured"
	assert ("ModelsChanged", "s", [ME]) in signals


def test_verify_only_root_or_self(env):
	from daemon.dispatch import Failed
	import models as model_store

	model_store.add(OTHER, "a", [[0.1]], 1)
	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.manage-users"})
	# Not even an admin may silently run the camera against someone else
	with pytest.raises(Failed):
		run(manager.verify(caller(MY_UID), OTHER))
	assert run(manager.verify(caller(0), OTHER)) == [0, 2.1]


def test_verify_without_models_skips_camera(env):
	manager, _, _ = make_manager(env)
	assert run(manager.verify(caller(MY_UID), ME)) == [10, -1.0]
	assert manager.engine.verify_calls == []


def test_root_cannot_be_enrolled(env):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env)
	with pytest.raises(Failed):
		run(manager.enroll(caller(0), "root", "x"))


def test_list_models_hides_biometrics(env):
	import models as model_store

	model_store.add(ME, "office", [[0.1, 0.2]], 1700000000)
	manager, _, _ = make_manager(env)
	assert run(manager.list_models(caller(MY_UID), ME)) == [[[0, 1700000000, "office"]]]


def test_list_other_users_models_needs_admin(env):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env)
	with pytest.raises(Failed):
		run(manager.list_models(caller(MY_UID), OTHER))


def test_delete_and_rename(env):
	import models as model_store
	from daemon.dispatch import Failed

	model_store.add(ME, "a", [[0.1]], 1)
	manager, _, signals = make_manager(env, allowed={"io.github.linuxhello.enroll-self"})
	run(manager.rename_model(caller(MY_UID), ME, 0, "glasses"))
	assert model_store.load(ME)[0]["label"] == "glasses"
	run(manager.delete_model(caller(MY_UID), ME, 0))
	assert model_store.load(ME) == []
	with pytest.raises(Failed) as error:
		run(manager.delete_model(caller(MY_UID), ME, 0))
	assert error.value.type.endswith("NotFound")
	assert signals.count(("ModelsChanged", "s", [ME])) == 2


# -- system integration ----------------------------------------------------------
def test_targets_need_configure(env):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env)
	with pytest.raises(Failed):
		run(manager.set_target(caller(MY_UID), "sudo", True))

	manager, _, signals = make_manager(env, allowed={"io.github.linuxhello.configure"})
	run(manager.set_target(caller(MY_UID), "sudo", True))
	targets = {t[0]: t for t in run(manager.list_targets(caller(MY_UID)))[0]}
	assert targets["sudo"][4] is True
	assert targets["sudo"][5] == ["sudo"]
	assert ("TargetsChanged", "", []) in signals


def test_unknown_target_is_an_error(env):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	with pytest.raises(Failed):
		run(manager.set_target(caller(MY_UID), "ssh", True))


def test_settings_round_trip(env):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	run(manager.set_setting(caller(MY_UID), "video", "certainty", "2.5"))
	assert run(manager.get_settings(caller(MY_UID)))[0]["video.certainty"] == "2.5"
	with pytest.raises(Failed):
		run(manager.set_setting(caller(MY_UID), "video", "nope", "1"))


def test_diagnose_reports_missing_pieces(env):
	manager, _, _ = make_manager(env)
	checks = {c[0]: c[1] for c in run(manager.diagnose(caller(MY_UID)))[0]}
	assert checks["daemon"] == "ok"
	assert checks["data-files"] == "error"
	assert checks["models"] == "warning"
	assert checks["targets"] == "warning"


def test_cancel_sets_the_callers_event(env):
	import threading

	manager, _, _ = make_manager(env)
	event = threading.Event()
	manager.running[":1.42"] = event
	run(manager.cancel(caller(MY_UID)))
	assert event.is_set()


# -- over a real bus -------------------------------------------------------------
@pytest.fixture
def private_bus():
	if not shutil.which("dbus-daemon"):
		pytest.skip("dbus-daemon not installed")
	process = subprocess.Popen(
		["dbus-daemon", "--session", "--nofork", "--print-address"],
		stdout=subprocess.PIPE, text=True,
	)
	address = process.stdout.readline().strip()
	yield address
	process.terminate()
	process.wait()


def test_service_over_dbus(env, private_bus):
	import linux_hello_daemon
	from dbus_fast.aio import MessageBus
	from daemon.manager import Manager
	from integration.pam import PamIntegration

	def factory(access, emit):
		return Manager(access, emit, FakeEngine(), PamIntegration(env))

	async def scenario():
		stop, ready = asyncio.Event(), asyncio.Event()
		service = asyncio.create_task(linux_hello_daemon.serve(
			bus_address=private_bus, idle_timeout=0, stop=stop, ready=ready, manager_factory=factory))
		await asyncio.wait_for(ready.wait(), 5)

		client = await MessageBus(bus_address=private_bus).connect()

		async def call(member, signature="", body=()):
			return await client.call(Message(
				destination=linux_hello_daemon.BUS_NAME,
				path=linux_hello_daemon.OBJECT_PATH,
				interface=linux_hello_daemon.INTERFACE,
				member=member, signature=signature, body=list(body),
			))

		status = await call("GetStatus")
		assert status.message_type == MessageType.METHOD_RETURN
		assert status.body[0]["user"].value == ME
		assert status.body[0]["user_has_models"].value is False

		verify = await call("Verify", "s", [ME])
		assert verify.body == [10, -1.0]

		targets = await call("ListTargets")
		assert {t[0] for t in targets.body[0]} == {"lock-screen", "admin", "sudo", "login"}

		# No polkit on a private bus: the check fails closed
		denied = await call("SetTarget", "sb", ["sudo", True])
		assert denied.message_type == MessageType.ERROR

		wrong = await call("Verify", "u", [1])
		assert wrong.error_name == "org.freedesktop.DBus.Error.InvalidArgs"

		unknown = await call("Explode")
		assert unknown.error_name == "org.freedesktop.DBus.Error.UnknownMethod"

		introspect = await client.call(Message(
			destination=linux_hello_daemon.BUS_NAME, path=linux_hello_daemon.OBJECT_PATH,
			interface="org.freedesktop.DBus.Introspectable", member="Introspect"))
		assert "io.github.LinuxHello1.Manager" in introspect.body[0]

		client.disconnect()
		stop.set()
		assert await asyncio.wait_for(service, 15) == 0

	asyncio.run(scenario())
	# The sudo target stayed untouched
	with open(os.path.join(env, "etc/pam.d/sudo")) as file:
		assert "linux-hello" not in file.read()


def test_duplicate_pam_modules_are_found(env, tmp_path):
	from daemon.manager import _pam_modules

	usr = tmp_path / "usr/lib/security"
	local = tmp_path / "usr/local/lib/security"
	usr.mkdir(parents=True)
	local.mkdir(parents=True)
	(usr / "pam_linux_hello.so").write_bytes(b"new")
	(tmp_path / "lib").symlink_to(tmp_path / "usr/lib")
	directories = [str(usr), str(tmp_path / "lib/security"), str(local)]

	# /lib/security is the same file through a symlink
	assert _pam_modules(directories) == [str(usr / "pam_linux_hello.so")]
	(local / "pam_linux_hello.so").write_bytes(b"old")
	assert len(_pam_modules(directories)) == 2


def test_rubberstamps_outcome(env, monkeypatch):
	import configparser
	import sys
	import types
	from daemon.manager import _rubberstamps

	config = configparser.ConfigParser()
	config.read_dict({"rubberstamps": {"enabled": "false"}})
	assert _rubberstamps(config, None, None) is True

	config.set("rubberstamps", "enabled", "true")
	backend = types.SimpleNamespace(detector=None)
	for code, accepted in ((0, True), (15, False)):
		def execute(config, ui, opencv, code=code):
			sys.exit(code)
		monkeypatch.setitem(sys.modules, "rubberstamps", types.SimpleNamespace(execute=execute))
		assert _rubberstamps(config, None, backend) is accepted
