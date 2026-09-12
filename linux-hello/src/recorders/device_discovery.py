# Zero-config camera discovery.
# Enumerates V4L2 devices, probes them and ranks them so Linux Hello can
# pick the best camera without any manual configuration. IR cameras
# (grayscale output, dark background) are preferred over RGB webcams.

import os
import time


def candidate_nodes():
	"""Return a deduplicated list of v4l2 device nodes to probe"""
	nodes = []
	seen = set()

	# Resolve stable by-id and by-path links first, they carry the most info
	for stable_dir in ("/dev/v4l/by-id", "/dev/v4l/by-path"):
		try:
			for entry in os.listdir(stable_dir):
				real = os.path.realpath(os.path.join(stable_dir, entry))
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
		"ok": True,
		"frames": len(captured),
		"brightness": brightness,
		"saturation": saturation,
		"ir_score": saturation,
	}


def discover_cameras(probe=True, per_device_timeout=2.0):
	"""
	Enumerate and probe all v4l2 devices.

	Returns a list of probe dicts sorted best-first: working IR cameras,
	then working RGB cameras, then dead nodes.
	"""
	results = []
	for node in candidate_nodes():
		results.append(probe_device(node, timeout=per_device_timeout))

	def sort_key(r):
		if not r.get("ok"):
			return (2, 0, 0)
		# Working devices: grayscale (IR) first, then by darkness (IR
		# backgrounds stay dark), then by frame count
		return (1 if r.get("saturation", 255) > 16 else 0, r.get("brightness", 255), -r.get("frames", 0))

	results.sort(key=sort_key)
	return results


def autodetect_device(verbose=False):
	"""
	Pick the best camera device path for Linux Hello.

	Returns the device path string, or None when no usable camera exists.
	"""
	for result in discover_cameras():
		if verbose:
			print("Probed {}: {}".format(result["node"],
				"usable" if result.get("ok") else "unusable"))
		if result.get("ok"):
			return result["node"]
	return None
