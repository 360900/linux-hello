# io.github.LinuxHello1.Manager: the daemon's API.
#
# Camera work (enrol, verify, camera probing) blocks, so it runs in a worker
# thread, one request at a time. Everything else is quick file access.

import asyncio
import logging
import os
import threading
import time

import cv2
from dbus_fast import Variant

import models as model_store
import paths_factory
import settings
from daemon import confirm as confirm_module
from daemon.access import ACTION_CONFIGURE, ACTION_ENROLL_SELF, ACTION_MANAGE_USERS, ACTION_PREVIEW
from daemon.dispatch import Failed
from engine import Engine
from integration.pam import PamError, PamIntegration
from verification import VerificationResult

log = logging.getLogger("linux-hello.manager")

ENROLL_SAMPLES = 5
ENROLL_TIMEOUT = 30.0

# The lock screen retries a failed scan at once. After this many failed
# scans in a row the camera stays off until there is input activity.
PAUSE_AFTER = 3
# A failure later than this after the previous one starts a new streak
STREAK_SECONDS = 30.0
# Longest wait per request, below the PAM module's call timeout
PAUSE_SECONDS = 25.0

# After this many admin prompt or sudo requests ran out without Enter within
# CONFIRM_PAUSE_SECONDS, such requests get no face scan for that long. A
# program trying again and again in the background hoping for a lone Enter
# gets a few tries every minute or two; a person letting the window pass to
# use a fingerprint does not get there. Esc only comes from a person, so it
# counts for nothing.
CONFIRM_MISSES = 3
CONFIRM_PAUSE_SECONDS = 60.0

# The picture the settings page shows while scanning
PREVIEW_FPS = 12
PREVIEW_QUALITY = 70


class Manager:
	def __init__(self, access, emit, engine=None, pam=None, confirm=None):
		self.access = access
		self.emit = emit
		self.engine = engine or Engine(settings.load())
		self.pam = pam or PamIntegration()
		self.camera_lock = asyncio.Lock()
		# sender -> threading.Event of the request it may cancel
		self.running = {}
		# user -> (failed scans in a row, monotonic time of the last one)
		self.failures = {}
		# (cancel event) -> what a person pressed, see daemon/confirm.py
		self.confirm = confirm or confirm_module.wait_for_enter
		# user -> monotonic times of recent requests that ran out without Enter
		self.confirm_misses = {}
		# user -> monotonic time until which face is paused for elevation
		self.confirm_paused = {}

	def methods(self):
		return {
			"GetStatus": ("", "a{sv}", self.get_status),
			"Verify": ("s", "ud", self.verify),
			"VerifyNow": ("s", "ud", self.verify_now),
			"VerifyConfirmed": ("s", "ud", self.verify_confirmed),
			"Enroll": ("ss", "uu", self.enroll),
			"Cancel": ("", "", self.cancel),
			"ListModels": ("s", "a(uxs)", self.list_models),
			"RenameModel": ("sus", "", self.rename_model),
			"DeleteModel": ("su", "", self.delete_model),
			"ListUsers": ("", "as", self.list_users),
			"ListTargets": ("", "a(sssbbas)", self.list_targets),
			"SetTarget": ("sb", "", self.set_target),
			"GetSettings": ("", "a{ss}", self.get_settings),
			"SetSetting": ("sss", "", self.set_setting),
			"ListCameras": ("", "a(sssbd)", self.list_cameras),
			"Diagnose": ("", "a(sss)", self.diagnose),
		}

	# -- helpers -------------------------------------------------------------
	def _check_user(self, user):
		if not model_store.user_exists(user):
			raise Failed("InvalidArgs", "No such user: {}".format(user))
		if user == "root":
			raise Failed("InvalidArgs", "Face unlock is not available for root")

	async def _camera(self, caller, work):
		"""Run blocking camera work in a thread, cancellable by the caller"""
		cancel = threading.Event()
		# Registered before waiting for the camera, so a caller that leaves
		# while queued is cancelled too
		self.running[caller.sender] = cancel
		try:
			async with self.camera_lock:
				if cancel.is_set():
					raise Failed("Cancelled", "Cancelled before the camera was free")
				self.engine.config = settings.load()
				return await asyncio.to_thread(work, cancel)
		finally:
			self.running.pop(caller.sender, None)

	def _preview(self, caller, user):
		"""on_frame callback sending the caller what the camera sees, as JPEG"""
		loop = asyncio.get_running_loop()
		last = [float("-inf")]

		def send(gsframe):
			now = time.monotonic()
			if now - last[0] < 1 / PREVIEW_FPS:
				return
			last[0] = now
			ok, data = cv2.imencode(".jpg", gsframe, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY])
			if ok:
				loop.call_soon_threadsafe(self.emit, "Preview", "say", [user, data.tobytes()], caller.sender)

		return send

	# -- status ----------------------------------------------------------------
	async def get_status(self, caller):
		config = settings.load()
		user = self.access.user_name(caller.uid)
		has_models = False
		if user and user != "root":
			try:
				has_models = bool(model_store.load(user))
			except model_store.ModelError:
				has_models = False
		return [{
			"version": Variant("s", paths_factory.version()),
			"disabled": Variant("b", config.getboolean("core", "disabled", fallback=False)),
			"device": Variant("s", config.get("video", "device_path", fallback="none")),
			"data_files": Variant("b", _dlib_data_present()),
			"user": Variant("s", user or ""),
			"user_has_models": Variant("b", has_models),
			"busy": Variant("b", self.camera_lock.locked()),
		}]

	# -- verification ----------------------------------------------------------
	async def verify(self, caller, user, pausable=True, preview=False):
		# Root (the PAM module in sudo, polkit, the login screen) or the user
		# for themselves (the PAM module in the lock screen greeter)
		if caller.uid != 0 and not self.access.is_self(caller, user):
			raise Failed("PermissionDenied", "Only root or {} may verify {}".format(user, user))
		self._check_user(user)
		try:
			encodings = [e for m in model_store.load(user) for e in m["data"]]
		except model_store.ModelError:
			encodings = []
		if not encodings:
			return [int(VerificationResult.NO_FACE_MODEL), -1.0]

		# Callers other than root verifying themselves are the lock screen,
		# which keeps retrying while nobody is in front of the camera
		lock_screen = pausable and caller.uid != 0
		if lock_screen and self._streak(user) >= PAUSE_AFTER:
			resumed, cancelled = await self._wait_for_activity(caller, user)
			if not resumed:
				result = VerificationResult.CANCELLED if cancelled else VerificationResult.TIMEOUT_REACHED
				return [int(result), -1.0]
			self.failures.pop(user, None)

		on_frame = self._preview(caller, user) if preview else None

		def work(cancel):
			config = self.engine.config
			save_failed = config.getboolean("snapshots", "save_failed", fallback=False)
			save_successful = config.getboolean("snapshots", "save_successful", fallback=False)
			frames = [] if (save_failed or save_successful) else None
			result, stats = self.engine.verify(
				encodings,
				cancelled=cancel.is_set,
				keep_frames=frames,
				after_match=lambda capture, backend: _rubberstamps(config, capture, backend),
				on_frame=on_frame,
			)
			if (result == VerificationResult.SUCCESS and save_successful) or (
					result in (VerificationResult.TIMEOUT_REACHED, VerificationResult.TOO_DARK) and save_failed):
				_snapshot(frames, "SUCCESSFUL" if result == VerificationResult.SUCCESS else "FAILED", stats)
			return result, stats

		result, stats = await self._camera(caller, work)
		if lock_screen:
			if result == VerificationResult.SUCCESS:
				self.failures.pop(user, None)
			elif result in (VerificationResult.TIMEOUT_REACHED, VerificationResult.TOO_DARK):
				self.failures[user] = (self._streak(user) + 1, time.monotonic())
		certainty = stats.as_dict()["best_certainty"]
		log.info("uid %d: verified %s: %s, best certainty %s", caller.uid, user, VerificationResult(result).name, certainty)
		return [int(result), -1.0 if certainty is None else float(certainty)]

	async def verify_now(self, caller, user):
		# Started by the user (a test in the settings page, a passkey prompt):
		# never waits for input activity and does not count as a failed unlock.
		# Only someone at the computer gets to see the camera picture.
		preview = await self.access.authorized(caller, ACTION_PREVIEW)
		return await self.verify(caller, user, pausable=False, preview=preview)

	async def verify_confirmed(self, caller, user):
		# Admin prompts and sudo, from the PAM module running as root in sudo
		# or polkit's helper: the scan starts only after a person pressed
		# Enter, see daemon/confirm.py. ConfirmWindow tells the caller when
		# to ask for it.
		if caller.uid != 0:
			raise Failed("PermissionDenied", "Only root may ask for a confirmed verification")
		self._check_user(user)
		try:
			has_models = bool(model_store.load(user))
		except model_store.ModelError:
			has_models = False
		if not has_models:
			return [int(VerificationResult.NO_FACE_MODEL), -1.0]
		if time.monotonic() < self.confirm_paused.get(user, float("-inf")):
			return [int(VerificationResult.CONFIRM_PAUSED), -1.0]

		cancel = threading.Event()
		self.running[caller.sender] = cancel
		try:
			self.emit("ConfirmWindow", "s", [user], caller.sender)
			pressed = await asyncio.to_thread(self.confirm, cancel)
		finally:
			self.running.pop(caller.sender, None)
		if pressed == confirm_module.SKIP:
			return [int(VerificationResult.SKIPPED), -1.0]
		if pressed != confirm_module.ENTER:
			# Also when the caller left: a program starting and killing sudo
			# over and over must not keep a window open without counting
			self._confirm_missed(user)
			return [int(VerificationResult.NOT_CONFIRMED), -1.0]
		if cancel.is_set():
			return [int(VerificationResult.CANCELLED), -1.0]
		self.confirm_misses.pop(user, None)
		return await self.verify(caller, user, pausable=False)

	def _confirm_missed(self, user):
		now = time.monotonic()
		misses = [t for t in self.confirm_misses.get(user, []) if now - t < CONFIRM_PAUSE_SECONDS] + [now]
		if len(misses) < CONFIRM_MISSES:
			self.confirm_misses[user] = misses
			return
		self.confirm_misses.pop(user, None)
		self.confirm_paused[user] = now + CONFIRM_PAUSE_SECONDS
		log.info("%d elevation requests for %s ended without Enter, no face scans for them for %ds",
			CONFIRM_MISSES, user, CONFIRM_PAUSE_SECONDS)

	def _streak(self, user):
		count, last = self.failures.get(user, (0, 0.0))
		if time.monotonic() - last > STREAK_SECONDS:
			return 0
		return count

	async def _wait_for_activity(self, caller, user):
		"""(resumed, cancelled): wait with the camera off until input arrives"""
		from daemon import presence

		log.info("Pausing face scans for %s until input activity", user)
		cancel = threading.Event()
		self.running[caller.sender] = cancel
		try:
			resumed = await asyncio.to_thread(presence.wait_for_activity, cancel, PAUSE_SECONDS)
		finally:
			self.running.pop(caller.sender, None)
		if not resumed and not cancel.is_set():
			# Still away: keep the streak alive for the lock screen's next try
			count, _last = self.failures.get(user, (PAUSE_AFTER, 0.0))
			self.failures[user] = (count, time.monotonic())
		elif resumed:
			log.info("Input activity, resuming face scans for %s", user)
		return resumed, cancel.is_set()

	# -- enrolment -------------------------------------------------------------
	async def enroll(self, caller, user, label):
		self._check_user(user)
		await self.access.require_self_or_admin(caller, user, ACTION_ENROLL_SELF)
		existing = model_store.load(user)
		dims = {len(e) for m in existing for e in m["data"]}
		loop = asyncio.get_running_loop()

		def progress(update):
			body = {
				"state": Variant("s", update["state"]),
				"sample": Variant("u", update["sample"]),
				"samples": Variant("u", update["samples"]),
			}
			if update["face"] is not None:
				body["face"] = Variant("ad", [float(v) for v in update["face"]])
			if update["darkness"] is not None:
				body["darkness"] = Variant("d", float(update["darkness"]))
			loop.call_soon_threadsafe(self.emit, "EnrollProgress", "sa{sv}", [user, body], caller.sender)

		# Enrolling needed the caller's password at the computer already
		on_frame = self._preview(caller, user)

		def work(cancel):
			return self.engine.enroll(
				samples=ENROLL_SAMPLES,
				timeout=ENROLL_TIMEOUT,
				on_progress=progress,
				cancelled=cancel.is_set,
				enrolled_dims=dims or None,
				on_frame=on_frame,
			)

		result, encodings, _stats = await self._camera(caller, work)
		if result != VerificationResult.SUCCESS:
			log.info("uid %d: enrolment for %s ended with %s", caller.uid, user, VerificationResult(result).name)
			return [int(result), 0]

		if not label.strip():
			label = "Face {}".format(len(existing) + 1)
		model = model_store.add(user, label, encodings, time.time())
		log.info("uid %d: added face model %d for %s", caller.uid, model["id"], user)
		self.emit("ModelsChanged", "s", [user])
		return [int(result), model["id"]]

	async def cancel(self, caller):
		event = self.running.get(caller.sender)
		if event is not None:
			event.set()
		return []

	# -- models ----------------------------------------------------------------
	async def list_models(self, caller, user):
		self._check_user(user)
		await self.access.require_self_or_admin(caller, user, None)
		return [[[m["id"], m.get("time", 0), m.get("label", "")] for m in model_store.load(user)]]

	async def rename_model(self, caller, user, model_id, label):
		self._check_user(user)
		await self.access.require_self_or_admin(caller, user, ACTION_ENROLL_SELF)
		if not model_store.rename(user, model_id, label):
			raise Failed("NotFound", "No model {} for {}".format(model_id, user))
		log.info("uid %d: renamed face model %d of %s", caller.uid, model_id, user)
		self.emit("ModelsChanged", "s", [user])
		return []

	async def delete_model(self, caller, user, model_id):
		self._check_user(user)
		await self.access.require_self_or_admin(caller, user, ACTION_ENROLL_SELF)
		if not model_store.remove(user, model_id):
			raise Failed("NotFound", "No model {} for {}".format(model_id, user))
		log.info("uid %d: removed face model %d of %s", caller.uid, model_id, user)
		self.emit("ModelsChanged", "s", [user])
		return []

	async def list_users(self, caller):
		await self.access.require(caller, ACTION_MANAGE_USERS)
		return [model_store.users()]

	# -- system integration ----------------------------------------------------
	async def list_targets(self, caller):
		return [[
			[t["id"], t["label"], t["description"], t["available"], t["enabled"], t["services"]]
			for t in self.pam.targets()
		]]

	async def set_target(self, caller, target, enabled):
		await self.access.require(caller, ACTION_CONFIGURE)
		try:
			if enabled:
				self.pam.enable(target)
			else:
				keep = []
				if target == "lock-screen":
					from integration import kscreenlocker
					keep = kscreenlocker.users_preferring_face()
				if keep:
					log.warning("Keeping the lock screen's Face option: %s still start with it", ", ".join(keep))
				self.pam.disable(target, keep_kde_config=bool(keep))
		except PamError as error:
			log.warning("uid %d: could not %s %s: %s", caller.uid, "enable" if enabled else "disable", target, error)
			raise Failed("Failed", str(error))
		log.info("uid %d: %s face unlock for %s", caller.uid, "enabled" if enabled else "disabled", target)
		self.emit("TargetsChanged", "", [])
		return []

	# -- settings --------------------------------------------------------------
	async def get_settings(self, caller):
		config = settings.load()
		known = settings.defaults()
		values = {}
		for section in known.sections():
			for key in known.options(section):
				values[section + "." + key] = config.get(section, key, fallback="")
		return [values]

	async def set_setting(self, caller, section, key, value):
		await self.access.require(caller, ACTION_CONFIGURE)
		try:
			settings.set_value(key, value, section=section)
		except settings.SettingsError as error:
			raise Failed("InvalidArgs", str(error))
		log.info("uid %d: set %s.%s = %s", caller.uid, section, key, value)
		self.emit("SettingsChanged", "", [])
		return []

	# -- cameras ---------------------------------------------------------------
	async def list_cameras(self, caller):
		# Probing opens every camera, so it is not for anyone to trigger
		await self.access.require(caller, ACTION_CONFIGURE)
		from recorders.device_discovery import discover_cameras

		def work(_cancel):
			return discover_cameras()

		cameras = []
		for result in await self._camera(caller, work):
			if not result.get("ok"):
				continue
			cameras.append([
				result["path"],
				result["node"],
				_camera_name(result["node"]),
				bool(result.get("infrared")),
				float(result.get("brightness", 0.0)),
			])
		return [cameras]

	# -- diagnostics -----------------------------------------------------------
	async def diagnose(self, caller):
		checks = []

		def add(check, state, message):
			checks.append([check, state, message])

		config = settings.load()
		add("daemon", "ok", "Linux Hello service {} is running".format(paths_factory.version()))

		if _dlib_data_present():
			add("data-files", "ok", "Face recognition data is installed")
		else:
			add("data-files", "error", "Face recognition data is missing in {}".format(paths_factory.dlib_data_dir_path()))

		device = config.get("video", "device_path", fallback="none")
		if device in ("none", "", "auto"):
			from recorders.device_discovery import candidate_nodes
			if candidate_nodes():
				add("camera", "ok", "Camera picked automatically")
			else:
				add("camera", "error", "No camera found")
		elif os.path.exists(device):
			add("camera", "ok", "Camera {}".format(device))
		else:
			add("camera", "error", "Configured camera {} is missing".format(device))

		user = self.access.user_name(caller.uid)
		if user and user != "root":
			try:
				count = len(model_store.load(user))
			except model_store.ModelError:
				count = -1
			if count > 0:
				add("models", "ok", "{} face model(s) for {}".format(count, user))
			elif count == 0:
				add("models", "warning", "No face enrolled for {}".format(user))
			else:
				add("models", "error", "Face models of {} are unreadable".format(user))

		if config.getboolean("core", "disabled", fallback=False):
			add("enabled", "warning", "Face unlock is turned off in the settings")

		targets = [t for t in self.pam.targets() if t["enabled"]]
		if targets:
			add("targets", "ok", "Used for: " + ", ".join(t["label"] for t in targets))
		else:
			add("targets", "warning", "Face unlock is not enabled anywhere yet")

		ir_nodes = _ir_nodes()
		if ir_nodes:
			import emitter
			state = emitter.status(ir_nodes[0])
			if state.get("method"):
				add("emitter", "ok", "IR light switched on by {}".format(state["method"]))
			elif config.get("video", "ir_emitter", fallback="auto").strip().lower() == "off":
				add("emitter", "info", "IR light control is turned off in the settings")
			else:
				add("emitter", "warning", "Linux Hello does not know how to switch on this camera's IR light. "
					"It may work in daylight only. Find the setting with: sudo linux-hello-cli emitter search")

		modules = _pam_modules()
		if len(modules) > 1:
			add("duplicates", "warning", "Several PAM modules installed, remove the stale ones: " + ", ".join(modules))

		if _selinux_enforcing():
			add("selinux", "info", "SELinux is enforcing; if the camera fails, check the audit log")
		return [checks]


def _rubberstamps(config, capture, backend):
	"""Extra checks after a match, such as a nod. True to accept the match."""
	if not config.getboolean("rubberstamps", "enabled", fallback=False):
		return True
	import cv2
	import rubberstamps

	try:
		rubberstamps.execute(config, None, {
			"video_capture": capture,
			"face_detector": backend.detector,
			"pose_predictor": getattr(backend, "pose_predictor", None),
			"clahe": cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)),
		})
	except SystemExit as outcome:
		# execute() ends with sys.exit(0) when every stamp passed
		return outcome.code in (0, None)
	return True


def _snapshot(frames, kind, stats):
	"""Save a picture of the attempt to the snapshots directory"""
	from datetime import datetime, timezone

	import snapshot

	if not frames:
		return
	summary = stats.as_dict()
	try:
		snapshot.generate(frames, [
			kind + " LOGIN",
			"Date: " + datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S UTC"),
			"Scan time: " + str(summary["seconds"]) + "s",
			"Frames: " + str(stats.frames) + " (" + str(summary["fps"]) + "FPS)",
			"Hostname: " + os.uname().nodename,
			"Best certainty value: " + str(summary["best_certainty"]),
		])
	except OSError as error:
		log.warning("Could not save a snapshot: %s", error)


def _dlib_data_present():
	return all(os.path.isfile(path) for path in (
		paths_factory.shape_predictor_5_face_landmarks_path(),
		paths_factory.dlib_face_recognition_resnet_model_v1_path(),
	))


PAM_MODULE_DIRS = (
	"/usr/lib/security", "/usr/lib64/security", "/lib/security", "/lib64/security",
	"/usr/lib/x86_64-linux-gnu/security", "/usr/lib/aarch64-linux-gnu/security",
	"/usr/local/lib/security", "/usr/local/lib64/security",
)


def _pam_modules(directories=PAM_MODULE_DIRS):
	"""Distinct pam_linux_hello.so files; /lib and /usr/lib are often one"""
	found = {}
	for directory in directories:
		path = os.path.join(directory, "pam_linux_hello.so")
		if os.path.isfile(path):
			found.setdefault(os.path.realpath(path), path)
	return sorted(found.values())


def _ir_nodes():
	"""IR capture nodes by advertised pixel format, without streaming"""
	from recorders.device_discovery import IR_FORMATS, candidate_nodes, capture_formats
	nodes = []
	for node in candidate_nodes():
		formats = capture_formats(node)
		if formats and any(fmt in IR_FORMATS for fmt in formats):
			nodes.append(node)
	return nodes


def _camera_name(node):
	try:
		with open("/sys/class/video4linux/{}/name".format(os.path.basename(node))) as file:
			return file.read().strip()
	except OSError:
		return os.path.basename(node)


def _selinux_enforcing():
	try:
		with open("/sys/fs/selinux/enforce") as file:
			return file.read().strip() == "1"
	except OSError:
		return False
