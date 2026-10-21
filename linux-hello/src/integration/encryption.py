# Whether a folder is on an encrypted disk (LUKS, through dm-crypt).
#
# Features that keep a secret on disk, like a key that opens KWallet after a
# face match, are only safe there: on a plain disk anyone who boots the
# computer from a USB stick reads them.

import os

MOUNTINFO = "/proc/self/mountinfo"
SYS_BLOCK = "/sys/dev/block"
MAX_DEPTH = 8


def _unescape(field):
	# mountinfo writes spaces, tabs, newlines and backslashes as octal
	for code, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
		field = field.replace(code, char)
	return field


def mount_source(path, mountinfo=MOUNTINFO):
	"""The device a path's filesystem was mounted from, or None"""
	path = os.path.realpath(path)
	best, source = "", None
	try:
		with open(mountinfo) as file:
			lines = file.read().splitlines()
	except OSError:
		return None
	for line in lines:
		fields = line.split()
		if " - " not in line or len(fields) < 5:
			continue
		point = _unescape(fields[4])
		inside = path == point or path.startswith(point.rstrip("/") + "/")
		if inside and len(point) >= len(best):
			best = point
			after = line.split(" - ", 1)[1].split()
			source = _unescape(after[1]) if len(after) > 1 else None
	return source


def _encrypted_block(device, depth=0):
	"""device: a /sys/dev/block/M:m directory. dm-crypt, or on top of it."""
	if depth > MAX_DEPTH:
		return False
	try:
		with open(os.path.join(device, "dm", "uuid")) as file:
			if file.read().startswith("CRYPT-"):
				return True
	except OSError:
		pass
	slaves = os.path.join(device, "slaves")
	try:
		names = os.listdir(slaves)
	except OSError:
		return False
	# LVM or RAID: encrypted only when everything under it is
	return bool(names) and all(
		_encrypted_block(os.path.realpath(os.path.join(slaves, name)), depth + 1) for name in names)


def is_encrypted(path, mountinfo=MOUNTINFO, sys_block=SYS_BLOCK, stat=os.stat):
	source = mount_source(path, mountinfo)
	if not source or not source.startswith("/dev/"):
		return False
	try:
		number = stat(source).st_rdev
	except OSError:
		return False
	device = os.path.join(sys_block, "{}:{}".format(os.major(number), os.minor(number)))
	return _encrypted_block(os.path.realpath(device))
