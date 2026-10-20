# The Plasma lock screen starts with the sign-in option that last unlocked
# it, stored per user in $XDG_STATE_HOME/kscreenlockerrc ([Greeter]
# Authenticator). Making face the starting option right after enabling it
# saves the user from picking it once by hand.

import os
import re
import stat
import tempfile

GROUP = "[Greeter]"
KEY = "Authenticator"
FACE = "Face"
PASSWORD = "Regular"

# Far more than a real state file; a bigger one is not read
MAX_SIZE = 64 * 1024


def state_path(environ=None):
	environ = os.environ if environ is None else environ
	base = environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
	return os.path.join(base, "kscreenlockerrc")


def _read_lines(path):
	"""
	The file's lines, [] when missing. Root reads these files in every
	user's home, so a link, FIFO or device there must not make it follow,
	hang or read without end: OSError for anything but a small regular file.
	"""
	try:
		descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
	except FileNotFoundError:
		return []
	try:
		info = os.fstat(descriptor)
		if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIZE:
			raise OSError("not a small regular file: {}".format(path))
		data = os.read(descriptor, MAX_SIZE + 1)
	finally:
		os.close(descriptor)
	return data.decode("utf-8", "replace").splitlines()


def read_choice(path):
	lines = _read_lines(path)
	group = None
	for line in lines:
		stripped = line.strip()
		if stripped.startswith("["):
			group = stripped
		elif group == GROUP and re.match(re.escape(KEY) + r"\s*=", stripped):
			return stripped.split("=", 1)[1].strip()
	return None


def set_choice(path, value):
	"""Set [Greeter] Authenticator, leaving every other line as it was"""
	lines = _read_lines(path)

	group = None
	group_end = None
	for index, line in enumerate(lines):
		stripped = line.strip()
		if stripped.startswith("["):
			if group == GROUP:
				group_end = index
				break
			group = stripped
		elif group == GROUP and re.match(re.escape(KEY) + r"\s*=", stripped):
			lines[index] = "{}={}".format(KEY, value)
			break
	else:
		if group == GROUP:
			lines.append("{}={}".format(KEY, value))
		elif group_end is None:
			if lines and lines[-1].strip():
				lines.append("")
			lines += [GROUP, "{}={}".format(KEY, value)]
	if group_end is not None:
		lines.insert(group_end, "{}={}".format(KEY, value))

	directory = os.path.dirname(path)
	os.makedirs(directory, exist_ok=True)
	descriptor, temporary = tempfile.mkstemp(prefix=".kscreenlockerrc-", dir=directory)
	try:
		with os.fdopen(descriptor, "w") as file:
			file.write("\n".join(lines) + "\n")
		os.chmod(temporary, 0o600)
		os.replace(temporary, path)
	except BaseException:
		if os.path.exists(temporary):
			os.unlink(temporary)
		raise


def prefer_face(path=None):
	set_choice(path or state_path(), FACE)


def stop_preferring_face(path=None):
	"""Back to the password, but only if face was the choice"""
	path = path or state_path()
	if read_choice(path) == FACE:
		set_choice(path, PASSWORD)


# -- consistency ----------------------------------------------------------------
# kscreenlocker (6.8) crashes on start when a user's saved choice is Face but
# the Face option is not enabled: it looks the option up and dereferences
# nothing. The helpers below keep the two in step.

def face_enabled(user_config=None, system_config="/etc/xdg/kscreenlockerrc"):
	"""Whether [Authenticators] Face is in effect: the user's value wins"""
	user_config = user_config or os.path.join(
		os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
		"kscreenlockerrc")
	for path in (user_config, system_config):
		value = _read_key(path, "[Authenticators]", "Face")
		if value is not None:
			return value.lower() in ("true", "1", "yes", "on")
	return False


def _read_key(path, wanted_group, key):
	try:
		with open(path) as file:
			lines = file.read().splitlines()
	except OSError:
		return None
	group = None
	value = None
	for line in lines:
		stripped = line.strip()
		if stripped.startswith("["):
			group = stripped
		elif group == wanted_group and re.match(re.escape(key) + r"\s*=", stripped):
			# Later entries win, as in KConfig
			value = stripped.split("=", 1)[1].strip()
	return value


def user_state_paths():
	"""(user, state file) for every regular account, at the default location"""
	import pwd

	found = []
	for entry in pwd.getpwall():
		if entry.pw_uid < 1000 or entry.pw_uid == 65534 or not os.path.isdir(entry.pw_dir):
			continue
		found.append((entry, os.path.join(entry.pw_dir, ".local", "state", "kscreenlockerrc")))
	return found


def users_preferring_face():
	"""Users whose lock screen starts with Face (read only)"""
	users = []
	for entry, path in user_state_paths():
		try:
			if read_choice(path) == FACE:
				users.append(entry.pw_name)
		except OSError:
			continue
	return users


def _as_user(entry, function, *args):
	"""
	Run function in a child process with the user's identity, so writing
	into their home cannot be redirected by links they control
	"""
	if os.geteuid() == entry.pw_uid:
		try:
			function(*args)
		except OSError:
			return False
		return True
	pid = os.fork()
	if pid == 0:
		code = 1
		try:
			os.setgroups([])
			os.setgid(entry.pw_gid)
			os.setuid(entry.pw_uid)
			function(*args)
			code = 0
		finally:
			os._exit(code)
	_pid, status = os.waitpid(pid, 0)
	return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def reset_all_users():
	"""As root: move every user's choice from Face back to the password"""
	changed = []
	for entry, path in user_state_paths():
		try:
			if read_choice(path) != FACE:
				continue
		except OSError:
			continue
		if _as_user(entry, set_choice, path, PASSWORD):
			changed.append(entry.pw_name)
	return changed
