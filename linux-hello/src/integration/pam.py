# PAM integration: wire pam_linux_hello.so into the services behind each
# user facing target (lock screen, admin prompts, sudo, login screen).
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

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

MODULE = "pam_linux_hello.so"

BEGIN = "# BEGIN linux-hello (managed by linux-hello, do not edit)"
END = "# END linux-hello"

# A failing, unavailable or missing module falls through to the next auth
# line, so the password keeps working whatever happens to Linux Hello
INSERT_LINE = "auth\t[success=done default=ignore]\t" + MODULE

# In a non-interactive stack nothing else may grant access: anything but a
# match (including PAM_IGNORE) fails the stack
OWN_STACK_LINE = "auth\t[success=ok default=die]\t" + MODULE + " no_password"

# First lines of every service file we create. Such a file is only ever
# deleted as a whole: stripping our module out of it would leave a stack
# that succeeds without any authentication.
OWN_HEADER = "#%PAM-1.0\n# Created by linux-hello"

ETC_DIR = "etc/pam.d"

# Plasma 6.8+ only offers face unlock on the lock screen when this is set;
# /etc/xdg is the system-wide default users can still override
KSCREENLOCKER_CONFIG = "etc/xdg/kscreenlockerrc"
KSCREENLOCKER_BLOCK = [BEGIN, "[Authenticators]", "Face=true", END]
VENDOR_DIRS = ("usr/lib/pam.d", "usr/etc/pam.d")
STATE_FILE = "var/lib/linux-hello/pam-state.json"

# Greeter binaries that may know the "kde-face" non-interactive service
KSCREENLOCKER_GREETERS = (
	"usr/lib/kscreenlocker_greet",
	"usr/libexec/kscreenlocker_greet",
	"usr/lib/x86_64-linux-gnu/libexec/kscreenlocker_greet",
	"usr/lib/aarch64-linux-gnu/libexec/kscreenlocker_greet",
	"usr/lib64/libexec/kscreenlocker_greet",
)

# Modules that must not end up in a non-interactive face stack copied from
# kde-fingerprint: the fingerprint module itself and the unconditional permit
OWN_STACK_DROP = ("pam_fprintd.so", "pam_permit.so")

TARGETS = [
	{
		"id": "lock-screen",
		"label": "Lock screen",
		"description": "Unlock the Plasma lock screen with your face.",
	},
	{
		"id": "admin",
		"label": "Admin prompts",
		"description": "Approve polkit prompts, including run0, with your face.",
	},
	{
		"id": "sudo",
		"label": "Terminal (sudo)",
		"description": "Authenticate sudo in a terminal with your face.",
	},
	{
		"id": "login",
		"label": "Login screen",
		"description": "Log in with your face. KWallet stays locked until you type your password.",
	},
]


class PamError(Exception):
	pass


def _sha256(text):
	return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _module_of(line):
	"""Return (type, module) of a PAM config line, or (None, None)"""
	stripped = line.strip()
	if not stripped or stripped.startswith("#"):
		return None, None
	if stripped.startswith("@include"):
		parts = stripped.split()
		return "@include", parts[1] if len(parts) > 1 else None

	parts = stripped.split()
	kind = parts[0].lstrip("-")
	rest = parts[1:]
	# Skip the control field, which is either one word or a [ ... ] group
	if rest and rest[0].startswith("["):
		while rest and not rest[0].endswith("]"):
			rest = rest[1:]
	rest = rest[1:]
	return kind, rest[0] if rest else None


def _is_auth_line(line):
	kind, module = _module_of(line)
	if kind == "auth":
		return True
	return kind == "@include" and module == "common-auth"


def has_block(text):
	return BEGIN in text


def insert_block(text):
	"""Insert our block before the first auth line of a service file"""
	if has_block(text):
		return text
	lines = text.splitlines(keepends=True)
	if lines and not lines[-1].endswith("\n"):
		lines[-1] += "\n"
	block = [BEGIN + "\n", INSERT_LINE + "\n", END + "\n"]

	for index, line in enumerate(lines):
		if _is_auth_line(line):
			return "".join(lines[:index] + block + lines[index:])
	raise PamError("no auth line found")


def remove_block(text):
	"""Remove every marked block, leaving the rest byte for byte"""
	out = []
	inside = False
	for line in text.splitlines(keepends=True):
		stripped = line.strip()
		if stripped == BEGIN:
			inside = True
			continue
		if inside:
			if stripped == END:
				inside = False
			continue
		out.append(line)
	return "".join(out)


def remove_legacy_lines(text):
	"""Drop unmarked pam_linux_hello.so lines written by older versions"""
	out = []
	for line in text.splitlines(keepends=True):
		_kind, module = _module_of(line)
		if module and os.path.basename(module) == MODULE:
			continue
		out.append(line)
	return "".join(out)


def build_own_stack(source_text, from_fingerprint):
	"""
	Build the kde-face service from kde-fingerprint (or from kde when that
	does not exist). Keeps the account, password and session lines and the
	protective auth checks (nologin, faillock, shells), replaces the
	fingerprint module with ours and drops anything that could grant access
	without a face match.
	"""
	header = [
		"#%PAM-1.0\n",
		"# Created by linux-hello: face unlock on the Plasma lock screen.\n",
		"# Removed again by 'linux-hello-cli reset-system'.\n",
	]
	body = []
	placed = False
	for line in source_text.splitlines(keepends=True):
		if not line.endswith("\n"):
			line += "\n"
		if line.startswith("#%PAM"):
			continue
		kind, module = _module_of(line)
		if not _is_auth_line(line):
			body.append(line)
			continue
		keep = (
			from_fingerprint
			and kind == "auth"
			and module is not None
			and module.endswith(".so")
			and os.path.basename(module) not in OWN_STACK_DROP
		)
		if not placed and not keep:
			body.append(OWN_STACK_LINE + "\n")
			placed = True
		elif keep:
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
		descriptor, temporary = tempfile.mkstemp(prefix=".linux-hello-", dir=directory)
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

	def _plan(self, target):
		"""
		Return the list of (service, kind) a target maps to on this system,
		kind being "own" (a file we create) or "insert" (a marked block).
		"""
		if target == "lock-screen":
			if self._kde_face_supported() and (self._find("kde-fingerprint") or self._find("kde")):
				return [("kde-face", "own"), ("kscreenlockerrc", "kde-config")]
			return [("kde", "insert")] if self._find("kde") else []
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
			for service in ("plasmalogin", "sddm"):
				if self._find(service):
					return [(service, "insert")]
			return []
		raise PamError("unknown target {}".format(target))

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
		path = self._etc(service)
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
			entry["available"] = bool(plan)
			entry["services"] = [service for service, kind in plan if kind != "kde-config"]
			entry["enabled"] = bool(plan) and all(self._service_enabled(s, k) for s, k in plan)
			result.append(entry)
		return result

	def enable(self, target):
		plan = self._plan(target)
		if not plan:
			raise PamError("target {} is not available on this system".format(target))
		state = self._load_state()
		for service, kind in plan:
			if kind == "own":
				self._enable_own(service, target, state)
			elif kind == "kde-config":
				self._enable_kde_config(target, state)
			else:
				self._enable_insert(service, target, state)
		self._save_state(state)

	def disable(self, target):
		state = self._load_state()
		for service, kind in self._plan(target):
			path = self._path(KSCREENLOCKER_CONFIG) if kind == "kde-config" else self._etc(service)
			self._revert(path, state)
		self._save_state(state)

	def reset(self):
		"""
		Revert everything in the manifest, then strip leftovers of older
		versions (unmarked lines and their .linux-hello-backup files).
		Returns the list of files changed.
		"""
		state = self._load_state()
		changed = []
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
		if has_block(text):
			return
		new_text = insert_block(text)
		self._write(path, new_text)
		state["files"][path] = {
			"action": action,
			"target": target,
			"sha256": _sha256(new_text),
		}

	def _enable_own(self, service, target, state):
		path = self._etc(service)
		if os.path.isfile(path) and not self._service_enabled(service, "own"):
			raise PamError("{} exists and was not created by linux-hello".format(path))
		source = self._find("kde-fingerprint")
		from_fingerprint = source is not None
		if source is None:
			source = self._find("kde")
		text = build_own_stack(self._read(source), from_fingerprint)
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

	def _revert(self, path, state):
		"""Undo our change to one file. Returns True when the file changed."""
		entry = state["files"].pop(path, None)
		if not os.path.isfile(path):
			return False
		text = self._read(path)
		untouched = entry is not None and _sha256(text) == entry.get("sha256")

		if text.startswith(OWN_HEADER):
			self._remove(path)
			return True

		cleaned = remove_block(text)
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
