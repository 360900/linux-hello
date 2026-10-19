# Waiting for the user to come back.
#
# The Plasma lock screen restarts face authentication as soon as it fails,
# so with nobody in front of the camera it would scan for as long as the
# screen stays locked. After a few failed scans the service waits for input
# activity instead, like Windows Hello: a key press, mouse or touchpad
# movement or opening the lid. Only the fact that an event arrived is used,
# its content is read to drain the device and dropped. Devices are opened
# only while such a wait is running.

import os
import select
import time

INPUT_CLASS = "/sys/class/input"

# Event types, from linux/input-event-codes.h
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_SW = 0x05

KEY_A = 30
BTN_LEFT = 0x110
BTN_TOUCH = 0x14A
SW_LID = 0x00

RESCAN_SECONDS = 5.0


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


def _bit(bitmap, number):
	return bool(bitmap >> number & 1)


def is_activity_device(capabilities):
	"""
	Keyboards, mice, touchpads and the lid switch. Sensors that report all
	the time (accelerometers, light sensors) have none of these.
	"""
	ev = _bitmap(os.path.join(capabilities, "ev"))
	key = _bitmap(os.path.join(capabilities, "key"))
	if _bit(ev, EV_KEY) and (_bit(key, KEY_A) or _bit(key, BTN_LEFT) or _bit(key, BTN_TOUCH)):
		return True
	if _bit(ev, EV_REL):
		return True
	if _bit(ev, EV_SW) and _bit(_bitmap(os.path.join(capabilities, "sw")), SW_LID):
		return True
	return False


def activity_devices(input_class=INPUT_CLASS):
	"""/dev/input/eventN nodes worth watching"""
	nodes = []
	try:
		names = sorted(os.listdir(input_class))
	except OSError:
		return nodes
	for name in names:
		if not name.startswith("event"):
			continue
		if is_activity_device(os.path.join(input_class, name, "device", "capabilities")):
			nodes.append(os.path.join("/dev/input", name))
	return nodes


def wait_for_activity(cancel, timeout, devices=activity_devices, opener=None):
	"""
	Block until an input event arrives (True), timeout seconds pass or
	cancel (a threading.Event) is set (False). Devices plugged in while
	waiting are picked up.
	"""
	opener = opener or (lambda node: os.open(node, os.O_RDONLY | os.O_NONBLOCK))
	deadline = time.monotonic() + timeout
	open_fds = {}
	try:
		next_scan = 0.0
		while not cancel.is_set():
			now = time.monotonic()
			if now >= deadline:
				return False
			if now >= next_scan:
				for node in devices():
					if node not in open_fds:
						try:
							open_fds[node] = opener(node)
						except OSError:
							continue
				next_scan = now + RESCAN_SECONDS
			if not open_fds:
				# Nothing to watch: keep checking for devices and cancellation
				time.sleep(min(0.25, max(0.0, deadline - now)))
				continue
			ready, _, _ = select.select(list(open_fds.values()), [], [], min(0.25, max(0.0, deadline - now)))
			for descriptor in ready:
				try:
					os.read(descriptor, 4096)
				except OSError:
					continue
				return True
		return False
	finally:
		for descriptor in open_fds.values():
			os.close(descriptor)
