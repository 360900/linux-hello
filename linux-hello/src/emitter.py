# IR emitter control.
#
# Many IR cameras only light their emitter when the host asks for it, which
# Windows does and Linux does not. Three ways to ask, tried in order:
#
# 1. Microsoft face authentication control (MSXU_CONTROL_FACE_AUTHENTICATION,
#    Microsoft UVC 1.5 extensions). Windows Hello cameras implement it: the
#    host selects "alternative frame illumination" (the emitter strobes on
#    every other frame) or "background subtraction" for the IR streaming
#    interface. Documented, so no guessing is involved.
# 2. A vendor control known to work for this camera model, from the shipped
#    database or from an earlier search on this machine.
# 3. An opt-in search through the camera's vendor controls (search() below).
#    It writes undocumented controls, so it never runs on its own.
#
# Values are set when a capture starts and restored when it ends. They are
# volatile: the camera forgets them when it powers down.
#
# The search and the flash analyzer follow linux-enable-ir-emitter by
# Maxime Dirksen (MIT), see NOTICE.

import json
import os
import tempfile
import time

import paths
from recorders import uvc_xu

# Face authentication modes (bmControlFlags), as KSCAMERA_EXTENDEDPROP_FACEAUTH_MODE_*
MODE_DISABLED = 0x1
MODE_ALTERNATIVE_FRAME_ILLUMINATION = 0x2
MODE_BACKGROUND_SUBTRACTION = 0x4

MODE_NAMES = {
	MODE_DISABLED: "disabled",
	MODE_ALTERNATIVE_FRAME_ILLUMINATION: "alternative frame illumination",
	MODE_BACKGROUND_SUBTRACTION: "background subtraction",
}

STATE_FILE = "emitter.json"
DATABASE_FILE = "emitter-devices.json"


# -- face authentication control ---------------------------------------------
def parse_face_auth(value):
	"""
	Entries of a face authentication payload: bNumEntries, then per
	streaming interface its number and bmControlFlags (little endian).
	Returns [(interface, flags, offset_of_flags, flags_size)].
	"""
	if not value:
		return []
	count = value[0]
	if count == 0:
		return []
	stride = (len(value) - 1) // count
	if stride < 2:
		return []
	entries = []
	for index in range(count):
		start = 1 + index * stride
		entries.append((
			value[start],
			int.from_bytes(value[start + 1:start + stride], "little"),
			start + 1,
			stride - 1,
		))
	return entries


def face_auth_target(current, maximum):
	"""
	The value that turns face authentication on for every IR streaming
	interface, preferring alternative frame illumination. Returns
	(value, mode) or None when no interface supports a face auth mode.
	"""
	supported = {interface: flags for interface, flags, _, _ in parse_face_auth(maximum)}
	target = bytearray(current)
	chosen = None
	for interface, flags, offset, size in parse_face_auth(current):
		modes = supported.get(interface, 0)
		for mode in (MODE_ALTERNATIVE_FRAME_ILLUMINATION, MODE_BACKGROUND_SUBTRACTION):
			if modes & mode:
				target[offset:offset + size] = mode.to_bytes(size, "little")
				chosen = chosen or mode
				break
	if chosen is None:
		return None
	return bytes(target), chosen


# -- stored vendor controls --------------------------------------------------
def _key(identity):
	return "{}/{}".format(identity[0], identity[1])


def _load(path):
	try:
		with open(path) as file:
			data = json.load(file)
	except (OSError, ValueError):
		return {"devices": {}}
	data.setdefault("devices", {})
	return data


def state_path():
	return os.path.join(str(paths.state_dir), STATE_FILE)


def database_path():
	return os.path.join(str(paths.data_dir), DATABASE_FILE)


def stored_controls(identity):
	"""Vendor controls for a camera: found on this machine first, then shipped"""
	for path in (state_path(), database_path()):
		entry = _load(path)["devices"].get(_key(identity))
		if entry and entry.get("controls"):
			return entry["controls"]
	return []


def _save_state(data):
	path = state_path()
	directory = os.path.dirname(path)
	os.makedirs(directory, exist_ok=True)
	descriptor, temporary = tempfile.mkstemp(prefix=".emitter-", dir=directory)
	try:
		with os.fdopen(descriptor, "w") as file:
			json.dump(data, file, indent=1, sort_keys=True)
			file.write("\n")
		os.chmod(temporary, 0o644)
		os.replace(temporary, path)
	except BaseException:
		if os.path.exists(temporary):
			os.unlink(temporary)
		raise


def remember(identity, controls):
	data = _load(state_path())
	entry = data["devices"].setdefault(_key(identity), {})
	entry["controls"] = controls
	_save_state(data)


def forget(identity):
	data = _load(state_path())
	removed = data["devices"].pop(_key(identity), None) is not None
	if removed:
		_save_state(data)
	return removed


def blacklisted(identity):
	entry = _load(state_path())["devices"].get(_key(identity), {})
	return {(item["guid"], item["selector"]) for item in entry.get("blacklist", [])}


def add_to_blacklist(identity, guid, selector):
	data = _load(state_path())
	entry = data["devices"].setdefault(_key(identity), {})
	items = entry.setdefault("blacklist", [])
	if {"guid": guid, "selector": selector} not in items:
		items.append({"guid": guid, "selector": selector})
	_save_state(data)


# -- session -----------------------------------------------------------------
class Session:
	"""
	The emitter switched on for one capture. Keep it open while frames are
	read, close() restores the camera's previous values.
	"""

	def __init__(self, device, method, alternating, restore):
		self.device = device
		self.method = method
		self.alternating = alternating
		self._restore = restore

	def close(self):
		if self.device is None:
			return
		for unit, selector, value in reversed(self._restore):
			try:
				self.device.set(unit, selector, value)
			except OSError:
				pass
		self.device.close()
		self.device = None


def _units_by_guid(node):
	identity = uvc_xu.usb_identity(node)
	descriptors = uvc_xu.read_descriptors(node)
	if identity is None or descriptors is None:
		return identity, {}
	interface = uvc_xu.parse_video_control(descriptors).get(identity[1], {"units": {}})
	return identity, {str(guid): unit for unit, guid in interface["units"].items()}


def plan(node, device):
	"""
	What would switch the emitter on, without writing anything. Returns a
	dict with "method", "alternating" and "writes" [(unit, selector, value)],
	or None when nothing is known for this camera.
	"""
	identity, units = _units_by_guid(node)
	msxu = units.get(str(uvc_xu.MS_CAMERA_CONTROL_XU))
	if msxu is not None:
		try:
			length = device.length(msxu, uvc_xu.MSXU_CONTROL_FACE_AUTHENTICATION)
			current = device.get(msxu, uvc_xu.MSXU_CONTROL_FACE_AUTHENTICATION, uvc_xu.GET_CUR, length)
			maximum = device.get(msxu, uvc_xu.MSXU_CONTROL_FACE_AUTHENTICATION, uvc_xu.GET_MAX, length)
		except OSError:
			current = None
		if current is not None:
			target = face_auth_target(current, maximum)
			if target is not None:
				value, mode = target
				return {
					"method": "face authentication ({})".format(MODE_NAMES[mode]),
					"alternating": mode == MODE_ALTERNATIVE_FRAME_ILLUMINATION,
					"writes": [(msxu, uvc_xu.MSXU_CONTROL_FACE_AUTHENTICATION, value)],
				}

	if identity is None:
		return None
	writes = []
	for control in stored_controls(identity):
		unit = units.get(control["guid"])
		if unit is None:
			return None
		writes.append((unit, control["selector"], bytes.fromhex(control["value"])))
	if writes:
		return {"method": "vendor control", "alternating": False, "writes": writes}
	return None


def activate(node):
	"""
	Switch the emitter on for a capture from node. Returns a Session, or None
	when the camera needs nothing or nothing is known. Never raises: a
	camera that cannot be configured is still used as it is.
	"""
	try:
		device = uvc_xu.XuDevice(node)
	except OSError:
		return None
	try:
		steps = plan(node, device)
		if steps is None:
			device.close()
			return None
		restore = []
		for unit, selector, value in steps["writes"]:
			previous = device.get(unit, selector, uvc_xu.GET_CUR, len(value))
			if previous != value:
				device.set(unit, selector, value)
				restore.append((unit, selector, previous))
		return Session(device, steps["method"], steps["alternating"], restore)
	except OSError:
		device.close()
		return None


def status(node):
	"""Read-only description of how the emitter would be driven"""
	try:
		with uvc_xu.XuDevice(node) as device:
			steps = plan(node, device)
	except OSError as error:
		return {"node": node, "method": None, "error": str(error)}
	return {"node": node, "method": steps["method"] if steps else None}


# -- flash analysis ----------------------------------------------------------
class FlashAnalyzer:
	"""
	Decides whether the emitter lights up, from how brightness changes
	between frames: a strobing emitter makes the frame to frame difference
	swing back and forth, a dark or steady scene does not. A reference
	taken with the emitter off scales the decision to each camera.

	The median swing is used, not the mean: cameras pulse their emitter
	once when streaming starts and auto exposure ramps up, and one such
	outlier in the reference would hide a real strobe.
	"""

	# A still scene gives a reference near zero, where sensor noise alone
	# would count as a flash: never go below this share of the brightness
	FLOOR = 0.02

	def __init__(self, coefficient=50):
		self.coefficient = coefficient
		self.reference = None
		self._reset()

	def _reset(self):
		self.previous_intensity = None
		self.previous_diff = None
		self.swings = []
		self.frames = 0
		self.total = 0.0

	def feed(self, intensity):
		"""intensity: the summed brightness of one frame"""
		self.frames += 1
		self.total += intensity
		if self.previous_intensity is not None:
			diff = intensity - self.previous_intensity
			if self.previous_diff is not None:
				self.swings.append(abs(diff - self.previous_diff))
			self.previous_diff = diff
		self.previous_intensity = intensity

	def variation(self):
		"""Median frame to frame swing of what was fed"""
		if not self.swings:
			return 0.0
		ordered = sorted(self.swings)
		return ordered[len(ordered) // 2]

	def set_reference(self):
		"""Use what was fed so far, with the emitter off, as the reference"""
		brightness = self.total / self.frames if self.frames else 0.0
		self.reference = max(self.variation() * self.coefficient, brightness * self.FLOOR)
		self._reset()

	def lit(self):
		"""True when the frames fed since the last reset show a working emitter"""
		lit = self.reference is not None and self.frames > 2 and self.variation() > self.reference
		self._reset()
		return lit


# Frames to drop when a stream starts: the emitter's start-up pulse and
# auto exposure settling
WARMUP_FRAMES = 5


def frame_intensity(frame):
	import numpy as np
	return float(np.sum(frame, dtype=np.float64))


# -- search ------------------------------------------------------------------
class SearchAborted(Exception):
	pass


def candidate_controls(node, device):
	"""Writable vendor controls on the node's chain, Microsoft's unit excluded"""
	identity, units = _units_by_guid(node)
	skip = blacklisted(identity) if identity else set()
	msxu = units.get(str(uvc_xu.MS_CAMERA_CONTROL_XU))
	guid_of = {unit: guid for guid, unit in units.items()}
	found = []
	for control in device.controls(sorted(guid_of)):
		if control.unit == msxu or not control.writable or control.cur is None:
			continue
		if (guid_of[control.unit], control.selector) in skip:
			continue
		found.append((guid_of[control.unit], control))
	return identity, found


def candidate_values(control, step=1, per_byte=5):
	"""
	Values to try for one control: each byte in turn is increased from its
	current value, a few steps at most and never past GET_MAX, while the
	other bytes keep their current value.
	"""
	current = bytearray(control.cur)
	maximum = control.maximum or bytes([255]) * len(current)
	for index in range(len(current)):
		value = bytearray(current)
		for _ in range(per_byte):
			if value[index] + step > min(255, maximum[index]):
				break
			value[index] += step
			yield bytes(value)


def search(node, read_frames, confirm=None, progress=None, frames=30):
	"""
	Look for a vendor control that lights the emitter. read_frames(n) must
	return n frame intensities from the camera, which streams while controls
	change. confirm(control, value) is asked when a value looks right and
	returns True to accept it. Each control is restored before moving on; a
	control that cannot be restored is blacklisted and the search stops.

	Returns the saved control as {"guid", "selector", "value"} or None.
	"""
	with uvc_xu.XuDevice(node) as device:
		identity, controls = candidate_controls(node, device)
		if identity is None:
			raise SearchAborted("{} is not a USB camera".format(node))

		analyzer = FlashAnalyzer()
		for intensity in read_frames(frames):
			analyzer.feed(intensity)
		analyzer.set_reference()

		for index, (guid, control) in enumerate(controls):
			if progress is not None:
				progress(index, len(controls), control)
			try:
				for value in candidate_values(control):
					try:
						device.set(control.unit, control.selector, value)
					except OSError:
						break
					for intensity in read_frames(frames):
						analyzer.feed(intensity)
					if analyzer.lit() and (confirm is None or confirm(control, value)):
						found = {"guid": guid, "selector": control.selector, "value": value.hex()}
						remember(identity, [found])
						return found
			finally:
				try:
					device.set(control.unit, control.selector, control.cur)
				except OSError:
					add_to_blacklist(identity, guid, control.selector)
					raise SearchAborted(
						"Control {} selector {} could not be restored and is now skipped. "
						"Shut the computer down completely, then search again.".format(control.unit, control.selector))
			time.sleep(0.1)
	return None
