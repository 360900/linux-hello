# Making sure a person asked for it.
#
# A face match only shows that someone sits in front of the camera, not
# that they want what is being asked. Any program running as the user can
# start sudo in a pseudo-terminal or replace the polkit agent and answer
# the prompt itself. So before scanning for admin prompts and sudo, the
# service waits for Enter on a physical keyboard, which no program without
# root can produce: uinput keyboards are virtual, and devices a user may
# write to (game controllers get that through logind) are left out, as
# writing to a device makes every reader see the event.
#
# Nothing waits for typed input meanwhile, like any PAM module: the user
# has a few seconds to press Enter, with at most one other key before it to
# forgive a slip. Esc skips at once. Anything more is typing (a password, a
# chat message). Without Enter, sign-in goes on with the next method
# (fingerprint, password) as if Linux Hello were not there.
#
# Privacy: keyboards are open only while such a wait runs. Each key press
# is compared to Enter and dropped; nothing about keys is logged, kept or
# sent anywhere. Keep it that way.

import os
import select
import stat
import struct
import time

INPUT_CLASS = "/sys/class/input"
UDEV_DATA = "/run/udev/data"
DEV_INPUT = "/dev/input"

# POSIX ACL entries in system.posix_acl_access (linux/posix_acl_xattr.h)
ACL_HEADER = struct.Struct("<I")
ACL_ENTRY = struct.Struct("<HHI")
ACL_USER = 0x02
ACL_GROUP = 0x08
ACL_WRITE = 0x02

# From linux/input-event-codes.h
EV_KEY = 0x01
KEY_ESC = 1
KEY_ENTER = 28
KEY_KPENTER = 96
# Codes from here on are mouse, touchpad and joystick buttons
BTN_MISC = 0x100

WINDOW_SECONDS = 3.0
OTHER_KEYS_ALLOWED = 1

# struct input_event: struct timeval, __u16 type, __u16 code, __s32 value
EVENT = struct.Struct("llHHi")
PRESSED = 1

# What wait_for_enter saw
ENTER = "enter"
SKIP = "skip"
TYPING = "typing"
NOTHING = "nothing"


def _bitmap(path):
	"""A sysfs capability bitmap (space separated hex words, highest first) as an int"""
	try:
		with open(path) as file:
			words = file.read().split()
	except OSError:
		return 0
	value = 0
	for word in words:
		value = (value << 64) | int(word, 16)
	return value


def is_physical_keyboard(device):
	"""device: /sys/class/input/eventN/device"""
	if "/devices/virtual/" in os.path.realpath(device):
		return False
	return bool(_bitmap(os.path.join(device, "capabilities", "key")) >> KEY_ENTER & 1)


def udev_says_keyboard(event, udev_data=UDEV_DATA):
	"""event: /sys/class/input/eventN. A keyboard to udev, and no joystick."""
	try:
		with open(os.path.join(event, "dev")) as file:
			number = file.read().strip()
		with open(os.path.join(udev_data, "c" + number)) as file:
			properties = set(file.read().splitlines())
	except OSError:
		return False
	return "E:ID_INPUT_KEYBOARD=1" in properties and not any(
		line.startswith("E:ID_INPUT_JOYSTICK=") for line in properties)


def users_may_write(node):
	"""Whether anyone but root and the owner may write to the device node"""
	try:
		info = os.stat(node)
	except OSError:
		return True
	if info.st_mode & stat.S_IWOTH:
		return True
	try:
		acl = os.getxattr(node, "system.posix_acl_access")
	except OSError:
		# No ACL: only the owner (root) and the group, input by convention,
		# whose members can read every key anyway
		return False
	for offset in range(ACL_HEADER.size, len(acl) - ACL_ENTRY.size + 1, ACL_ENTRY.size):
		tag, permissions, _id = ACL_ENTRY.unpack_from(acl, offset)
		if tag in (ACL_USER, ACL_GROUP) and permissions & ACL_WRITE:
			return True
	return False


def keyboards(input_class=INPUT_CLASS, udev_data=UDEV_DATA, dev_input=DEV_INPUT):
	"""/dev/input/eventN nodes of physical keyboards nobody but root can write to"""
	nodes = []
	try:
		names = sorted(os.listdir(input_class))
	except OSError:
		return nodes
	for name in names:
		event = os.path.join(input_class, name)
		node = os.path.join(dev_input, name)
		if (name.startswith("event") and is_physical_keyboard(os.path.join(event, "device"))
				and udev_says_keyboard(event, udev_data) and not users_may_write(node)):
			nodes.append(node)
	return nodes


def wait_for_enter(cancel, window=WINDOW_SECONDS, devices=keyboards, opener=None):
	"""
	ENTER once Enter is pressed within window seconds, with at most
	OTHER_KEYS_ALLOWED other keys before it. SKIP on Esc, TYPING on more
	keys. NOTHING at the end of the window, without keyboards or when
	cancel (a threading.Event) is set.
	"""
	opener = opener or (lambda node: os.open(node, os.O_RDONLY | os.O_NONBLOCK))
	deadline = time.monotonic() + window
	descriptors = []
	try:
		for node in devices():
			try:
				descriptors.append(opener(node))
			except OSError:
				continue
		other_keys = 0
		while descriptors and not cancel.is_set():
			left = deadline - time.monotonic()
			if left <= 0:
				return NOTHING
			ready, _, _ = select.select(descriptors, [], [], min(0.25, left))
			for descriptor in ready:
				try:
					data = os.read(descriptor, EVENT.size * 64)
				except OSError:
					continue
				for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
					_sec, _usec, kind, code, value = EVENT.unpack_from(data, offset)
					if kind != EV_KEY or value != PRESSED or code >= BTN_MISC:
						continue
					if code in (KEY_ENTER, KEY_KPENTER):
						return ENTER
					if code == KEY_ESC:
						return SKIP
					other_keys += 1
					if other_keys > OTHER_KEYS_ALLOWED:
						return TYPING
		return NOTHING
	finally:
		for descriptor in descriptors:
			os.close(descriptor)
