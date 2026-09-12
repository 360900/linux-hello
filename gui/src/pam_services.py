# KDE/Plasma integration: wire pam_linux_hello.so into PAM services
# such as the kscreenlocker ("kde"), kcheckpass and system-login.
# Runs from the GUI, which is elevated through polkit.

import os
import re

PAM_DIR = "/etc/pam.d"

# Known services used by KDE/Plasma and common login flows
KNOWN_SERVICES = [
	"kde",              # kscreenlocker (lock screen) on Arch/Debian
	"kcheckpass",       # older screen lock helper
	"kscreensaver",     # legacy name
	"plasmalogin",      # Plasma Login Manager (Fedora 44+ KDE default)
	"sddm",             # display manager (some setups still use it)
	"system-login",     # Arch system login stack
	"common-auth",      # Debian/Ubuntu shared auth
	"polkit-1",         # admin prompts
]

MODULE_LINE = "auth\tsufficient\tpam_linux_hello.so\n"


def existing_services():
	"""Return the known services that actually exist on this system"""
	if not os.path.isdir(PAM_DIR):
		return []
	return [s for s in KNOWN_SERVICES if os.path.isfile(os.path.join(PAM_DIR, s))]


def is_enabled(service):
	try:
		with open(os.path.join(PAM_DIR, service), "r") as f:
			for line in f:
				if "pam_linux_hello.so" in line and not line.lstrip().startswith("#"):
					return True
	except OSError:
		pass
	return False


def _backup_path(service):
	return os.path.join(PAM_DIR, service + ".linux-hello-backup")


def enable(service):
	"""
	Insert 'auth sufficient pam_linux_hello.so' as the first auth line,
	after any leading comments. Returns (ok, message).
	"""
	path = os.path.join(PAM_DIR, service)
	if not os.path.isfile(path):
		return False, "PAM service {} does not exist".format(service)
	if is_enabled(service):
		return True, "Already enabled for {}".format(service)

	with open(path, "r") as f:
		lines = f.readlines()

	if not os.path.exists(_backup_path(service)):
		with open(_backup_path(service), "w") as f:
			f.writelines(lines)

	insert_at = None
	for index, line in enumerate(lines):
		stripped = line.strip()
		if stripped.startswith("auth") and "#" != stripped[0:1]:
			insert_at = index
			break

	if insert_at is None:
		lines.append(MODULE_LINE)
	else:
		lines.insert(insert_at, MODULE_LINE)

	with open(path, "w") as f:
		f.writelines(lines)
	return True, "Enabled for {}".format(service)


def disable(service):
	"""Remove all pam_linux_hello.so lines from a service. Returns (ok, message)."""
	path = os.path.join(PAM_DIR, service)
	if not os.path.isfile(path):
		return False, "PAM service {} does not exist".format(service)

	with open(path, "r") as f:
		lines = f.readlines()

	kept = [l for l in lines if "pam_linux_hello.so" not in l or l.lstrip().startswith("#")]
	if len(kept) == len(lines):
		return True, "Not enabled for {}".format(service)

	with open(path, "w") as f:
		f.writelines(kept)
	return True, "Disabled for {}".format(service)


def detect_desktop_environment():
	"""Best-effort detection of the running desktop environment"""
	env = os.environ.get("XDG_CURRENT_DESKTOP", "")
	if "KDE" in env or "Plasma" in env:
		return "KDE"
	for key in ("DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
		value = os.environ.get(key, "")
		if "plasma" in value.lower() or "kde" in value.lower():
			return "KDE"
	return env or "unknown"
