# Guard against multiprocessing re-importing this module in child processes.
# Some GPU runtimes (e.g. OpenVINO) spawn workers via forkserver, which
# re-executes the main module as "__mp_main__" (cli.py when running any
# `linux-hello-cli <cmd>`). Without this guard, workers re-run the CLI dispatch and
# break the parent process with camera EBUSY errors and argparse side effects.
import sys as _sys
if __name__ == "__mp_main__":
	_sys.exit(0)

# linux-hello-cli: command line front end. Model, target and settings
# commands talk to the Linux Hello service and need no root: polkit asks
# for a password where needed. Diagnostic commands that open the camera or
# edit files directly (test, snapshot, config, webauthn, reset-system) still
# run as root.

import argparse
import builtins
import getpass
import json
import os
import pwd
import sys
import time

from i18n import _


RESULTS = {
	0: _("Success"),
	10: _("No face model for this user"),
	11: _("No matching face seen before the timeout"),
	12: _("Aborted"),
	13: _("All frames were too dark, check the IR emitter and dark_threshold"),
	14: _("The camera is not available"),
	15: _("A rubberstamp check failed"),
	250: _("Cancelled"),
}

HINTS = {
	"searching": _("Looking for your face"),
	"no-face": _("No face in view"),
	"multiple-faces": _("More than one face in view"),
	"too-far": _("Move closer"),
	"too-dark": _("Too dark"),
	"captured": _("Got it, hold still"),
}

LOCAL_COMMANDS = ("config", "snapshot", "test", "webauthn", "reset-system", "emitter")


def invoking_user():
	"""The human behind sudo, doas or pkexec, else the current user"""
	for variable in ("SUDO_USER", "DOAS_USER"):
		if os.environ.get(variable):
			return os.environ[variable]
	uid = os.environ.get("PKEXEC_UID", "")
	if uid.isdigit():
		try:
			return pwd.getpwuid(int(uid)).pw_name
		except KeyError:
			pass
	return getpass.getuser()


def cli_copies():
	"""Distinct linux-hello-cli executables on PATH, in lookup order"""
	found = {}
	for directory in os.environ.get("PATH", "").split(os.pathsep):
		path = os.path.join(directory, "linux-hello-cli")
		if os.path.isfile(path) and os.access(path, os.X_OK):
			found.setdefault(os.path.realpath(path), path)
	return list(found.values())


def build_parser():
	parser = argparse.ArgumentParser(
		prog="linux-hello-cli",
		description=_("Face authentication for Linux."),
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog=_("""commands:
  status                   service state and your models
  doctor                   check the whole setup
  list                     face models of the user
  add [label]              enrol a face
  remove <id>              remove one face model
  rename <id> <label>      rename a face model
  clear                    remove all face models of the user
  targets                  where face unlock can be used
  enable <target>          use face unlock for lock-screen, admin, sudo or login
  disable <target>         stop using it there
  set <option> <value>     change a setting
  cameras                  list cameras, best first
  test                     live camera preview (root)
  snapshot                 save a camera snapshot (root)
  config                   edit local settings (root)
  emitter status|test      how the IR light is switched on, and whether it works (root)
  emitter search           find the IR light setting of an unsupported camera (root)
  reset-system [--purge]   undo all PAM changes; --purge also deletes models and settings (root)
  webauthn ...             passkey authenticator (root)
  version

For support please visit https://github.com/360900/linux-hello"""),
	)
	parser.add_argument("command", help=argparse.SUPPRESS)
	parser.add_argument("arguments", nargs="*", help=argparse.SUPPRESS)
	parser.add_argument("-U", "--user", default=None, help=_("user account to act on (default: you)"))
	parser.add_argument("-y", action="store_true", help=_("skip all questions"))
	parser.add_argument("--json", action="store_true", help=_("machine readable output"))
	parser.add_argument("--plain", action="store_true", help=_("comma separated output (list)"))
	parser.add_argument("--section", default=None, help=_("config section, for options that exist in several (set)"))
	parser.add_argument("--purge", action="store_true", help=_("also delete models and settings (reset-system)"))
	return parser


def confirm(args, question):
	if args.y:
		return True
	answer = input(question + " [y/N]: ")
	return answer.strip().lower() == "y"


def emit_json(value):
	print(json.dumps(value, indent=1, sort_keys=True))


def fail(message, code=1):
	print(message, file=sys.stderr)
	sys.exit(code)


# -- service commands --------------------------------------------------------
def cmd_status(client, args):
	status = {key: value.value for key, value in client.call("GetStatus")[0].items()}
	if args.json:
		return emit_json(status)
	print(_("Service:      running (version {})").format(status["version"]))
	print(_("Face unlock:  {}").format(_("off") if status["disabled"] else _("on")))
	print(_("Camera:       {}").format(_("automatic") if status["device"] in ("none", "", "auto") else status["device"]))
	print(_("Data files:   {}").format(_("installed") if status["data_files"] else _("MISSING")))
	if status["user"] and status["user"] != "root":
		print(_("Your face:    {}").format(_("enrolled") if status["user_has_models"] else _("not enrolled, run: linux-hello-cli add")))


def cmd_doctor(client, args):
	checks = client.call("Diagnose")[0]
	if os.geteuid() != 0:
		from integration import kscreenlocker
		try:
			stuck = kscreenlocker.read_choice(kscreenlocker.state_path()) == kscreenlocker.FACE
		except OSError:
			stuck = False
		if stuck and not kscreenlocker.face_enabled():
			kscreenlocker.stop_preferring_face()
			checks.append(["lock-screen-choice", "warning",
				_("Your lock screen started with Face while Face is off, which crashes it. Switched it back to the password.")])
	copies = cli_copies()
	if len(copies) > 1:
		checks.append(["duplicates-cli", "warning",
			_("Several linux-hello-cli on PATH, {} runs first: {}").format(copies[0], ", ".join(copies))])
	if args.json:
		emit_json([{"check": c, "state": s, "message": m} for c, s, m in checks])
	else:
		marks = {"ok": "ok   ", "info": "info ", "warning": "warn ", "error": "FAIL "}
		for check, state, message in checks:
			print("{} {}".format(marks.get(state, state), message))
	if any(state == "error" for _c, state, _m in checks):
		sys.exit(1)


def cmd_list(client, args):
	models = client.call("ListModels", "s", [args.user])[0]
	if args.json:
		return emit_json([{"id": i, "time": t, "label": l} for i, t, l in models])
	if not models:
		fail(_("No face model known for {}, add one with: linux-hello-cli add").format(args.user))
	if not args.plain:
		print(_("Face models of {}:").format(args.user))
		print()
		print(_("ID  Date                 Label"))
	for model_id, created, label in models:
		date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created))
		if args.plain:
			print("{},{},{}".format(model_id, date, label))
		else:
			print("{:<4}{}  {}".format(model_id, date, label))


def cmd_add(client, args):
	label = args.arguments[0] if args.arguments else ""
	interactive = sys.stdout.isatty() and not args.json
	if interactive:
		print(_("Enrolling a face for {}. Look at the camera.").format(args.user))

	def on_signal(member, body):
		if member != "EnrollProgress" or not interactive:
			return
		update = {key: value.value for key, value in body[1].items()}
		done = update["sample"]
		bar = "#" * done + "-" * (update["samples"] - done)
		hint = HINTS.get(update["state"], update["state"])
		sys.stdout.write("\r  [{}] {}/{}  {:<32}".format(bar, done, update["samples"], hint))
		sys.stdout.flush()

	client.start_tty_agent()
	try:
		result, model_id = client.call("Enroll", "ss", [args.user, label], on_signal=on_signal)
	finally:
		client.stop_tty_agent()
		if interactive:
			print()

	if args.json:
		return emit_json({"result": result, "id": model_id})
	if result != 0:
		fail(_("Enrolment failed: {}").format(RESULTS.get(result, result)), result)
	print(_("Added face model {} for {}").format(model_id, args.user))


def _model_id(args):
	try:
		return int(args.arguments[0])
	except (IndexError, ValueError):
		fail(_("Give the model ID, see: linux-hello-cli list"))


def cmd_remove(client, args):
	model_id = _model_id(args)
	if not confirm(args, _("Remove face model {} of {}?").format(model_id, args.user)):
		fail(_("Aborted"))
	client.start_tty_agent()
	try:
		client.call("DeleteModel", "su", [args.user, model_id])
	finally:
		client.stop_tty_agent()
	print(_("Removed model {}").format(model_id))


def cmd_rename(client, args):
	model_id = _model_id(args)
	if len(args.arguments) < 2:
		fail(_("Give the new label: linux-hello-cli rename <id> <label>"))
	client.start_tty_agent()
	try:
		client.call("RenameModel", "sus", [args.user, model_id, " ".join(args.arguments[1:])])
	finally:
		client.stop_tty_agent()


def cmd_clear(client, args):
	models = client.call("ListModels", "s", [args.user])[0]
	if not models:
		fail(_("{} has no face models").format(args.user))
	if not confirm(args, _("Remove all {} face models of {}?").format(len(models), args.user)):
		fail(_("Aborted"))
	client.start_tty_agent()
	try:
		for model_id, _created, _label in models:
			client.call("DeleteModel", "su", [args.user, model_id])
	finally:
		client.stop_tty_agent()
	print(_("Models cleared"))


def cmd_targets(client, args):
	targets = client.call("ListTargets")[0]
	if args.json:
		return emit_json([
			{"id": i, "label": l, "description": d, "available": a, "enabled": e, "services": s}
			for i, l, d, a, e, s in targets
		])
	for target_id, label, description, available, enabled, services in targets:
		state = _("on") if enabled else (_("off") if available else _("n/a"))
		print("{:<12} {:<4} {:<18} {}".format(target_id, state, label, description))


def cmd_enable(client, args, enabled=True):
	if not args.arguments:
		fail(_("Give a target: lock-screen, admin, sudo or login"))
	# Hand the lock screen back to the password before the Face option can
	# go away: kscreenlocker crashes when the choice outlives the option
	if not enabled and "lock-screen" in args.arguments:
		lock_screen_choice(client, False)
	client.start_tty_agent()
	try:
		for target in args.arguments:
			client.call("SetTarget", "sb", [target, enabled])
	finally:
		client.stop_tty_agent()
	if enabled and "lock-screen" in args.arguments:
		lock_screen_choice(client, True)


def lock_screen_choice(client, enabled):
	"""
	Plasma 6.8+ starts its lock screen with the sign-in option that last
	unlocked it. Make face yours right away, in your own state file, and
	hand the choice back to the password when face unlock is turned off.
	"""
	if os.geteuid() == 0:
		return
	targets = {t[0]: t for t in client.call("ListTargets")[0]}
	if "kde-face" not in targets.get("lock-screen", [None] * 6)[5]:
		return
	from integration import kscreenlocker
	try:
		if enabled:
			if not kscreenlocker.face_enabled():
				print(_("The lock screen's Face option is not in effect, keeping the password as its start"))
				return
			kscreenlocker.prefer_face()
			print(_("The lock screen now starts with face unlock. Your password stays one click away."))
		else:
			kscreenlocker.stop_preferring_face()
	except OSError as error:
		print(_("Could not update your lock screen choice: {}").format(error))


def cmd_disable(client, args):
	# Older versions used "disable 1|0" for the global switch
	if args.arguments and args.arguments[0].lower() in ("0", "1", "true", "false"):
		value = "true" if args.arguments[0].lower() in ("1", "true") else "false"
		return set_setting(client, "core", "disabled", value)
	cmd_enable(client, args, enabled=False)


def set_setting(client, section, key, value):
	client.start_tty_agent()
	try:
		client.call("SetSetting", "sss", [section, key, value])
	finally:
		client.stop_tty_agent()


def cmd_set(client, args):
	if len(args.arguments) < 2:
		fail(_("Usage: linux-hello-cli set [--section SECTION] <option> <value>"))
	key, value = args.arguments[0], " ".join(args.arguments[1:])
	section = args.section
	if section is None:
		known = client.call("GetSettings")[0]
		sections = [name.split(".", 1)[0] for name in known if name.split(".", 1)[1] == key]
		if not sections:
			fail(_('Unknown option "{}"').format(key))
		if len(sections) > 1:
			fail(_('"{}" exists in several sections ({}), pass --section').format(key, ", ".join(sections)))
		section = sections[0]
	set_setting(client, section, key, value)
	print(_("Config option updated"))


def cmd_cameras(client, args):
	client.start_tty_agent()
	try:
		cameras = client.call("ListCameras")[0]
	finally:
		client.stop_tty_agent()
	if args.json:
		return emit_json([
			{"path": p, "node": n, "name": name, "infrared": ir, "brightness": b}
			for p, n, name, ir, b in cameras
		])
	if not cameras:
		fail(_("No usable camera found"))
	for path, node, name, infrared, _brightness in cameras:
		print("{:<12} {:<3} {:<32} {}".format(node, "IR" if infrared else "RGB", name, path))


# -- local commands ----------------------------------------------------------
def cmd_reset_system(args):
	from integration.pam import PamIntegration

	from integration import kscreenlocker

	# Before the Face option goes: kscreenlocker crashes for users whose
	# lock screen still starts with it
	for name in kscreenlocker.reset_all_users():
		print(_("Lock screen of {} starts with the password again").format(name))
	changed = PamIntegration().reset()
	for path in changed:
		print(_("Restored {}").format(path))
	if args.purge:
		import shutil
		import paths

		for directory in (paths.state_dir, paths.config_dir, paths.log_path):
			if os.path.isdir(str(directory)):
				shutil.rmtree(str(directory))
				print(_("Deleted {}").format(directory))
	print(_("Linux Hello is no longer used by any PAM service."))


def run_local(args):
	if os.geteuid() != 0:
		fail(_("Please run this command as root:\n\n\tsudo linux-hello-cli {}").format(" ".join(sys.argv[1:])))
	if args.command == "reset-system":
		return cmd_reset_system(args)
	if args.command == "emitter":
		import cli.emitter
		sys.exit(cli.emitter.run(args))

	# The older commands read their options from builtins
	builtins.linux_hello_args = args
	builtins.linux_hello_user = args.user
	if args.user == "root":
		fail(_("Can't run Linux Hello commands as root, please use the --user flag"))

	from recorders.errors import CameraError
	try:
		if args.command == "config":
			import cli.config  # noqa: F401
		elif args.command == "snapshot":
			import cli.snap  # noqa: F401
		elif args.command == "test":
			import cli.test  # noqa: F401
		elif args.command == "webauthn":
			import cli.webauthn  # noqa: F401
	except CameraError as error:
		fail(str(error), 14)


SERVICE_COMMANDS = {
	"status": cmd_status,
	"doctor": cmd_doctor,
	"list": cmd_list,
	"add": cmd_add,
	"remove": cmd_remove,
	"rename": cmd_rename,
	"clear": cmd_clear,
	"targets": cmd_targets,
	"enable": cmd_enable,
	"disable": cmd_disable,
	"set": cmd_set,
	"cameras": cmd_cameras,
}


def main():
	parser = build_parser()
	if len(sys.argv) < 2:
		parser.print_help()
		return 0
	# Options may come between the arguments on every Python version
	args = parser.parse_intermixed_args()
	if args.user is None:
		args.user = invoking_user()

	if args.command == "version":
		import paths_factory
		print("Linux Hello " + paths_factory.version())
		return 0
	if args.command in LOCAL_COMMANDS:
		return run_local(args)
	if args.command not in SERVICE_COMMANDS:
		parser.error(_("unknown command {}").format(args.command))

	from client import Client, ServiceError

	try:
		SERVICE_COMMANDS[args.command](Client(), args)
	except ServiceError as error:
		if error.short_name == "PermissionDenied":
			fail(_("Not authorized. Try again, or run with sudo."), 1)
		fail(str(error), 1)
	except KeyboardInterrupt:
		fail(_("Aborted"), 12)
	return 0


if __name__ == "__main__":
	sys.exit(main())
