# linux-hello-cli emitter status|test|search|forget|export
# Runs as root next to the service: it opens the IR camera itself.

import json
import sys

import emitter
from i18n import _
from recorders import device_discovery, uvc_xu

WARNING = _("""The search writes undocumented settings to your camera, one after another,
and watches whether the IR light starts flashing. Most cameras simply ignore
wrong values, but writing to undocumented controls can leave some cameras
unresponsive until a full shutdown, and on rare models it could damage the
camera's firmware permanently. Linux Hello restores every value it tried
and skips settings that could not be restored.

Only continue if your camera is not listed as supported and you accept this risk.""")


def ir_nodes():
	"""IR capture nodes, by advertised pixel format"""
	nodes = []
	for node in device_discovery.candidate_nodes():
		formats = device_discovery.capture_formats(node)
		if formats and any(fmt in device_discovery.IR_FORMATS for fmt in formats):
			nodes.append(node)
	return nodes


def pick_node(args):
	if len(args.arguments) > 1:
		return args.arguments[1]
	nodes = ir_nodes()
	if not nodes:
		print(_("No infrared camera found"))
		sys.exit(1)
	return nodes[0]


class FrameReader:
	"""Streams a camera and returns summed frame brightness"""

	def __init__(self, node):
		import cv2
		self.cv2 = cv2
		self.capture = cv2.VideoCapture(node, cv2.CAP_V4L2)
		if not self.capture.isOpened():
			raise OSError("cannot open {}".format(node))
		for _ in range(emitter.WARMUP_FRAMES):
			self.capture.read()

	def __call__(self, count):
		values = []
		while len(values) < count:
			ok, frame = self.capture.read()
			if ok:
				values.append(emitter.frame_intensity(frame))
		return values

	def close(self):
		self.capture.release()


def cmd_status(args):
	nodes = [args.arguments[1]] if len(args.arguments) > 1 else ir_nodes()
	if not nodes:
		print(_("No infrared camera found"))
		return 1
	for node in nodes:
		identity = uvc_xu.usb_identity(node)
		state = emitter.status(node)
		label = "{} ({})".format(node, identity[0]) if identity else node
		if state.get("method"):
			print(_("{}: emitter driven by {}").format(label, state["method"]))
		else:
			print(_("{}: no known way to drive the emitter, see: linux-hello-cli emitter search").format(label))
	return 0


def cmd_test(args):
	node = pick_node(args)
	analyzer = emitter.FlashAnalyzer()

	# Reference with the camera as Linux left it
	reader = FrameReader(node)
	try:
		for value in reader(30):
			analyzer.feed(value)
	finally:
		reader.close()
	analyzer.set_reference()

	# Same again with the emitter switched on before streaming starts
	session = emitter.activate(node)
	print(_("Method: {}").format(session.method if session else _("none, camera used as it is")))
	try:
		reader = FrameReader(node)
		try:
			for value in reader(60):
				analyzer.feed(value)
		finally:
			reader.close()
	finally:
		if session is not None:
			session.close()

	lit = analyzer.lit()
	print(_("The IR emitter flashes.") if lit else _("No emitter flashing detected."))
	return 0 if lit else 1


def cmd_search(args):
	node = pick_node(args)
	state = emitter.status(node)
	if state.get("method"):
		print(_("{} already has a way to drive the emitter: {}").format(node, state["method"]))
		print(_("Check it with: linux-hello-cli emitter test"))
		return 0

	print(WARNING)
	print()
	if input(_("Type yes to start: ")).strip().lower() != "yes":
		print(_("Aborted"))
		return 1

	def progress(index, total, control):
		print(_("Trying control {}/{} (unit {}, selector {})").format(index + 1, total, control.unit, control.selector))

	def confirm(control, value):
		answer = input(_("  The frames changed. Is the red IR light flashing now? [y/N]: "))
		return answer.strip().lower() == "y"

	reader = FrameReader(node)
	try:
		found = emitter.search(node, reader, confirm=confirm, progress=progress)
	except emitter.SearchAborted as error:
		print(error)
		return 1
	finally:
		reader.close()

	if found is None:
		print(_("No control switched the emitter on."))
		return 1
	print(_("Found it. Linux Hello now switches the emitter on whenever it scans."))
	print(_("Please share it so other owners of this camera get it automatically: linux-hello-cli emitter export"))
	return 0


def cmd_forget(args):
	node = pick_node(args)
	identity = uvc_xu.usb_identity(node)
	if identity and emitter.forget(identity):
		print(_("Forgot the emitter setting for {}").format(identity[0]))
		return 0
	print(_("Nothing stored for {}").format(node))
	return 1


def cmd_export(args):
	node = pick_node(args)
	identity = uvc_xu.usb_identity(node)
	controls = emitter.stored_controls(identity) if identity else []
	if not controls:
		print(_("Nothing stored for {}").format(node))
		return 1
	print(json.dumps({emitter._key(identity): {"controls": controls}}, indent=2))
	return 0


COMMANDS = {
	"status": cmd_status,
	"test": cmd_test,
	"search": cmd_search,
	"forget": cmd_forget,
	"export": cmd_export,
}


def run(args):
	action = args.arguments[0] if args.arguments else "status"
	if action not in COMMANDS:
		print(_("Usage: linux-hello-cli emitter status|test|search|forget|export [device]"))
		return 1
	return COMMANDS[action](args)
