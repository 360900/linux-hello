import asyncio
import getpass
import os
import pwd
import shutil
import subprocess
import sys
import threading
import time

import numpy as np
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

	# Whether the asking process and the user count as at this computer
	local = True

	async def has_local_session(self, uid, pid):
		return self.local

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

	def verify(self, encodings, cancelled=None, keep_frames=None, after_match=None, on_frame=None):
		from verification import VerificationResult
		self.verify_calls.append(encodings)
		if on_frame is not None:
			on_frame(np.zeros((30, 40), np.uint8))
		if after_match is not None and after_match(None, None) is False:
			return VerificationResult.RUBBERSTAMP, FakeStats()
		return VerificationResult.SUCCESS, FakeStats(0.21)

	def enroll(self, samples, timeout, on_progress, cancelled, enrolled_dims, on_frame=None):
		from verification import VerificationResult
		on_frame(np.zeros((30, 40), np.uint8))
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

	# The picture comes first, so the face box always has one under it.
	# ModelsChanged may come before either: the camera thread hands both
	# to the loop, which can resume enroll first.
	camera = [signal for signal in signals if signal[0] in ("Preview", "EnrollProgress")]
	assert camera[0][:2] == ("Preview", "say") and camera[0][3] == ":1.42"
	user, jpeg = camera[0][2]
	assert user == ME and jpeg[:2] == b"\xff\xd8"
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


def test_camera_picture_only_for_tests_at_the_computer(env):
	import models as model_store

	model_store.add(ME, "a", [[0.1]], 1)
	previews = lambda signals: [s for s in signals if s[0] == "Preview"]

	manager, access, signals = make_manager(env, allowed={"io.github.linuxhello.preview"})
	run(manager.verify_now(caller(MY_UID), ME))
	assert len(previews(signals)) == 1 and signals[0][3] == ":1.42"
	# Unlocking (PAM) never sends the picture anywhere
	run(manager.verify(caller(MY_UID), ME))
	run(manager.verify(caller(0), ME))
	assert len(previews(signals)) == 1

	# Not at the computer, for example over SSH
	manager, access, signals = make_manager(env)
	run(manager.verify_now(caller(MY_UID), ME))
	assert previews(signals) == []
	assert access.asked == ["io.github.linuxhello.preview"]


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


@pytest.fixture
def encrypted(monkeypatch):
	import daemon.manager
	monkeypatch.setattr(daemon.manager, "_home_encrypted", lambda user: True)


def test_auto_login_logs_in_who_turned_it_on(env, encrypted):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	run(manager.set_target(caller(MY_UID), "auto-login", True))
	with open(os.path.join(env, "etc/plasmalogin.conf.d/linux-hello.conf")) as file:
		assert "User={}\n".format(ME) in file.read()
	# Not root, through sudo
	run(manager.set_target(caller(MY_UID), "auto-login", False))
	with pytest.raises(Failed):
		run(manager.set_target(caller(0), "auto-login", True))


def test_auto_login_needs_an_encrypted_disk(env):
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	with pytest.raises(Failed):
		run(manager.set_target(caller(MY_UID), "auto-login", True))
	assert not os.path.exists(os.path.join(env, "etc/plasmalogin.conf.d/linux-hello.conf"))


def test_auto_login_on_a_plain_disk_when_allowed(env):
	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	run(manager.set_setting(caller(0), "core", "insecure_auto_login", "true"))
	run(manager.set_target(caller(MY_UID), "auto-login", True))


def test_login_password_kept_only_for_auto_login_and_root(env, encrypted):
	from daemon import secrets
	from daemon.dispatch import Failed

	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	# Auto-login off: dropped
	run(manager.remember_login_secret(caller(0), ME, b"hunter2"))
	assert not secrets.exists(ME)
	assert run(manager.login_secret(caller(0), ME)) == [b""]

	run(manager.set_target(caller(MY_UID), "auto-login", True))
	assert run(manager.get_status(caller(MY_UID)))[0]["login_secret_missing"].value
	with pytest.raises(Failed):
		run(manager.remember_login_secret(caller(MY_UID), ME, b"hunter2"))
	run(manager.remember_login_secret(caller(0), ME, b"hunter2"))
	assert not run(manager.get_status(caller(MY_UID)))[0]["login_secret_missing"].value
	with pytest.raises(Failed):
		run(manager.login_secret(caller(MY_UID), ME))
	assert run(manager.login_secret(caller(0), ME)) == [b"hunter2"]

	# Turning it off forgets it
	run(manager.set_target(caller(MY_UID), "auto-login", False))
	assert not secrets.exists(ME)


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
		assert {t[0] for t in targets.body[0]} == {"lock-screen", "admin", "sudo", "login", "auto-login"}

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


@pytest.mark.parametrize("starting_with_face, kept", [([], False), (["alice"], True)])
def test_disabling_lock_screen_protects_users_starting_with_face(env, monkeypatch, starting_with_face, kept):
	from integration import kscreenlocker, pam

	monkeypatch.setattr(kscreenlocker, "users_preferring_face", lambda: starting_with_face)
	manager, _, _ = make_manager(env, allowed={"io.github.linuxhello.configure"})
	run(manager.set_target(caller(MY_UID), "lock-screen", True))
	run(manager.set_target(caller(MY_UID), "lock-screen", False))

	assert not os.path.exists(os.path.join(env, "etc/pam.d/kde-face"))
	assert os.path.exists(os.path.join(env, pam.KSCREENLOCKER_CONFIG)) is kept


class ScriptedEngine(FakeEngine):
	"""Answers verify with the given results in turn"""

	def __init__(self, results):
		super().__init__()
		self.results = list(results)

	def verify(self, encodings, cancelled=None, keep_frames=None, after_match=None, on_frame=None):
		self.verify_calls.append(encodings)
		return self.results.pop(0), FakeStats()


def make_scripted(env, results):
	from daemon.manager import Manager
	from integration.pam import PamIntegration

	engine = ScriptedEngine(results)
	return Manager(FakeAccess(), lambda *args: None, engine, PamIntegration(env)), engine


def test_lock_screen_pauses_after_failed_scans(env, monkeypatch):
	import models as model_store
	from daemon import presence
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine = make_scripted(env, [R.TIMEOUT_REACHED] * 3 + [R.SUCCESS])
	waits = []
	answers = [False, True]
	monkeypatch.setattr(presence, "wait_for_activity", lambda cancel, timeout: waits.append(timeout) or answers.pop(0))

	for _ in range(3):
		assert run(manager.verify(caller(MY_UID), ME))[0] == R.TIMEOUT_REACHED
	assert len(engine.verify_calls) == 3

	# Nobody came back: no scan, the camera stays off
	assert run(manager.verify(caller(MY_UID), ME))[0] == R.TIMEOUT_REACHED
	assert len(engine.verify_calls) == 3
	assert waits == [25.0]

	# Input activity: scanning resumes with a fresh streak
	assert run(manager.verify(caller(MY_UID), ME))[0] == R.SUCCESS
	assert len(engine.verify_calls) == 4
	assert manager.failures == {}


def test_verify_now_neither_waits_nor_counts(env, monkeypatch):
	import models as model_store
	from daemon import presence
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine = make_scripted(env, [R.TIMEOUT_REACHED] * 7)
	monkeypatch.setattr(presence, "wait_for_activity", lambda cancel, timeout: pytest.fail("paused a test"))

	# Failed tests leave the lock screen's streak alone
	for _ in range(3):
		assert run(manager.verify_now(caller(MY_UID), ME))[0] == R.TIMEOUT_REACHED
	assert manager.failures == {}

	# And a test still scans while the lock screen is paused
	for _ in range(3):
		run(manager.verify(caller(MY_UID), ME))
	assert run(manager.verify_now(caller(MY_UID), ME))[0] == R.TIMEOUT_REACHED
	assert len(engine.verify_calls) == 7


def make_confirming(env, answers, results=()):
	"""A manager whose keyboard watch sees answers in turn"""
	from daemon.manager import Manager
	from integration.pam import PamIntegration

	engine = ScriptedEngine(results)
	signals = []
	watches = []

	def confirm(cancel):
		watches.append(cancel)
		return answers.pop(0)

	manager = Manager(FakeAccess(), lambda *args: signals.append(args), engine, PamIntegration(env), confirm)
	return manager, engine, watches, signals


def test_confirmed_verification_is_root_only(env):
	from daemon.dispatch import Failed
	import models as model_store

	model_store.add(ME, "a", [[0.1]], 1)
	manager, _engine, watches, _signals = make_confirming(env, ["enter"])
	with pytest.raises(Failed) as error:
		run(manager.verify_confirmed(caller(MY_UID), ME, 0))
	assert error.value.type.endswith("PermissionDenied")
	assert watches == []


def test_scan_only_after_enter(env):
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine, watches, signals = make_confirming(env, ["enter"], [R.SUCCESS])
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.SUCCESS
	assert len(watches) == 1 and len(engine.verify_calls) == 1
	# The module asks for Enter only once the service listens for it
	assert signals == [("ConfirmWindow", "s", [ME], ":1.42")]


def test_running_out_three_times_pauses_face_for_elevation(env):
	import models as model_store
	from daemon import manager as manager_module
	from verification import VerificationResult as R

	for nobody_pressed in ("nothing", "typing"):
		model_store.add(ME, "a", [[0.1]], 1)
		manager, engine, watches, signals = make_confirming(env, [nobody_pressed] * 3 + ["enter"], [R.SUCCESS, R.SUCCESS])
		for _ in range(3):
			assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.NOT_CONFIRMED
		# A program trying again right away gets no scan, no keyboard watch
		# and no window to show
		assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.CONFIRM_PAUSED
		assert len(watches) == 3 and len(signals) == 3 and engine.verify_calls == []
		# Unlocking and tests are not affected
		assert run(manager.verify(caller(0), ME))[0] == R.SUCCESS

		# A minute later
		assert manager.confirm_paused[ME] - time.monotonic() > manager_module.CONFIRM_PAUSE_SECONDS - 5
		manager.confirm_paused[ME] = time.monotonic()
		assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.SUCCESS


def test_pauses_grow_until_enter_is_used(env):
	import models as model_store
	from daemon import manager as manager_module
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, _engine, _watches, _signals = make_confirming(env, [], [R.SUCCESS] * 2)
	manager.confirm = lambda cancel: "nothing"

	def pause_after_three_misses():
		manager.confirm_paused.pop(ME, None)
		for _ in range(3):
			assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.NOT_CONFIRMED
		result, seconds_left = run(manager.verify_confirmed(caller(0), ME, 0))
		assert result == R.CONFIRM_PAUSED
		return round(seconds_left / 60)

	assert [pause_after_three_misses() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
	assert manager_module.CONFIRM_PAUSE_MAX_SECONDS == 3600

	# A person using face with Enter starts over at a minute
	manager.confirm_paused.pop(ME, None)
	manager.confirm = lambda cancel: "enter"
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.SUCCESS
	manager.confirm = lambda cancel: "nothing"
	assert pause_after_three_misses() == 1


def test_letting_the_window_pass_now_and_then_never_pauses(env):
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, _engine, _watches, _signals = make_confirming(env, ["nothing", "nothing", "enter", "nothing", "nothing"], [R.SUCCESS])
	results = [run(manager.verify_confirmed(caller(0), ME, 0))[0] for _ in range(5)]
	# Enter in between starts the count over
	assert results == [R.NOT_CONFIRMED, R.NOT_CONFIRMED, R.SUCCESS, R.NOT_CONFIRMED, R.NOT_CONFIRMED]

	# Misses older than the pause do not count
	manager.confirm_misses[ME] = [time.monotonic() - 120] * 2
	manager.confirm = lambda cancel: "nothing"
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.NOT_CONFIRMED
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.NOT_CONFIRMED


def test_killed_requests_count_too(env):
	# A program starts sudo and kills it before the window runs out, again
	# and again, hoping to keep a window open for a lone Enter
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine, watches, _signals = make_confirming(env, [])

	def killed(cancel):
		watches.append(cancel)
		cancel.set()
		return "nothing"

	manager.confirm = killed
	for _ in range(3):
		run(manager.verify_confirmed(caller(0), ME, 0))
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.CONFIRM_PAUSED
	assert len(watches) == 3 and engine.verify_calls == []


def test_only_the_user_at_this_computer(env):
	# Another account (pkexec choosing this user as admin) or a remote
	# session gets nothing, and cannot pause the user's face either
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine, watches, signals = make_confirming(env, ["enter"] * 2, [R.SUCCESS])
	# pid 1 belongs to root, not to the user
	assert run(manager.verify_confirmed(caller(0), ME, 1))[0] == R.NOT_LOCAL
	manager.access.local = False
	for _ in range(5):
		assert run(manager.verify_confirmed(caller(0), ME, os.getpid()))[0] == R.NOT_LOCAL
	assert watches == [] and signals == [] and manager.confirm_paused == {}

	manager.access.local = True
	assert run(manager.verify_confirmed(caller(0), ME, os.getpid()))[0] == R.SUCCESS


def test_one_enter_never_approves_two_requests(env):
	# A program starts its own sudo right next to the user's real one
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine, _watches, _signals = make_confirming(env, [], [R.SUCCESS] * 2)
	started = threading.Event()

	def enter_after_the_second_arrives(cancel):
		started.set()
		time.sleep(0.2)
		return "enter"

	manager.confirm = enter_after_the_second_arrives

	async def both():
		first = asyncio.ensure_future(manager.verify_confirmed(caller(0), ME, 0))
		await asyncio.to_thread(started.wait)
		second = await manager.verify_confirmed(caller(0), ME, 0)
		return (await first)[0], second[0]

	assert run(both()) == (R.CONFIRM_CONFLICT, R.CONFIRM_CONFLICT)
	assert engine.verify_calls == []
	# Afterwards a single request works again
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.SUCCESS


def test_esc_skips_without_pausing(env):
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine, _watches, _signals = make_confirming(env, ["skip", "enter"], [R.SUCCESS])
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.SKIPPED
	assert engine.verify_calls == []
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.SUCCESS


def test_no_face_no_keyboard_watch(env):
	from verification import VerificationResult as R

	manager, _engine, watches, signals = make_confirming(env, ["enter"])
	assert run(manager.verify_confirmed(caller(0), ME, 0))[0] == R.NO_FACE_MODEL
	assert watches == [] and signals == []


def test_root_callers_never_pause(env, monkeypatch):
	import models as model_store
	from daemon import presence
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, engine = make_scripted(env, [R.TIMEOUT_REACHED] * 5)
	monkeypatch.setattr(presence, "wait_for_activity", lambda cancel, timeout: pytest.fail("paused sudo"))
	for _ in range(5):
		run(manager.verify(caller(0), ME))
	assert len(engine.verify_calls) == 5


def test_success_and_time_reset_the_streak(env, monkeypatch):
	import models as model_store
	from verification import VerificationResult as R

	model_store.add(ME, "a", [[0.1]], 1)
	manager, _engine = make_scripted(env, [R.TIMEOUT_REACHED, R.TIMEOUT_REACHED, R.SUCCESS, R.TIMEOUT_REACHED])
	manager_module = sys.modules[type(manager).__module__]
	for _ in range(3):
		run(manager.verify(caller(MY_UID), ME))
	assert manager._streak(ME) == 0

	run(manager.verify(caller(MY_UID), ME))
	assert manager._streak(ME) == 1
	monkeypatch.setattr(manager_module, "STREAK_SECONDS", -1)
	assert manager._streak(ME) == 0


def test_local_session_rules():
	from daemon.access import Access

	def logind(sessions, by_pid=None):
		"""An Access answering from a fake logind: path -> (uid, seat, active, remote)"""
		access = Access(None)

		async def call(_destination, path, _interface, member, _signature, body):
			if member == "GetSessionByPID":
				return None if by_pid is None else [by_pid]
			if member == "ListSessions":
				return [[(path, uid, "u", seat, path) for path, (uid, seat, _a, _r) in sessions.items()]]
			uid, seat, active, remote = sessions[path]
			return [{"Active": Variant("b", active), "Remote": Variant("b", remote)}]

		access._call_or_none = call
		return access

	from dbus_fast import Variant
	desk = {"/s/1": (1000, "seat0", True, False)}
	# A terminal app on Plasma: outside any session, the user is at the desk
	assert run(logind(desk).has_local_session(1000, 4242))
	# Nobody at the desk: only an ssh session, or a desk session of someone else
	assert not run(logind({"/s/2": (1000, "", True, True)}).has_local_session(1000, 4242))
	assert not run(logind({"/s/1": (1001, "seat0", True, False)}).has_local_session(1000, 4242))
	# Switched away (another user's session in front)
	assert not run(logind({"/s/1": (1000, "seat0", False, False)}).has_local_session(1000, 4242))
	# Asked from an ssh session while the user is also at the desk
	both = dict(desk, **{"/s/2": (1000, "", True, True)})
	assert not run(logind(both, by_pid="/s/2").has_local_session(1000, 4242))
	assert run(logind(both, by_pid="/s/1").has_local_session(1000, 4242))
