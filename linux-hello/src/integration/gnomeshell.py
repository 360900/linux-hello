# The Linux Hello extension for GNOME Shell (gnome/extension), which starts
# the gdm-face stack next to the password on the lock screen. Installed
# system-wide with the package; turned on per user, as every extension.

import json
import os
import shutil
import subprocess

UUID = "linux-hello@360900.github.io"
SYSTEM_DIR = "/usr/share/gnome-shell/extensions"
SCHEMA = "org.gnome.shell"
# The shell runs what is in ENABLED unless it is also in DISABLED, which takes
# precedence, or USER_OFF switches every listed extension off (the switch at
# the top of the Extensions app); see _getEnabledExtensions in gnome-shell's
# js/ui/extensionSystem.js
ENABLED = "enabled-extensions"
DISABLED = "disabled-extensions"
USER_OFF = "disable-user-extensions"
# ExtensionState.UNINSTALLED in gnome-shell's js/misc/extensionUtils.js: an
# extension the shell has not found. Not 6, which is INITIALIZED, loaded but
# not turned on yet.
NOT_LOADED = 99


def shell_installed(path="/usr/bin/gnome-shell"):
	return os.path.isfile(path)


def installed(directory=SYSTEM_DIR):
	return os.path.isfile(os.path.join(directory, UUID, "metadata.json"))


def in_gnome(environ=None):
	"""Whether this process runs in a GNOME session"""
	environ = os.environ if environ is None else environ
	desktops = environ.get("XDG_CURRENT_DESKTOP", "").split(":")
	return "GNOME" in desktops and bool(environ.get("DBUS_SESSION_BUS_ADDRESS"))


def _gsettings(*arguments):
	gsettings = shutil.which("gsettings")
	if gsettings is None:
		raise OSError("gsettings not found")
	try:
		result = subprocess.run([gsettings, *arguments], capture_output=True, text=True, timeout=30)
	except subprocess.SubprocessError as error:
		raise OSError("gsettings did not answer: {}".format(error)) from error
	if result.returncode != 0:
		raise OSError(result.stderr.strip() or "gsettings failed")
	return result.stdout.strip()


def _list(key):
	# A GVariant string array prints as JSON-like text with single quotes;
	# "@as []" when empty
	text = _gsettings("get", SCHEMA, key)
	if text.startswith("@as"):
		text = text[len("@as"):].strip()
	return json.loads(text.replace("'", '"'))


def _set_list(key, uuids):
	_gsettings("set", SCHEMA, key, "[" + ", ".join("'" + uuid + "'" for uuid in uuids) + "]")


def user_extensions_off():
	"""Whether the user switched every extension off in the Extensions app"""
	try:
		return _gsettings("get", SCHEMA, USER_OFF) == "true"
	except OSError:
		return False


def enabled_for_user():
	"""Whether the shell runs the extension for this user"""
	try:
		return UUID in _list(ENABLED) and UUID not in _list(DISABLED) and not user_extensions_off()
	except (OSError, ValueError):
		return False


def set_enabled(enabled):
	"""
	Turn the extension on or off for this user, as the Extensions app does,
	except for the switch over all extensions, which stays the user's. The
	shell picks up a change at once when it knows the extension, else at the
	next login.
	"""
	if enabled:
		disabled = _list(DISABLED)
		if UUID in disabled:
			disabled.remove(UUID)
			_set_list(DISABLED, disabled)
	uuids = _list(ENABLED)
	if enabled and UUID not in uuids:
		uuids.append(UUID)
	elif not enabled and UUID in uuids:
		uuids.remove(UUID)
	else:
		return
	_set_list(ENABLED, uuids)


def loaded():
	"""
	Whether the running shell knows the extension: the state it reports
	(1 active, 2 inactive, 6 loaded, 99 not found), or None without a shell. A
	shell started before the extension was installed has not loaded it.
	"""
	try:
		result = subprocess.run(
			["gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell",
				"--method", "org.gnome.Shell.Extensions.GetExtensionInfo", UUID],
			capture_output=True, text=True, timeout=10)
	except (OSError, subprocess.SubprocessError):
		return None
	if result.returncode != 0:
		return None
	# ({'uuid': <'...'>, 'state': <1.0>, ...},)
	text = result.stdout
	marker = "'state': <"
	at = text.find(marker)
	if at < 0:
		return NOT_LOADED
	try:
		return int(float(text[at + len(marker):].split(">", 1)[0]))
	except ValueError:
		return None
