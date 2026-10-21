# What protects this computer when someone else has it: the facts behind the
# Security checklist in System Settings. Read only; nothing here changes the
# system.
#
# - An encrypted disk keeps files, and the password kept for KWallet, out of
#   reach of a USB stick (integration/encryption.py).
# - Secure Boot lets only signed bootloaders and kernels start, so a TPM
#   bound to it unlocks the disk for this system only.
# - The TPM unlocking the disk is what makes booting need no password.

import os

EFI_VARS = "/sys/firmware/efi/efivars"
# EFI_GLOBAL_VARIABLE
GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
SYS_TPM = "/sys/class/tpm"
CMDLINE = "/proc/cmdline"
CRYPTTABS = ("/etc/crypttab", "/etc/crypttab.initramfs")


def _efi_flag(name, efivars):
	"""A one byte EFI variable behind its 4 attribute bytes, or None"""
	try:
		with open(os.path.join(efivars, "{}-{}".format(name, GLOBAL)), "rb") as file:
			data = file.read(8)
	except OSError:
		return None
	return data[4] if len(data) >= 5 else None


def secure_boot(efivars=EFI_VARS):
	"""on, off, setup (no keys yet, see the guide), legacy (no UEFI) or unknown"""
	if not os.path.isdir(efivars):
		return "legacy"
	if _efi_flag("SecureBoot", efivars) == 1:
		return "on"
	if _efi_flag("SetupMode", efivars) == 1:
		return "setup"
	if _efi_flag("SecureBoot", efivars) == 0:
		return "off"
	return "unknown"


def tpm_version(sys_tpm=SYS_TPM):
	"""2 for a TPM 2.0, 1 for an old one, 0 without (or switched off)"""
	try:
		names = sorted(os.listdir(sys_tpm))
	except OSError:
		return 0
	for name in names:
		try:
			with open(os.path.join(sys_tpm, name, "tpm_version_major")) as file:
				return int(file.read().strip())
		except (OSError, ValueError):
			# Kernels before 5.6 have no version file: TPM 2.0 has a
			# resource manager device
			if os.path.exists(os.path.join("/dev", name.replace("tpm", "tpmrm"))):
				return 2
			return 1
	return 0


def tpm_unlock(cmdline=CMDLINE, crypttabs=CRYPTTABS):
	"""Whether the disk is set up to be unlocked by the TPM at boot"""
	for path in (cmdline,) + tuple(crypttabs):
		try:
			with open(path) as file:
				text = file.read()
		except OSError:
			continue
		for line in text.splitlines():
			if not line.lstrip().startswith("#") and "tpm2-device=" in line:
				return True
	return False
