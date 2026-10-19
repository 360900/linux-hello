# The Plasma lock screen starts with the sign-in option that last unlocked
# it, stored per user in $XDG_STATE_HOME/kscreenlockerrc ([Greeter]
# Authenticator). Making face the starting option right after enabling it
# saves the user from picking it once by hand.

import os
import re
import tempfile

GROUP = "[Greeter]"
KEY = "Authenticator"
FACE = "Face"
PASSWORD = "Regular"


def state_path(environ=None):
	environ = os.environ if environ is None else environ
	base = environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
	return os.path.join(base, "kscreenlockerrc")


def read_choice(path):
	try:
		with open(path) as file:
			lines = file.read().splitlines()
	except FileNotFoundError:
		return None
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
	try:
		with open(path) as file:
			lines = file.read().splitlines()
	except FileNotFoundError:
		lines = []

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
