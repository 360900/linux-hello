# UVC extension unit (XU) controls: vendor and Microsoft camera controls
# that the standard V4L2 API does not expose, such as the IR emitter.
#
# Controls are read and written with the uvcvideo UVCIOC_CTRL_QUERY ioctl
# on the video node. Unit IDs differ between devices and even between the
# RGB and IR halves of one camera module, so units are found by the GUID
# in the USB descriptors instead of by number.

import ctypes
import fcntl
import os
import uuid

# Microsoft camera control extension unit (Microsoft UVC 1.5 extensions)
MS_CAMERA_CONTROL_XU = uuid.UUID("0f3f95dc-2632-4c4e-92c9-a04782f43bc8")
MSXU_CONTROL_FACE_AUTHENTICATION = 0x06

SET_CUR = 0x01
GET_CUR = 0x81
GET_MIN = 0x82
GET_MAX = 0x83
GET_RES = 0x84
GET_LEN = 0x85
GET_INFO = 0x86
GET_DEF = 0x87

# GET_INFO capability bits
INFO_GET = 0x01
INFO_SET = 0x02


class _Query(ctypes.Structure):
	"""struct uvc_xu_control_query from linux/uvcvideo.h"""
	_fields_ = [
		("unit", ctypes.c_uint8),
		("selector", ctypes.c_uint8),
		("query", ctypes.c_uint8),
		("size", ctypes.c_uint16),
		("data", ctypes.POINTER(ctypes.c_uint8)),
	]


# _IOWR('u', 0x21, struct uvc_xu_control_query)
UVCIOC_CTRL_QUERY = (3 << 30) | (ctypes.sizeof(_Query) << 16) | (ord("u") << 8) | 0x21


class Control:
	"""One XU control as found on a device"""

	def __init__(self, unit, selector, length, info, cur=None, minimum=None, maximum=None, default=None):
		self.unit = unit
		self.selector = selector
		self.length = length
		self.info = info
		self.cur = cur
		self.minimum = minimum
		self.maximum = maximum
		self.default = default

	@property
	def writable(self):
		return bool(self.info & INFO_SET)

	def __repr__(self):
		return "Control(unit={}, selector={}, len={}, info=0x{:02x}, cur={})".format(
			self.unit, self.selector, self.length, self.info, self.cur.hex() if self.cur else None)


class XuDevice:
	"""
	Open handle on a video node for XU queries. Keep it open while the
	camera streams: when every handle closes, the camera may power down
	and forget the values that were set.
	"""

	def __init__(self, node):
		self.node = node
		self.fd = os.open(node, os.O_RDWR)

	def close(self):
		if self.fd is not None:
			os.close(self.fd)
			self.fd = None

	def __enter__(self):
		return self

	def __exit__(self, *_exc):
		self.close()

	def query(self, unit, selector, code, data):
		buffer = (ctypes.c_uint8 * len(data))(*data)
		request = _Query(unit, selector, code, len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint8)))
		fcntl.ioctl(self.fd, UVCIOC_CTRL_QUERY, request)
		return bytes(buffer)

	def length(self, unit, selector):
		return int.from_bytes(self.query(unit, selector, GET_LEN, bytes(2)), "little")

	def info(self, unit, selector):
		return self.query(unit, selector, GET_INFO, bytes(1))[0]

	def get(self, unit, selector, code=GET_CUR, length=None):
		if length is None:
			length = self.length(unit, selector)
		return self.query(unit, selector, code, bytes(length))

	def set(self, unit, selector, value):
		self.query(unit, selector, SET_CUR, bytes(value))

	def control(self, unit, selector):
		"""Read everything about one control, without writing. Raises OSError."""
		length = self.length(unit, selector)
		info = self.info(unit, selector)
		values = {}
		for name, code in (("cur", GET_CUR), ("minimum", GET_MIN), ("maximum", GET_MAX), ("default", GET_DEF)):
			try:
				values[name] = self.get(unit, selector, code, length)
			except OSError:
				values[name] = None
		return Control(unit, selector, length, info, **values)

	def controls(self, units=None):
		"""
		Every XU control reachable through this node. uvcvideo answers for
		the units of this node's chain only, and only for the selectors the
		descriptors advertise, so unknown combinations fail without USB
		traffic. GET_INFO tells writability, no test write is needed.
		"""
		found = []
		for unit in (units if units is not None else range(1, 256)):
			for selector in range(1, 256):
				try:
					found.append(self.control(unit, selector))
				except OSError:
					continue
		return found


# -- USB topology ------------------------------------------------------------
def usb_interface_dir(node):
	"""sysfs directory of the USB interface behind a video node, or None"""
	path = "/sys/class/video4linux/{}/device".format(os.path.basename(os.path.realpath(node)))
	try:
		return os.path.realpath(path)
	except OSError:
		return None


def usb_identity(node):
	"""
	(vendor:product, video control interface number) of a video node, e.g.
	("5986:118c", 2), or None when the node is not a USB camera
	"""
	interface = usb_interface_dir(node)
	if not interface:
		return None
	device = os.path.dirname(interface)
	try:
		with open(os.path.join(interface, "bInterfaceNumber")) as file:
			number = int(file.read().strip(), 16)
		with open(os.path.join(device, "idVendor")) as file:
			vendor = file.read().strip()
		with open(os.path.join(device, "idProduct")) as file:
			product = file.read().strip()
	except (OSError, ValueError):
		return None
	return "{}:{}".format(vendor, product), number


def read_descriptors(node):
	interface = usb_interface_dir(node)
	if not interface:
		return None
	try:
		with open(os.path.join(os.path.dirname(interface), "descriptors"), "rb") as file:
			return file.read()
	except OSError:
		return None


def parse_video_control(descriptors):
	"""
	Map each video control interface to its extension units and streaming
	interfaces: {interface: {"units": {unit_id: guid}, "streaming": [n]}}
	"""
	result = {}
	current = None
	index = 0
	while index + 2 <= len(descriptors):
		length, kind = descriptors[index], descriptors[index + 1]
		if length < 2 or index + length > len(descriptors):
			break
		block = descriptors[index:index + length]
		if kind == 0x04 and length >= 9:
			# Interface descriptor: class 0x0e video, subclass 1 control
			current = block[2] if (block[5], block[6]) == (0x0E, 0x01) else None
			if current is not None:
				result.setdefault(current, {"units": {}, "streaming": []})
		elif kind == 0x24 and current is not None and length >= 3:
			subtype = block[2]
			if subtype == 0x01 and length >= 12:
				count = block[11]
				result[current]["streaming"] = list(block[12:12 + count])
			elif subtype == 0x06 and length >= 21:
				result[current]["units"][block[3]] = uuid.UUID(bytes_le=bytes(block[4:20]))
		index += length
	return result


def find_unit(node, guid, descriptors=None):
	"""Unit ID of the extension unit with this GUID on the node's chain"""
	identity = usb_identity(node)
	if descriptors is None:
		descriptors = read_descriptors(node)
	if identity is None or descriptors is None:
		return None
	interface = parse_video_control(descriptors).get(identity[1])
	if interface is None:
		return None
	for unit, unit_guid in interface["units"].items():
		if unit_guid == guid:
			return unit
	return None
