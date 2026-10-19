# Zero-config camera discovery.
# Enumerates V4L2 devices, probes them and ranks them so Linux Hello can
# pick the best camera without any manual configuration. IR cameras
# (grayscale output, dark background) are preferred over RGB webcams.

import os
import time

# Stable symlink for each real node, so the config survives renumbering
# of /dev/videoN across reboots and replugs. by-id wins over by-path.
_stable_paths = {}


def candidate_nodes():
	"""Return a deduplicated list of v4l2 device nodes to probe"""
	nodes = []
	seen = set()

	# Resolve stable by-id and by-path links first, they carry the most info
	for stable_dir in ("/dev/v4l/by-id", "/dev/v4l/by-path"):
		try:
			for entry in os.listdir(stable_dir):
				link = os.path.join(stable_dir, entry)
				real = os.path.realpath(link)
				_stable_paths.setdefault(real, link)
				if real not in seen:
					seen.add(real)
					nodes.append(real)
		except FileNotFoundError:
			pass

	# Fall back to scanning /dev/video* for anything not covered above
	try:
		for entry in os.listdir("/dev"):
			if not entry.startswith("video"):
				continue
			real = os.path.realpath(os.path.join("/dev", entry))
			if real not in seen:
				seen.add(real)
				nodes.append(real)
	except FileNotFoundError:
		pass

	return nodes


def stable_path(node):
	"""Return the /dev/v4l symlink for a node, or the node itself"""
	return _stable_paths.get(node, node)


def probe_device(node, frames_wanted=3, timeout=2.0):
	"""
	Open a device with OpenCV and capture a few frames.

	Returns a dict:
	{node, ok, frames, brightness, saturation, ir_score}

	- brightness: mean pixel value across frames (0-255)
	- saturation: mean per-pixel channel spread; near 0 means grayscale,
	  which strongly suggests an IR camera
	- ir_score: lower is more likely IR (grayscale output preferred)
	"""
	try:
		import cv2
		import numpy as np
	except ImportError:
		return {"node": node, "ok": False, "frames": 0}

	cap = cv2.VideoCapture(node, cv2.CAP_V4L2)
	if not cap.isOpened():
		return {"node": node, "ok": False, "frames": 0}

	captured = []
	deadline = time.time() + timeout
	while len(captured) < frames_wanted and time.time() < deadline:
		ok, frame = cap.read()
		if ok and frame is not None:
			captured.append(frame)

	cap.release()

	if not captured:
		return {"node": node, "ok": False, "frames": 0}

	brightness = float(np.mean(captured))
	saturation = float(np.mean([np.mean(np.std(f, axis=2)) if f.ndim == 3 else 0.0 for f in captured]))

	return {
		"node": node,
		"path": stable_path(node),
		"ok": True,
		"frames": len(captured),
		"brightness": brightness,
		"saturation": saturation,
		"ir_score": saturation,
	}


# Pixel formats only infrared sensors produce: 8 to 16 bit luminance, plus
# the interleaved stereo IR formats of depth cameras
IR_FORMATS = {"GREY", "Y8  ", "Y8I ", "Y10 ", "Y10B", "Y12 ", "Y12I", "Y16 ", "Y16I"}


def capture_formats(node):
	"""
	Pixel formats a node can capture, read with ioctls only: nothing is
	streamed, so no camera LED lights up. Returns [] for nodes that cannot
	capture video (UVC metadata nodes), None when the node cannot be opened.
	"""
	import errno
	import fcntl

	try:
		from recorders import v4l2
	except ImportError:
		import v4l2

	try:
		descriptor = os.open(node, os.O_RDWR | os.O_NONBLOCK)
	except OSError:
		return None
	try:
		capability = v4l2.v4l2_capability()
		fcntl.ioctl(descriptor, v4l2.VIDIOC_QUERYCAP, capability)
		# The bundled bindings predate device_caps: it is reserved[0], valid
		# when V4L2_CAP_DEVICE_CAPS is set. It describes this node, while
		# capabilities covers every node of the physical device.
		caps = capability.capabilities
		if caps & 0x80000000:
			caps = capability.reserved[0]
		if not caps & v4l2.V4L2_CAP_VIDEO_CAPTURE:
			return []

		formats = []
		for index in range(64):
			description = v4l2.v4l2_fmtdesc()
			description.index = index
			description.type = v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE
			try:
				fcntl.ioctl(descriptor, v4l2.VIDIOC_ENUM_FMT, description)
			except OSError as error:
				if error.errno == errno.EINVAL:
					break
				return None
			code = description.pixelformat
			formats.append("".join(chr((code >> shift) & 0xFF) for shift in (0, 8, 16, 24)))
		return formats
	except OSError:
		return None
	finally:
		os.close(descriptor)


def is_infrared(result):
	"""IR by advertised format; by grayscale frames when formats are unknown"""
	formats = result.get("formats")
	if formats:
		return any(fmt in IR_FORMATS for fmt in formats)
	return result.get("saturation", 255) <= 16


def _sort_key(result):
	if not result.get("ok"):
		return (2, 0, 0)
	# Working devices: infrared first, then by darkness (IR backgrounds stay
	# dark), then by frame count
	return (0 if is_infrared(result) else 1, result.get("brightness", 255), -result.get("frames", 0))


def discover_cameras(probe_all=True, per_device_timeout=2.0):
	"""
	Enumerate and probe v4l2 capture devices.

	Nodes that cannot capture are skipped without streaming. With
	probe_all=False and at least one device advertising an infrared format,
	only those are opened, so colour webcams stay off. Returns probe dicts
	sorted best-first: working IR cameras, then RGB cameras, then dead nodes.
	"""
	nodes = []
	for node in candidate_nodes():
		formats = capture_formats(node)
		if formats == []:
			continue
		nodes.append((node, formats))

	infrared = [entry for entry in nodes if entry[1] and any(f in IR_FORMATS for f in entry[1])]
	if not probe_all and infrared:
		nodes = infrared

	results = []
	for node, formats in nodes:
		result = probe_device(node, timeout=per_device_timeout)
		result["formats"] = formats or []
		result.setdefault("path", stable_path(node))
		result["infrared"] = is_infrared(result) if result.get("ok") else bool(formats and any(f in IR_FORMATS for f in formats))
		results.append(result)

	results.sort(key=_sort_key)
	return results


# The daemon keeps running between requests: remember the pick instead of
# opening every camera again for each authentication
_autodetected = None


def autodetect_device(verbose=False, use_cache=True):
	"""
	Pick the best camera device path for Linux Hello. Infrared cameras are
	tried first and colour cameras are only opened when no infrared one
	works. Returns the device path string, or None when no usable camera
	exists.
	"""
	global _autodetected
	if use_cache and _autodetected and os.path.exists(_autodetected):
		return _autodetected

	results = discover_cameras(probe_all=False)
	if not any(r.get("ok") for r in results):
		# Advertised IR devices did not deliver frames: try everything
		results = discover_cameras(probe_all=True)

	for result in results:
		if verbose:
			print("Probed {}: {}".format(result["node"],
				"usable" if result.get("ok") else "unusable"))
		if result.get("ok"):
			_autodetected = result["path"]
			return _autodetected
	return None


def forget_autodetected():
	"""Drop the cached pick, for example after the camera failed to open"""
	global _autodetected
	_autodetected = None
