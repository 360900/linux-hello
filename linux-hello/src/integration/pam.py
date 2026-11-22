# PAM integration: wire pam_linux_hello.so into the services behind each
# user facing target (lock screen, admin prompts, sudo, login screen), and
# turn on logging in automatically behind the lock screen.
#
# Rules this module keeps, so enabling, disabling and uninstalling are safe:
# - Global stacks (common-auth, system-auth, system-login, password-auth)
#   are never touched, only the service files of a target.
# - Every change is a marked block, or a whole file we created, and is
#   recorded in a manifest. reset() reverts from that manifest only.
# - A service that only exists in the vendor directory (/usr/lib/pam.d) is
#   copied to /etc/pam.d first. When face unlock is turned off again and
#   nobody else changed the copy, it is deleted so the vendor file applies.
# - Files are replaced atomically, keeping their mode.
#
# Desktops: Plasma's lock screen (6.8+) runs a non-interactive "kde-face"
# stack of its own, and its login managers (plasmalogin, SDDM before it) take
# the password before PAM.
# GNOME's lock screen and login screen both go through GDM, which runs
# "gdm-password" as root and, on the lock screen, the non-interactive
# "gdm-face" stack that the Linux Hello extension for GNOME Shell starts next
# to it (gnome/extension). Both desktops may be installed at once; a target
# then covers both.

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

MODULE = "pam_linux_hello.so"

BEGIN = "# BEGIN linux-hello (managed by linux-hello, do not edit)"
END = "# END linux-hello"

# The keyring (KWallet, GNOME Keyring) after logging in automatically
# (daemon/secrets.py): the login screen's stack holds on to the typed
# password at the end of auth and hands it to the service when a session
# opens; the autologin stack gets it back for the keyring module. Its own
# marker, as the login screen's file may hold the login target's block too.
KEYRING_BEGIN = "# BEGIN linux-hello keyring (managed by linux-hello, do not edit)"
# What versions before 0.2 wrote; still recognized, rewritten on refresh
WALLET_BEGIN = "# BEGIN linux-hello kwallet (managed by linux-hello, do not edit)"
KEYRING_HOLD_LINE = "-auth\toptional\t" + MODULE + " remember_password"
KEYRING_REMEMBER_LINE = "-session\toptional\t" + MODULE + " remember_password"
KEYRING_RELEASE_LINE = "-auth\toptional\t" + MODULE + " keyring"
KEYRING_BEGINS = (KEYRING_BEGIN, WALLET_BEGIN)
BEGINS = (BEGIN,) + KEYRING_BEGINS

# Modules that take the login password out of the stack for the keyring
KEYRING_MODULES = ("pam_gnome_keyring.so", "pam_oo7.so", "pam_kwallet5.so")

# A failing, unavailable or missing module falls through to the next auth
# line, so the password keeps working whatever happens to Linux Hello
INSERT_LINE = "auth\t[success=done default=ignore]\t" + MODULE

# Login screens hand over the form's password first (see pam/main.cc): a
# typed one makes the module step aside, so it only reports a failed match
# for an empty form. Ending the stack there keeps that empty password from
# counting as a wrong one toward faillock's lockout as well.
PASSWORD_FIRST_SERVICES = ("plasmalogin", "sddm")
LOGIN_INSERT_LINE = "auth\t[success=done ignore=ignore auth_err=die default=ignore]\t" + MODULE

# In a non-interactive stack nothing else may grant access: anything but a
# match (including PAM_IGNORE) fails the stack
OWN_STACK_LINE = "auth\t[success=ok default=die]\t" + MODULE + " no_password"

# First lines of every service file we create. Such a file is only ever
# deleted as a whole: stripping our module out of it would leave a stack
# that succeeds without any authentication.
OWN_HEADER = "#%PAM-1.0\n# Created by linux-hello"

# Our non-interactive stacks: built from the fingerprint stack next to them,
# which the lock screen runs in parallel to the password, or from the
# password stack when there is none. Value: (fingerprint stack, password
# stack, what the stack is for).
OWN_STACKS = {
	"kde-face": ("kde-fingerprint", "kde", "the Plasma lock screen"),
	"gdm-face": ("gdm-fingerprint", "gdm-password", "the GNOME lock screen"),
}

ETC_DIR = "etc/pam.d"

# Plasma 6.8+ only offers face unlock on the lock screen when this is set;
# /etc/xdg is the system-wide default users can still override
KSCREENLOCKER_CONFIG = "etc/xdg/kscreenlockerrc"
KSCREENLOCKER_BLOCK = [BEGIN, "[Authenticators]", "Face=true", END]
VENDOR_DIRS = ("usr/lib/pam.d", "usr/etc/pam.d")

# Plasma's login managers, plasmalogin (6.8+) and SDDM, read the drop-ins of
# their directory in name order, then their main file, whose values win. Ours
# names the user to log in at boot; the session then locks itself at once
# (see linux-hello-lock-guard). Value: binary, main file, drop-in directory.
# The PAM services are named after the manager: "sddm" and "sddm-autologin".
LOGIN_MANAGERS = {
	"plasmalogin": ("usr/bin/plasmalogin", "etc/plasmalogin.conf", "etc/plasmalogin.conf.d"),
	"sddm": ("usr/bin/sddm", "etc/sddm.conf", "etc/sddm.conf.d"),
}
AUTOLOGIN_NAME = "linux-hello.conf"
AUTOLOGIN_HEADER = "# Created by linux-hello"
WAYLAND_SESSION = "usr/share/wayland-sessions/plasma.desktop"
STATE_FILE = "var/lib/linux-hello/pam-state.json"
# The old text of a file being rewritten in place, until the write is done
JOURNAL_DIR = "var/lib/linux-hello/pending"

# GDM has no drop-ins: logging in automatically is a marked block in the
# [daemon] group of its one configuration file. Arch and Fedora; Ubuntu;
# Debian.
GDM_BINARIES = ("usr/bin/gdm", "usr/sbin/gdm", "usr/sbin/gdm3")
GDM_CONFIGS = ("etc/gdm/custom.conf", "etc/gdm3/custom.conf", "etc/gdm3/daemon.conf")
GDM_GROUP = "daemon"
# Which display manager boots: systemd's alias for it, else what is installed
DISPLAY_MANAGER_LINK = "etc/systemd/system/display-manager.service"

# Greeter binaries that may know the "kde-face" non-interactive service
KSCREENLOCKER_GREETERS = (
	"usr/lib/kscreenlocker_greet",
	"usr/libexec/kscreenlocker_greet",
	"usr/lib/x86_64-linux-gnu/libexec/kscreenlocker_greet",
	"usr/lib/aarch64-linux-gnu/libexec/kscreenlocker_greet",
	"usr/lib64/libexec/kscreenlocker_greet",
)

# Checks of a service's auth stack that a face match must not skip: with
# success=done ours ends the stack, so they are copied into our block in
# front of it. Value: an argument the line needs (faillock only checks with
# preauth). Running a check twice gives the same answer.
GATES = {
	"pam_faillock.so": "preauth",
	"pam_nologin.so": None,
	"pam_access.so": None,
	"pam_shells.so": None,
	"pam_securetty.so": None,
	"pam_listfile.so": None,
	"pam_time.so": None,
}
# Deep enough for any real stack, and stops include loops
MAX_INCLUDE_DEPTH = 8

# Modules that must not end up in a non-interactive face stack copied from
# a fingerprint stack: the fingerprint module itself and the unconditional
# permit
OWN_STACK_DROP = ("pam_fprintd.so", "pam_permit.so")

TARGETS = [
	{
		"id": "lock-screen",
		"label": "Lock screen",
		"description": "Unlock the lock screen with your face.",
	},
	{
		"id": "admin",
		"label": "Admin prompts",
		"description": "Approve polkit prompts, including run0, with your face after pressing Enter.",
	},
	{
		"id": "sudo",
		"label": "Terminal (sudo)",
		"description": "Authenticate sudo in a terminal with your face after pressing Enter.",
	},
	{
		"id": "login",
		"label": "Login screen",
		"description": "Log in with your face. A typed password works as usual and unlocks your keyring.",
	},
	{
		"id": "auto-login",
		"label": "Log in automatically",
		"description": "Start your session at boot with your keyring open and lock it right away, so the lock screen looks for your face. Fingerprint and password work there too.",
	},
]

# How a target works on each desktop, for the targets that differ
DESCRIPTIONS = {
	"lock-screen": {
		"plasma": "Unlock the Plasma lock screen with your face.",
		"gnome": "Unlock the GNOME lock screen with your face, next to the password. Needs the Linux Hello extension for GNOME Shell.",
		"both": "Unlock the lock screen with your face. On GNOME this needs the Linux Hello extension for GNOME Shell.",
	},
	"login": {
		"plasma": "Leave the password empty and press Enter to log in with your face. A typed password works as usual and unlocks KWallet.",
		"gnome": "Your face is looked for as soon as your name is selected; the password field follows when it is not found. A typed password works as usual and unlocks the keyring.",
		"both": "Plasma: leave the password empty and press Enter. GNOME: your face is looked for as soon as your name is selected. A typed password works as usual and unlocks your keyring.",
	},
	"auto-login": {
		"plasma": "Start your session at boot with KWallet open and lock it right away, so the lock screen looks for your face. Fingerprint and password work there too.",
		"gnome": "Start your session at boot with the keyring open and lock it right away, so the lock screen looks for your face. Fingerprint and password work there too.",
	},
}
PLASMA_SERVICES = {"kde-face", "kde", "kscreenlockerrc", "plasmalogin", "plasmalogin-autologin", "plasmalogin.conf.d", "sddm", "sddm-autologin", "sddm.conf.d"}
GNOME_SERVICES = {"gdm-face", "gdm-password", "gdm-autologin", "gdm"}


# What a drop-in may name; anything else could add lines to it
USER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.@-]*")


class PamError(Exception):
	pass


def _ini_group(text, group):
	"""Keys of one [group] in an INI file; repeated groups add up, like KConfig"""
	values = {}
	current = None
	for line in text.splitlines():
		line = line.strip()
		if line.startswith("[") and line.endswith("]"):
			current = line[1:-1]
		elif current == group and "=" in line and not line.startswith("#"):
			key, value = line.split("=", 1)
			values[key.strip()] = value.strip()
	return values


def _true(value):
	return (value or "").strip().lower() in ("true", "1", "yes", "on")


def _sha256(text):
	return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split(line):
	"""(type, control, [module, args...]) of a PAM config line, or None"""
	stripped = line.strip()
	if not stripped or stripped.startswith("#") or stripped.startswith("@include"):
		return None
	parts = stripped.split()
	kind = parts[0].lstrip("-")
	rest = parts[1:]
	# The control field is either one word or a [ ... ] group
	control = []
	if rest and rest[0].startswith("["):
		while rest and not rest[0].endswith("]"):
			control.append(rest[0])
			rest = rest[1:]
	if rest:
		control.append(rest[0])
		rest = rest[1:]
	return kind, " ".join(control), rest


def _module_of(line):
	"""Return (type, module) of a PAM config line, or (None, None)"""
	stripped = line.strip()
	if stripped.startswith("@include"):
		parts = stripped.split()
		return "@include", parts[1] if len(parts) > 1 else None
	split = _split(line)
	if split is None:
		return None, None
	kind, _control, rest = split
	return kind, rest[0] if rest else None


def _module_args(line):
	"""(module file name, arguments) of a line, for telling copies apart"""
	split = _split(line)
	if split is None or not split[2]:
		return None
	return os.path.basename(split[2][0]), tuple(split[2][1:])


def _is_auth_line(line):
	kind, module = _module_of(line)
	if kind == "auth":
		return True
	return kind == "@include" and module == "common-auth"


def has_block(text, begin=BEGIN):
	return begin in text


def has_keyring_block(text):
	return any(has_block(text, begin) for begin in KEYRING_BEGINS)


def block_lines(text, begin=BEGIN):
	"""The lines inside the first block marked by begin"""
	lines = []
	inside = False
	for line in text.splitlines():
		stripped = line.strip()
		if not inside and stripped == begin:
			inside = True
		elif inside:
			if stripped == END:
				break
			lines.append(line)
	return lines


def gate_line(line):
	"""Our copy of a check line, or None when the line is not one"""
	parts = line.split()
	if len(parts) < 3 or parts[0].lstrip("-") != "auth" or parts[1] not in ("required", "requisite"):
		return None
	module = parts[2]
	name = os.path.basename(module)
	if name not in GATES or (GATES[name] and GATES[name] not in parts[3:]):
		return None
	# "-": a module removed from the system later is skipped, it must not
	# fail the stack and with it the password
	return "-auth\t{}\t{}".format(parts[1], " ".join(parts[2:]))


def insert_block(text, gates=(), line=INSERT_LINE):
	"""Insert our block, behind copies of the gates, before the first auth line"""
	if has_block(text):
		return text
	lines = text.splitlines(keepends=True)
	if lines and not lines[-1].endswith("\n"):
		lines[-1] += "\n"
	block = [BEGIN + "\n"] + [gate + "\n" for gate in gates] + [line + "\n", END + "\n"]

	for index, current in enumerate(lines):
		if _is_auth_line(current):
			return "".join(lines[:index] + block + lines[index:])
	raise PamError("no auth line found")


def _ours(line):
	_kind, module = _module_of(line)
	return bool(module) and os.path.basename(module) == MODULE


def remove_block(text, begins=BEGINS):
	"""Remove every block marked by one of begins, leaving the rest byte for byte"""
	out = []
	block = None
	for line in text.splitlines(keepends=True):
		stripped = line.strip()
		if block is None and stripped in begins:
			block = []
			continue
		if block is not None:
			if stripped == END:
				block = None
			else:
				block.append(line)
			continue
		out.append(line)
	if block is not None:
		# The END line is gone: keep what follows, which is not ours, rather
		# than cut the stack off at our block
		out.extend(line for line in block if not _ours(line))
	return "".join(out)


def _keyring_block(*lines):
	return KEYRING_BEGIN + "\n" + "".join(line + "\n" for line in lines) + END + "\n"


def keyring_companions(text, started=()):
	"""
	Keyring auth lines for our block, for the modules whose session line
	(started: the keyring modules the stack's session section runs) would
	otherwise never get the password we hand over. GDM's autologin stack runs
	its own only when pam_gdm found the disk password in the kernel keyring
	([success=ok default=1] skips the next line), so they are copied; Fedora's
	and Debian's sddm-autologin have none at all, so they are added.
	"""
	own = {}
	for line in text.splitlines():
		kind, module = _module_of(line)
		if kind == "auth" and module and os.path.basename(module) in KEYRING_MODULES:
			own[os.path.basename(module)] = "-auth\toptional\t" + " ".join(_split(line)[2])
	companions = list(own.values()) if "pam_gdm.so" in text else []
	companions += ["-auth\toptional\t" + module for module in started if module not in own]
	return companions


def insert_keyring_release(text, companions=()):
	"""Before the first auth line, so the keyring module finds the password"""
	if has_keyring_block(text):
		return text
	lines = text.splitlines(keepends=True)
	for index, line in enumerate(lines):
		if _is_auth_line(line):
			return "".join(lines[:index]) + _keyring_block(KEYRING_RELEASE_LINE, *companions) + "".join(lines[index:])
	raise PamError("no auth line found")


def insert_keyring_remember(text):
	"""Behind the last auth line, and a session line at the end"""
	if has_keyring_block(text):
		return text
	if text and not text.endswith("\n"):
		text += "\n"
	lines = text.splitlines(keepends=True)
	last = max((index for index, line in enumerate(lines) if _is_auth_line(line)), default=None)
	if last is None:
		raise PamError("no auth line found")
	lines.insert(last + 1, _keyring_block(KEYRING_HOLD_LINE))
	return "".join(lines) + _keyring_block(KEYRING_REMEMBER_LINE)


def insert_in_group(text, group, lines):
	"""
	Append lines to one [group] of an INI file, creating the group at the end
	when it is missing. Last in the group, so the values win over earlier
	ones, like GKeyFile reads them.
	"""
	out = text.splitlines(keepends=True)
	if out and not out[-1].endswith("\n"):
		out[-1] += "\n"
	header = "[" + group + "]"
	block = [line + "\n" for line in lines]
	start = next((index for index, line in enumerate(out) if line.strip() == header), None)
	if start is None:
		if out and out[-1].strip():
			out.append("\n")
		return "".join(out + [header + "\n"] + block)
	end = next((index for index in range(start + 1, len(out)) if out[index].strip().startswith("[")), len(out))
	# Before the blank lines that set the group apart from the next one
	while end > start + 1 and not out[end - 1].strip():
		end -= 1
	return "".join(out[:end] + block + out[end:])


def remove_legacy_lines(text):
	"""Drop unmarked pam_linux_hello.so lines written by older versions"""
	return "".join(line for line in text.splitlines(keepends=True) if not _ours(line))


def build_own_stack(source_text, from_fingerprint, purpose, gates=()):
	"""
	Build a non-interactive face stack from a fingerprint stack (or from the
	password stack when that does not exist). Keeps the account, password and
	session lines and the protective auth checks (nologin, faillock, shells),
	replaces the fingerprint module with ours and drops anything that could
	grant access without a face match. Checks that only an included stack
	ran (gates, see GATES) are copied in front of our line.
	"""
	header = [
		"#%PAM-1.0\n",
		"# Created by linux-hello: face unlock on {}.\n".format(purpose),
		"# Removed again by 'linux-hello-cli reset-system'.\n",
	]
	lines = [line if line.endswith("\n") else line + "\n" for line in source_text.splitlines()]

	def kept(line):
		kind, module = _module_of(line)
		return (
			from_fingerprint
			and kind == "auth"
			and module is not None
			and module.endswith(".so")
			and os.path.basename(module) not in OWN_STACK_DROP
		)

	kept_modules = {_module_args(line) for line in lines if kept(line)}
	body = []
	placed = False
	for line in lines:
		if line.startswith("#%PAM"):
			continue
		_kind, module = _module_of(line)
		if not _is_auth_line(line):
			# A password change through this stack must not reach the
			# fingerprint module either
			if module is None or os.path.basename(module) != "pam_fprintd.so":
				body.append(line)
			continue
		if not placed and not kept(line):
			for gate in gates:
				if _module_args(gate) not in kept_modules:
					body.append(gate + "\n")
			body.append(OWN_STACK_LINE + "\n")
			placed = True
		elif kept(line):
			body.append(line)
	if not placed:
		raise PamError("source stack has no auth section")
	return "".join(header + body)


class PamIntegration:
	"""Enable, disable and reset face unlock per target"""

	def __init__(self, root="/"):
		self.root = root

	# -- paths -------------------------------------------------------------
	def _path(self, relative):
		return os.path.join(self.root, relative)

	def _etc(self, service):
		return self._path(os.path.join(ETC_DIR, service))

	def _vendor(self, service):
		for directory in VENDOR_DIRS:
			path = self._path(os.path.join(directory, service))
			if os.path.isfile(path):
				return path
		return None

	def _find(self, service):
		"""Path of the file PAM uses for a service, or None"""
		path = self._etc(service)
		if os.path.isfile(path):
			return path
		return self._vendor(service)

	def _read(self, path):
		with open(path, "r", encoding="utf-8") as file:
			return file.read()

	def _write(self, path, text, mode=0o644):
		directory = os.path.dirname(path)
		os.makedirs(directory, exist_ok=True)
		if os.path.exists(path):
			mode = os.stat(path).st_mode & 0o7777
		try:
			descriptor, temporary = tempfile.mkstemp(prefix=".linux-hello-", dir=directory)
		except OSError as error:
			if error.errno not in (errno.EROFS, errno.EACCES) or not os.path.isfile(path):
				raise
			self._write_in_place(path, text)
			return
		try:
			with os.fdopen(descriptor, "w", encoding="utf-8") as file:
				file.write(text)
				file.flush()
				os.fsync(file.fileno())
			os.chmod(temporary, mode)
			os.replace(temporary, path)
		except BaseException:
			if os.path.exists(temporary):
				os.unlink(temporary)
			raise
		self._sync_dir(directory)
		self._restore_context(path)

	def _write_in_place(self, path, text):
		"""
		For a file made writable on its own inside a read-only directory (the
		service's ReadWritePaths for /etc/sddm.conf and /etc/plasmalogin.conf),
		where nothing can be renamed into place. The old text is kept in the
		journal first, so a write cut short by a full disk is undone at once,
		and one cut short by a crash or power loss by recover().
		"""
		old = self._read(path)
		journal = self._journal(path)
		self._write(journal, json.dumps({"path": path, "text": old}) + "\n", mode=0o600)
		try:
			self._overwrite(path, text)
		except BaseException:
			try:
				self._overwrite(path, old)
			except OSError:
				# The journal stays for recover()
				pass
			else:
				self._remove(journal)
			raise
		self._remove(journal)
		self._restore_context(path)

	def _overwrite(self, path, text):
		with open(path, "w", encoding="utf-8") as file:
			file.write(text)
			file.flush()
			os.fsync(file.fileno())

	def _journal(self, path):
		name = hashlib.sha256(path.encode("utf-8")).hexdigest()[:32] + ".json"
		return self._path(os.path.join(JOURNAL_DIR, name))

	def recover(self):
		"""
		Put back files whose rewrite in place was cut short. Returns the list
		of files put back.
		"""
		directory = self._path(JOURNAL_DIR)
		try:
			names = sorted(os.listdir(directory))
		except OSError:
			return []
		restored = []
		for name in names:
			journal = os.path.join(directory, name)
			try:
				with open(journal, "r", encoding="utf-8") as file:
					entry = json.load(file)
				path, text = entry["path"], entry["text"]
			except (OSError, ValueError, KeyError, TypeError):
				# Cut short itself: the file was not touched yet
				self._remove(journal)
				continue
			try:
				with open(path, "rb") as file:
					same = file.read() == text.encode("utf-8")
			except OSError:
				same = False
			if not same:
				self._overwrite(path, text)
				self._restore_context(path)
				restored.append(path)
			self._remove(journal)
		return restored

	def _remove(self, path):
		os.unlink(path)
		self._sync_dir(os.path.dirname(path))

	def _sync_dir(self, directory):
		try:
			descriptor = os.open(directory, os.O_RDONLY)
		except OSError:
			return
		try:
			os.fsync(descriptor)
		finally:
			os.close(descriptor)

	def _restore_context(self, path):
		# Keep the SELinux label right on Fedora; only for the real root
		if self.root == "/" and shutil.which("restorecon"):
			subprocess.run(["restorecon", path], check=False, capture_output=True)

	# -- manifest ----------------------------------------------------------
	def _load_state(self):
		# Every change starts here: from the files as they were
		self.recover()
		try:
			with open(self._path(STATE_FILE), "r", encoding="utf-8") as file:
				state = json.load(file)
		except FileNotFoundError:
			return {"version": 1, "files": {}}
		except ValueError as error:
			raise PamError("corrupt state file: {}".format(error))
		state.setdefault("files", {})
		return state

	def _save_state(self, state):
		path = self._path(STATE_FILE)
		self._write(path, json.dumps(state, indent=1, sort_keys=True) + "\n", mode=0o600)

	# -- target resolution -------------------------------------------------
	def _kde_face_supported(self):
		for greeter in KSCREENLOCKER_GREETERS:
			path = self._path(greeter)
			try:
				with open(path, "rb") as file:
					if b"kde-face\x00" in file.read():
						return True
			except OSError:
				continue
		return False

	def display_manager(self):
		"""
		plasmalogin, sddm, gdm or None: what boots, else, with no display
		manager enabled, what is installed
		"""
		link = self._path(DISPLAY_MANAGER_LINK)
		if os.path.lexists(link):
			try:
				target = os.path.basename(os.readlink(link))
			except OSError:
				target = ""
			name = target[:-len(".service")] if target.endswith(".service") else target
			if name in ("gdm", "gdm3"):
				return "gdm"
			if name in LOGIN_MANAGERS:
				return name
			# Another one boots (LightDM, greetd, ly): configuration for one
			# installed next to it would do nothing now, and log in without
			# a password once someone switches
			return None
		for manager, (binary, _main, _directory) in LOGIN_MANAGERS.items():
			if os.path.isfile(self._path(binary)):
				return manager
		if any(os.path.isfile(self._path(binary)) for binary in GDM_BINARIES):
			return "gdm"
		return None

	def _autologin_file(self, manager):
		"""Our drop-in of a login manager"""
		return self._path(os.path.join(LOGIN_MANAGERS[manager][2], AUTOLOGIN_NAME))

	def _autologin_files(self):
		return {self._autologin_file(manager): manager for manager in LOGIN_MANAGERS}

	def _other_autologin_configs(self, manager):
		"""
		The login manager's other configuration files, in the order it reads
		them: the other drop-ins, then the main file. Any of them may name a
		user to log in too.
		"""
		_binary, main, directory = LOGIN_MANAGERS[manager]
		paths = []
		try:
			names = sorted(os.listdir(self._path(directory)))
		except OSError:
			names = []
		for name in names:
			path = self._path(os.path.join(directory, name))
			if name != AUTOLOGIN_NAME and os.path.isfile(path):
				paths.append(path)
		if os.path.isfile(self._path(main)):
			paths.append(self._path(main))
		return paths

	def _read_after_ours(self, manager, path):
		"""Whether the login manager reads this file after our drop-in, so its values win"""
		_binary, main, _directory = LOGIN_MANAGERS[manager]
		return path == self._path(main) or os.path.basename(path) > AUTOLOGIN_NAME

	def _gdm_config(self):
		"""GDM's configuration file: the one there is, else where it belongs"""
		for relative in GDM_CONFIGS:
			if os.path.isfile(self._path(relative)):
				return self._path(relative)
		# Debian keeps daemon.conf and Ubuntu custom.conf under /etc/gdm3;
		# with neither there is no telling which GDM reads
		if os.path.isdir(self._path("etc/gdm")):
			return self._path(GDM_CONFIGS[0])
		return None

	def _plan(self, target):
		"""
		Return the list of (service, kind) a target maps to on this system,
		kind being "own" (a file we create), "insert" (a marked block) or
		one of the configuration kinds.
		"""
		if target == "lock-screen":
			plan = []
			if self._kde_face_supported() and (self._find("kde-fingerprint") or self._find("kde")):
				plan += [("kde-face", "own"), ("kscreenlockerrc", "kde-config")]
			elif self._find("kde"):
				plan.append(("kde", "insert"))
			if self._find("gdm-fingerprint") or self._find("gdm-password"):
				plan.append(("gdm-face", "own"))
			return plan
		if target == "admin":
			return [("polkit-1", "insert")] if self._find("polkit-1") else []
		if target == "sudo":
			plan = [("sudo", "insert")] if self._find("sudo") else []
			# sudo-i usually includes sudo (Fedora, Arch); patching both
			# would scan the face twice
			if self._find("sudo-i") and not (plan and self._includes("sudo-i", "sudo")):
				plan.append(("sudo-i", "insert"))
			return plan
		if target == "login":
			# Every login screen installed: which one boots can change
			# without face unlock being set up again
			plan = [(service, "insert") for service in PASSWORD_FIRST_SERVICES if self._find(service)]
			if self._find("gdm-password"):
				plan.append(("gdm-password", "insert"))
			return plan
		if target == "auto-login":
			manager = self.display_manager()
			if manager in LOGIN_MANAGERS and self._find(manager) and self._find(manager + "-autologin"):
				# The drop-in last: it is what logs in, so it only appears
				# once the keyring's lines are in place
				return [
					(manager + "-autologin", "keyring-release"),
					(manager, "keyring-remember"),
					(manager + ".conf.d", "autologin"),
				]
			if manager == "gdm" and self._gdm_config() and self._find("gdm-autologin") and self._find("gdm-password"):
				return [
					("gdm-autologin", "keyring-release"),
					("gdm-password", "keyring-remember"),
					("gdm", "gdm-config"),
				]
			return []
		raise PamError("unknown target {}".format(target))

	def _describe(self, target, plan):
		services = {service for service, _kind in plan}
		plasma = bool(services & PLASMA_SERVICES)
		gnome = bool(services & GNOME_SERVICES)
		texts = DESCRIPTIONS.get(target["id"], {})
		if plasma and gnome:
			return texts.get("both", target["description"])
		if plasma:
			return texts.get("plasma", target["description"])
		if gnome:
			return texts.get("gnome", target["description"])
		return target["description"]

	def _stack_lines(self, text, kind="auth", depth=0, seen=None):
		"""The lines of one kind (auth, session) in a service file, includes followed"""
		seen = set() if seen is None else seen
		for line in text.splitlines():
			parts = line.split()
			if not parts or parts[0].startswith("#"):
				continue
			included = None
			if parts[0] == "@include" and len(parts) > 1:
				included = parts[1]
			elif len(parts) >= 3 and parts[0].lstrip("-") == kind and parts[1] in ("include", "substack"):
				included = parts[2]
			if included is None:
				if parts[0].lstrip("-") == kind:
					yield line
				continue
			path = self._find(included)
			if path is None or path in seen or depth >= MAX_INCLUDE_DEPTH:
				continue
			seen.add(path)
			try:
				yield from self._stack_lines(self._read(path), kind, depth + 1, seen)
			except (OSError, UnicodeDecodeError):
				continue

	def _keyring_sessions(self, text):
		"""The keyring modules a stack's session section starts, in order"""
		started = []
		for line in self._stack_lines(text, "session"):
			_kind, module = _module_of(line)
			name = os.path.basename(module) if module else None
			if name in KEYRING_MODULES and name not in started:
				started.append(name)
		return started

	def _gates(self, text):
		"""Copies of the checks in a service's whole auth stack, see GATES"""
		gates = []
		for line in self._stack_lines(remove_block(text)):
			gate = gate_line(line)
			if gate is not None and gate not in gates:
				gates.append(gate)
		return gates

	def _includes(self, service, other):
		"""True when a service pulls in another one's auth stack"""
		try:
			text = self._read(self._find(service))
		except (OSError, TypeError):
			return False
		for line in text.splitlines():
			parts = line.split()
			if len(parts) >= 3 and parts[0].lstrip("-") == "auth" and parts[1] in ("include", "substack") and parts[2] == other:
				return True
		return False

	def _service_enabled(self, service, kind):
		if kind == "kde-config":
			path = self._path(KSCREENLOCKER_CONFIG)
			return os.path.isfile(path) and has_block(self._read(path))
		if kind == "autologin":
			return self._dropin_autologin_user(service[:-len(".conf.d")]) is not None
		if kind == "gdm-config":
			return self._gdm_autologin_user() is not None
		path = self._etc(service)
		if kind.startswith("keyring-"):
			return os.path.isfile(path) and has_keyring_block(self._read(path))
		if not os.path.isfile(path):
			return False
		text = self._read(path)
		if kind == "own":
			return text.startswith(OWN_HEADER)
		return has_block(text)

	# -- public API --------------------------------------------------------
	def targets(self):
		"""Every target with its availability, state and service files"""
		result = []
		for target in TARGETS:
			plan = self._plan(target["id"])
			entry = dict(target)
			entry["description"] = self._describe(target, plan)
			entry["available"] = bool(plan)
			entry["services"] = [service for service, kind in plan if kind not in ("kde-config", "autologin", "gdm-config")]
			entry["enabled"] = bool(plan) and all(self._service_enabled(s, k) for s, k in plan)
			if target["id"] == "auto-login" and self.autologin_user() is not None:
				# The configuration alone logs in: shown as on, so it can be
				# turned off
				entry["enabled"] = True
			result.append(entry)
		return result

	def enable(self, target, user=None):
		"""user: who logs in automatically, for auto-login"""
		plan = self._plan(target)
		if not plan:
			raise PamError("target {} is not available on this system".format(target))
		state = self._load_state()
		# Put back exactly what was there when a step fails, so a target is
		# never left half on
		paths = [self._kind_path(service, kind) for service, kind in plan]
		before = {path: self._read(path) if os.path.isfile(path) else None for path in paths}
		saved = json.loads(json.dumps(state))
		try:
			for service, kind in plan:
				if kind == "own":
					self._enable_own(service, target, state)
				elif kind == "kde-config":
					self._enable_kde_config(target, state)
				elif kind == "autologin":
					self._enable_autologin(service[:-len(".conf.d")], target, user, state)
				elif kind == "gdm-config":
					self._enable_gdm_config(target, user, state)
				elif kind.startswith("keyring-"):
					self._enable_keyring(service, kind, target, state)
				else:
					self._enable_insert(service, target, state)
		except BaseException:
			for path, text in before.items():
				if text is None:
					if os.path.isfile(path):
						self._remove(path)
				elif not os.path.isfile(path) or self._read(path) != text:
					self._write(path, text)
			self._save_state(saved)
			raise
		self._save_state(state)

	def _kind_path(self, service, kind):
		if kind == "kde-config":
			return self._path(KSCREENLOCKER_CONFIG)
		if kind == "autologin":
			return self._autologin_file(service[:-len(".conf.d")])
		if kind == "gdm-config":
			return self._gdm_config()
		return self._etc(service)

	def disable(self, target, keep_kde_config=False):
		"""
		keep_kde_config leaves the lock screen's Face option in place while
		some user's lock screen still starts with it: removing it then
		crashes kscreenlocker for that user. Face unlock itself stops
		working as the PAM stack is gone.
		"""
		state = self._load_state()
		plan = self._plan(target)
		if target == "auto-login":
			# Also when the display manager is gone: configuration left
			# behind would log in without a password once it comes back. The
			# configuration first, so nothing logs in while the keyring's
			# lines go.
			plan = [(manager + ".conf.d", "autologin") for manager in LOGIN_MANAGERS] + [("gdm", "gdm-config")]
			for manager in LOGIN_MANAGERS:
				plan += [(manager + "-autologin", "keyring-release"), (manager, "keyring-remember")]
			plan += [("gdm-autologin", "keyring-release"), ("gdm-password", "keyring-remember")]
		for service, kind in plan:
			if kind == "kde-config" and keep_kde_config:
				continue
			path = self._kind_path(service, kind)
			if path is None:
				continue
			self._revert(path, state, KEYRING_BEGINS if kind.startswith("keyring-") else (BEGIN,))
		self._save_state(state)

	def reset(self):
		"""
		Revert everything in the manifest, then strip leftovers of older
		versions (unmarked lines and their .linux-hello-backup files).
		Returns the list of files changed.
		"""
		changed = []
		# Before anything else, also with a broken manifest: configuration
		# left behind would log in without a password
		for autologin, manager in self._autologin_files().items():
			if os.path.isfile(autologin):
				self._forget_copied_autologin(manager, self._dropin_autologin_user(manager))
				self._remove(autologin)
				changed.append(autologin)
		for relative in GDM_CONFIGS:
			path = self._path(relative)
			if not os.path.isfile(path):
				continue
			text = self._read(path)
			if not has_block(text):
				continue
			cleaned = remove_block(text, (BEGIN,))
			if cleaned.strip() in ("", "[{}]".format(GDM_GROUP)):
				# Only our [daemon] group was in a file we created
				self._remove(path)
			else:
				self._write(path, cleaned)
			changed.append(path)
		try:
			state = self._load_state()
		except PamError:
			state = {"version": 1, "files": {}}
		for path in list(state["files"]):
			if self._revert(path, state):
				changed.append(path)

		etc = self._path(ETC_DIR)
		if os.path.isdir(etc):
			for name in sorted(os.listdir(etc)):
				path = os.path.join(etc, name)
				if name.endswith(".linux-hello-backup"):
					self._remove(path)
					changed.append(path)
					continue
				if not os.path.isfile(path):
					continue
				try:
					text = self._read(path)
				except (OSError, UnicodeDecodeError):
					continue
				if text.startswith(OWN_HEADER):
					self._remove(path)
					changed.append(path)
					continue
				if MODULE not in text:
					continue
				cleaned = remove_legacy_lines(remove_block(text))
				if cleaned != text:
					self._write(path, cleaned)
					changed.append(path)

		# A block whose manifest entry was lost
		config = self._path(KSCREENLOCKER_CONFIG)
		if os.path.isfile(config):
			text = self._read(config)
			if has_block(text):
				cleaned = remove_block(text)
				if cleaned.strip():
					self._write(config, cleaned)
				else:
					self._remove(config)
				changed.append(config)

		self._save_state(state)
		return changed

	# -- internals ---------------------------------------------------------
	def _enable_insert(self, service, target, state):
		path = self._etc(service)
		if os.path.isfile(path):
			text = self._read(path)
			action = "inserted"
		else:
			vendor = self._vendor(service)
			if vendor is None:
				raise PamError("PAM service {} not found".format(service))
			text = self._read(vendor)
			action = "copied"
		line = LOGIN_INSERT_LINE if service in PASSWORD_FIRST_SERVICES else INSERT_LINE
		new_text = insert_block(remove_block(text, (BEGIN,)), self._gates(text), line)
		if new_text == text:
			return
		action = self._action(path, text, action, state)
		self._write(path, new_text)
		state["files"][path] = {
			"action": action,
			"target": target,
			"sha256": _sha256(new_text),
		}

	def refresh(self):
		"""Bring the blocks of enabled targets up to date, after an upgrade"""
		for target in self.targets():
			if target["enabled"]:
				self.enable(target["id"])

	def _enable_own(self, service, target, state):
		path = self._etc(service)
		if os.path.isfile(path) and not self._service_enabled(service, "own"):
			raise PamError("{} exists and was not created by linux-hello".format(path))
		fingerprint, password, purpose = OWN_STACKS[service]
		source = self._find(fingerprint)
		from_fingerprint = source is not None
		if source is None:
			source = self._find(password)
		# Without our blocks: the password stack carries the login and
		# keyring blocks, which must not outlive their targets in a copy
		source_text = remove_block(self._read(source))
		text = build_own_stack(source_text, from_fingerprint, purpose, self._gates(source_text))
		if not (os.path.isfile(path) and self._read(path) == text):
			self._write(path, text)
		state["files"][path] = {
			"action": "created",
			"target": target,
			"sha256": _sha256(text),
		}

	def _enable_kde_config(self, target, state):
		path = self._path(KSCREENLOCKER_CONFIG)
		block = "\n".join(KSCREENLOCKER_BLOCK) + "\n"
		if os.path.isfile(path):
			text = self._read(path)
			if has_block(text):
				return
			separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
			new_text = text + separator + block
			action = "inserted"
		else:
			new_text = block
			action = "created"
		self._write(path, new_text)
		state["files"][path] = {
			"action": action,
			"target": target,
			"sha256": _sha256(new_text),
		}
		if action == "inserted":
			state["files"][path]["separator"] = separator

	def _enable_keyring(self, service, kind, target, state):
		path = self._etc(service)
		if os.path.isfile(path):
			text = self._read(path)
			action = "inserted"
		else:
			vendor = self._vendor(service)
			if vendor is None:
				raise PamError("PAM service {} not found".format(service))
			text = self._read(vendor)
			action = "copied"
		# Older lines are replaced, so upgrades reach them
		clean = remove_block(text, KEYRING_BEGINS)
		if kind == "keyring-release":
			new_text = insert_keyring_release(clean, keyring_companions(clean, self._keyring_sessions(clean)))
		else:
			new_text = insert_keyring_remember(clean)
		if new_text == text:
			return
		action = self._action(path, text, action, state)
		self._write(path, new_text)
		state["files"][path] = {
			"action": action,
			"target": target,
			"sha256": _sha256(new_text),
		}

	def _action(self, path, text, action, state):
		"""
		How the file came to be: from the manifest while one of our blocks is
		in it (an older version's, or another target's), else as found now.
		A file without our blocks may be the admin's own, never ours to delete.
		"""
		entry = state["files"].get(path)
		if entry is not None and any(has_block(text, begin) for begin in BEGINS):
			return entry.get("action", action)
		return action

	def autologin_user(self):
		"""The user our configuration logs in automatically, or None"""
		return self._dropin_autologin_user() or self._gdm_autologin_user()

	def _dropin_autologin_user(self, manager=None):
		"""The user named in our drop-in of a login manager, or of any"""
		for path, owner in self._autologin_files().items():
			if manager in (None, owner) and os.path.isfile(path):
				user = _ini_group(self._read(path), "Autologin").get("User")
				if user:
					return user
		return None

	def _gdm_autologin_user(self):
		"""The user named in our block of GDM's configuration, while it is on"""
		config = self._gdm_config()
		if config is None or not os.path.isfile(config):
			return None
		values = _ini_group("[{}]\n".format(GDM_GROUP) + "\n".join(block_lines(self._read(config))), GDM_GROUP)
		if not _true(values.get("AutomaticLoginEnable")):
			return None
		return values.get("AutomaticLogin") or None

	def _forget_copied_autologin(self, manager, user):
		"""
		The Login Screen settings save every value they see, ours from the
		drop-in too: into plasmalogin.conf, or for SDDM into the kde_settings
		drop-in. Take that copy out again, only while it names the same user.
		"""
		if not user:
			return
		for path in self._other_autologin_configs(manager):
			try:
				text = self._read(path)
			except (OSError, UnicodeDecodeError):
				continue
			out = []
			group = None
			for line in text.splitlines(keepends=True):
				stripped = line.strip()
				if stripped.startswith("[") and stripped.endswith("]"):
					group = stripped[1:-1]
				elif group == "Autologin" and "=" in stripped and not stripped.startswith("#"):
					key, value = stripped.split("=", 1)
					if key.strip() == "User" and value.strip() == user:
						continue
				out.append(line)
			if "".join(out) != text:
				self._write(path, "".join(out))

	def _autologin_user_for(self, user):
		user = user or self.autologin_user()
		if not user or user == "root" or not USER_NAME.fullmatch(user):
			raise PamError("auto-login needs a regular user")
		return user

	def _enable_autologin(self, manager, target, user, state):
		user = self._autologin_user_for(user)
		for path in self._other_autologin_configs(manager):
			try:
				named = _ini_group(self._read(path), "Autologin").get("User")
			except (OSError, UnicodeDecodeError):
				continue
			if named is None or named == user:
				# The same user is our own copy, saved there by the Login
				# Screen settings
				continue
			if named or self._read_after_ours(manager, path):
				# Another user is someone's decision; an empty value is none
				# (the Login Screen settings save one by default), unless it
				# is read after ours and so wins over it
				raise PamError("{} sets the user to log in automatically; change it in the Login Screen settings".format(path))
		lines = [
			AUTOLOGIN_HEADER + ": log in automatically, then lock the screen.",
			"# Removed again by 'linux-hello-cli disable auto-login'.",
			"[Autologin]",
			"User=" + user,
		]
		if os.path.isfile(self._path(WAYLAND_SESSION)):
			lines.append("Session=plasma")
		text = "\n".join(lines) + "\n"
		path = self._autologin_file(manager)
		if not (os.path.isfile(path) and self._read(path) == text):
			self._write(path, text)
		state["files"][path] = {
			"action": "created",
			"target": target,
			"sha256": _sha256(text),
		}

	def _enable_gdm_config(self, target, user, state):
		user = self._autologin_user_for(user)
		path = self._gdm_config()
		if path is None:
			raise PamError("GDM configuration file not found")
		exists = os.path.isfile(path)
		text = self._read(path) if exists else ""
		clean = remove_block(text, (BEGIN,))
		# An automatic login set up by hand or in Settings > Users is
		# someone's decision; the same user there is a copy of ours
		daemon = _ini_group(clean, GDM_GROUP)
		named = daemon.get("AutomaticLogin")
		if _true(daemon.get("AutomaticLoginEnable")) and named and named != user:
			raise PamError("{} logs in {} automatically; change it in the Users settings".format(path, named))
		new_text = insert_in_group(clean, GDM_GROUP, [BEGIN, "AutomaticLoginEnable=true", "AutomaticLogin=" + user, END])
		if new_text == text:
			return
		action = self._action(path, text, "inserted" if exists else "created", state)
		self._write(path, new_text)
		state["files"][path] = {
			"action": action,
			"target": target,
			"sha256": _sha256(new_text),
		}

	def _revert(self, path, state, begins=BEGINS):
		"""
		Undo our change to one file, only the blocks marked by begins.
		Returns True when the file changed.
		"""
		if not os.path.isfile(path):
			state["files"].pop(path, None)
			return False
		text = self._read(path)
		entry = state["files"].get(path)
		autologin = path in self._autologin_files()
		if autologin:
			self._forget_copied_autologin(self._autologin_files()[path], _ini_group(text, "Autologin").get("User"))
		# The drop-in is ours by its name, whatever was edited in it
		whole = autologin or text.startswith(OWN_HEADER)
		if not whole:
			partly = remove_block(text, begins)
			if any(has_block(partly, begin) for begin in BEGINS):
				# Another target's block stays, and with it the file
				if partly == text:
					return False
				self._write(path, partly)
				if entry is not None:
					entry["sha256"] = _sha256(partly)
				return True
		state["files"].pop(path, None)
		untouched = entry is not None and _sha256(text) == entry.get("sha256")

		if whole:
			# Whole files of ours, deleted even when edited: a drop-in left
			# behind would keep logging in without a password
			self._remove(path)
			return True

		cleaned = remove_block(text, begins)
		if entry is not None and entry.get("action") == "created":
			# Ours to delete, unless someone added their own settings to it
			if untouched or not cleaned.strip():
				self._remove(path)
				return True
			if cleaned != text:
				self._write(path, cleaned)
				return True
			return False

		if cleaned == text:
			return False
		separator = (entry or {}).get("separator", "")
		if separator and cleaned.endswith(separator):
			# The blank line written in front of our block
			cleaned = cleaned[:-len(separator)]
		if entry is not None and entry.get("action") == "copied" and untouched:
			# Nobody edited our copy: drop it so the vendor file applies again
			self._remove(path)
			return True
		self._write(path, cleaned)
		return True
