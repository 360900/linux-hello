# The login password kept for opening KWallet after logging in automatically.
#
# KWallet is encrypted with the user's password and opens at login when
# pam_kwallet5 gets it. Logging in automatically types no password, so the
# PAM module hands pam_kwallet5 this copy instead, taken from the last login
# with the password.
#
# Kept only while auto-login is on and only on an encrypted disk, where a
# copy of the disk is unreadable. Files are root only, and sealed to the
# TPM with systemd-creds when there is one, so the disk taken out and read
# elsewhere does not give it away either. Never logged.

import logging
import os
import shutil
import subprocess

log = logging.getLogger("linux-hello.secrets")

TPM_DEVICE = "/dev/tpmrm0"
# A password longer than this is not one
MAX_SIZE = 4096
# In front of a copy that is not sealed (no TPM)
PLAIN = b"plain\n"


class SecretError(Exception):
	pass


def _directory():
	import paths
	return os.path.join(str(paths.state_dir), "secrets")


def _path(user):
	return os.path.join(_directory(), user)


def _credential_name(user):
	return "linux-hello-login-" + user


def _can_seal():
	return os.path.exists(TPM_DEVICE) and shutil.which("systemd-creds") is not None


def _run(arguments, data):
	result = subprocess.run(arguments, input=data, capture_output=True, timeout=30)
	if result.returncode != 0:
		raise SecretError(result.stderr.decode(errors="replace").strip() or "systemd-creds failed")
	return result.stdout


def store(user, secret):
	if not secret or len(secret) > MAX_SIZE:
		raise SecretError("not a password")
	data = None
	if _can_seal():
		try:
			data = _run(["systemd-creds", "encrypt", "--with-key=tpm2", "--name=" + _credential_name(user), "-", "-"], secret)
		except (SecretError, subprocess.TimeoutExpired) as error:
			# Still on the encrypted disk, only not tied to this computer
			log.warning("Could not seal with the TPM, keeping it unsealed: %s", error)
	if data is None:
		data = PLAIN + secret
	directory = _directory()
	os.makedirs(directory, mode=0o700, exist_ok=True)
	os.chmod(directory, 0o700)
	temporary = _path(user) + ".new"
	descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
	try:
		with os.fdopen(descriptor, "wb") as file:
			file.write(data)
			file.flush()
			os.fsync(file.fileno())
		os.replace(temporary, _path(user))
	except BaseException:
		if os.path.exists(temporary):
			os.unlink(temporary)
		raise


def load(user):
	"""The kept password, or None"""
	try:
		descriptor = os.open(_path(user), os.O_RDONLY | os.O_NOFOLLOW)
	except FileNotFoundError:
		return None
	with os.fdopen(descriptor, "rb") as file:
		data = file.read(MAX_SIZE * 4)
	if data.startswith(PLAIN):
		return data[len(PLAIN):]
	if not shutil.which("systemd-creds"):
		return None
	try:
		return _run(["systemd-creds", "decrypt", "--name=" + _credential_name(user), "-", "-"], data)
	except (SecretError, subprocess.TimeoutExpired):
		# Secure Boot or the TPM changed: the next login with the password
		# keeps a new copy
		return None


def exists(user):
	return os.path.isfile(_path(user))


def forget(user):
	try:
		os.unlink(_path(user))
	except FileNotFoundError:
		pass
