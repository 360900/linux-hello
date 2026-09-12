# Guard against multiprocessing re-importing this module in child processes.
# Some GPU runtimes (e.g. OpenVINO) spawn workers via forkserver, which
# re-executes the main module as "__mp_main__". Without this guard, workers
# re-run the camera setup, hit EBUSY on /dev/video*, and break the parent
# process with "Failed to read camera" errors and PAM timeouts.
import sys as _sys
if __name__ == "__mp_main__":
	_sys.exit(0)

# Intercept Ctrl+C and exit gracefully
def handle_sigint(signum, frame):
	# Exit with the ABORT status, not 0: the PAM module maps status 0 to
	# PAM_SUCCESS, which would let Ctrl+C pass as a successful authentication
	raise SystemExit(12)

import signal
signal.signal(signal.SIGINT, handle_sigint)

# Import time so we can start timing asap
import time

timings = {
	"st": time.time()
}

import sys
import os

# Force singlethreaded BLAS. dlib's OpenBLAS/OpenMP matmuls can livelock when raced against this script.
# Overwrite any inherited value since a pre-set thread count > 1 keeps the livelock alive.
# Set LINUX_HELLO_BLAS_THREADS=1 in the environment to opt out of this (e.g. for CNN detector performance).
if "LINUX_HELLO_BLAS_THREADS" not in os.environ:
	os.environ["OPENBLAS_NUM_THREADS"] = "1"
	os.environ["OMP_NUM_THREADS"] = "1"
	os.environ["GOTO_NUM_THREADS"] = "1"

import json
import configparser
import dlib
import cv2
from datetime import timezone, datetime
import atexit
import subprocess
import snapshot
import numpy as np
import _thread as thread
import paths_factory
from recorders.video_capture import VideoCapture
from i18n import _

def exit(code=None):
	global ui_proc
	if "ui_proc" in globals():
		ui_proc.terminate()
	if code is not None:
		sys.exit(code)


def init_detector(lock):
	global face_detector, face_encoder, pose_predictor, use_openvino

	if not os.path.isfile(paths_factory.shape_predictor_5_face_landmarks_path()):
		print(_("Data files have not been downloaded, please run the following commands:"))
		print("\n\tcd " + paths_factory.dlib_data_dir_path())
		print("\tsudo ./install.sh\n")
		lock.release()
		exit(1)

	use_openvino = False
	try:
		import openvino_face
		if openvino_face.is_available():
			face_detector = openvino_face.FaceDetector("GPU")
			face_encoder = openvino_face.FaceEncoder("GPU")
			# OpenVINO embeddings (256-D) cannot be matched against models
			# enrolled with dlib (128-D); fall back to dlib instead of
			# crashing on np.dot at match time. Re-enroll with "linux-hello-cli add"
			# to switch to OpenVINO.
			model_dims = {len(e) for e in encodings}
			if model_dims and model_dims != {face_encoder.embedding_dim}:
				print("Enrolled models use {}-D encodings but the OpenVINO encoder outputs {}-D; falling back to dlib. Re-run 'sudo linux-hello-cli add' to re-enroll for OpenVINO.".format(
					"/".join(str(d) for d in sorted(model_dims)), face_encoder.embedding_dim), file=sys.stderr)
			else:
				use_openvino = True
	except Exception as e:
		print("OpenVINO init failed:", e, file=sys.stderr)

	if not use_openvino:
		if use_cnn:
			face_detector = dlib.cnn_face_detection_model_v1(paths_factory.mmod_human_face_detector_path())
		else:
			face_detector = dlib.get_frontal_face_detector()
		face_encoder = None

	pose_predictor = dlib.shape_predictor(paths_factory.shape_predictor_5_face_landmarks_path())
	if not use_openvino:
		dlib_encoder = dlib.face_recognition_model_v1(paths_factory.dlib_face_recognition_resnet_model_v1_path())
		globals()["dlib_encoder"] = dlib_encoder

	timings["ll"] = time.time() - timings["ll"]
	lock.release()


def make_snapshot(type):
	snapshot.generate(snapframes, [
		type + _(" LOGIN"),
		_("Date: ") + datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S UTC"),
		_("Scan time: ") + str(round(time.time() - timings["fr"], 2)) + "s",
		_("Frames: ") + str(frames) + " (" + str(round(frames / (time.time() - timings["fr"]), 2)) + "FPS)",
		_("Hostname: ") + os.uname().nodename,
		_("Best certainty value: ") + str(round(lowest_certainty * 10, 1))
	])


def send_to_ui(type, message):
	global ui_proc
	if "ui_proc" in globals():
		message = type + "=" + message + " \n"
		try:
			if ui_proc.poll() is None:
				ui_proc.stdin.write(bytearray(message.encode("utf-8")))
				ui_proc.stdin.flush()
		except IOError:
			pass


if len(sys.argv) < 2:
	exit(12)

user = sys.argv[1]
models = []
encodings = []
black_tries = 0
dark_tries = 0
frames = 0
snapframes = []
lowest_certainty = 10
face_detector = None
face_encoder = None
pose_predictor = None
use_openvino = False

try:
	models = json.load(open(paths_factory.user_model_path(user)))
	for model in models:
		encodings += model["data"]
except FileNotFoundError:
	exit(10)

if len(models) < 1:
	exit(10)

config = configparser.ConfigParser()
config.read(paths_factory.config_file_path())

use_cnn = config.getboolean("core", "use_cnn", fallback=False)
timeout = config.getint("video", "timeout", fallback=4)
# Parse the dark threshold; "auto" enables adaptive rejection which learns
# the scene's darkness baseline and needs no manual tuning
dark_threshold_raw = str(config.get("video", "dark_threshold", fallback="60")).strip().lower()
dark_threshold_auto = dark_threshold_raw in ("auto", "none")
if not dark_threshold_auto:
	dark_threshold = float(dark_threshold_raw)
dark_history = []
video_certainty = config.getfloat("video", "certainty", fallback=3.5) / 10
end_report = config.getboolean("debug", "end_report", fallback=False)
save_failed = config.getboolean("snapshots", "save_failed", fallback=False)
save_successful = config.getboolean("snapshots", "save_successful", fallback=False)
ui_stdout = config.getboolean("debug", "ui_stdout", fallback=False)
rotate = config.getint("video", "rotate", fallback=0)

ui_pipe = sys.stdout if ui_stdout else subprocess.DEVNULL

try:
	ui_proc = subprocess.Popen(["linux-hello", "--start-auth-ui"], stdin=subprocess.PIPE, stdout=ui_pipe, stderr=ui_pipe)
	atexit.register(exit)
except FileNotFoundError:
	pass

send_to_ui("M", _("Starting up..."))

timings["in"] = time.time() - timings["st"]

# Open camera FIRST, before loading OpenVINO
timings["ic"] = time.time()
video_capture = VideoCapture(config)
exposure = config.getint("video", "exposure", fallback=-1)
timings["ic"] = time.time() - timings["ic"]

# THEN load OpenVINO in background thread
timings["ll"] = time.time()
lock = thread.allocate_lock()
lock.acquire()
thread.start_new_thread(init_detector, (lock, ))

lock.acquire()
lock.release()
del lock

max_height = config.getfloat("video", "max_height", fallback=320.0)
height = video_capture.internal.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1
if rotate == 2:
	height = video_capture.internal.get(cv2.CAP_PROP_FRAME_WIDTH) or 1
scaling_factor = (max_height / height) or 1

timeout = config.getint("video", "timeout", fallback=4)
end_report = config.getboolean("debug", "end_report", fallback=False)

clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

send_to_ui("M", _("Identifying you..."))

frames = 0
valid_frames = 0
timings["fr"] = time.time()
dark_running_total = 0

while True:
	frames += 1

	ui_subtext = "Scanned " + str(valid_frames - dark_tries) + " frames"
	if (dark_tries > 1):
		ui_subtext += " (skipped " + str(dark_tries) + " dark frames)"
	send_to_ui("S", ui_subtext)

	if time.time() - timings["fr"] > timeout:
		if save_failed:
			make_snapshot(_("FAILED"))
		if dark_tries == valid_frames:
			print(_("All frames were too dark, please check dark_threshold in config"))
			print(_("Average darkness: {avg}, Threshold: {threshold}").format(avg=str(dark_running_total / max(1, valid_frames)), threshold=("auto" if dark_threshold_auto else str(dark_threshold))))
			exit(13)
		else:
			exit(11)

	frame, gsframe = video_capture.read_frame()
	gsframe = clahe.apply(gsframe)

	if save_failed or save_successful:
		if len(snapframes) < 3:
			snapframes.append(frame)

	hist = cv2.calcHist([gsframe], [0], None, [8], [0, 256])
	hist_total = np.sum(hist)
	darkness = (hist[0] / hist_total * 100)

	if (hist_total == 0) or (darkness == 100):
		black_tries += 1
		continue

	dark_running_total += darkness
	valid_frames += 1

	if dark_threshold_auto:
		# Adaptive mode: build a baseline from accepted frames, then reject
		# frames much darker than the baseline (flashing IR emitters produce
		# near-black frames). The cap at 95 keeps fully unlit frames out even
		# in very dim rooms where the lit baseline is high.
		if len(dark_history) >= 5:
			baseline = sorted(dark_history)[len(dark_history) // 2]
			if darkness > min(95.0, baseline * 1.25 + 10):
				dark_tries += 1
				continue
		dark_history.append(darkness)
		if len(dark_history) > 30:
			dark_history.pop(0)
	elif (darkness > dark_threshold):
		dark_tries += 1
		continue

	if scaling_factor != 1:
		frame = cv2.resize(frame, None, fx=scaling_factor, fy=scaling_factor, interpolation=cv2.INTER_AREA)
		gsframe = cv2.resize(gsframe, None, fx=scaling_factor, fy=scaling_factor, interpolation=cv2.INTER_AREA)

	if rotate == 1:
		if frames % 3 == 1:
			frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
			gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_COUNTERCLOCKWISE)
		if frames % 3 == 2:
			frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
			gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_CLOCKWISE)
	elif rotate == 2:
		if frames % 2 == 0:
			frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
			gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_COUNTERCLOCKWISE)
		else:
			frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
			gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_CLOCKWISE)

	face_locations = face_detector(gsframe, 1)

	for fl in face_locations:
		if use_cnn and not use_openvino:
			fl = fl.rect

		if use_openvino:
			face_encoding = face_encoder.encode(frame, fl)
			if face_encoding is None:
				continue
		else:
			face_landmark = pose_predictor(frame, fl)
			face_encoding = np.array(dlib_encoder.compute_face_descriptor(frame, face_landmark, 1))

		if use_openvino:
			# Cosine distance on L2-normalized 256-D embeddings, not the
			# Euclidean distance dlib uses: the scale differs, so the
			# "certainty" config value may need recalibrating for OpenVINO
			enc_array = np.array(encodings)
			matches = 1.0 - np.dot(enc_array, face_encoding)
		else:
			matches = np.linalg.norm(encodings - face_encoding, axis=1)

		match_index = np.argmin(matches)
		match = matches[match_index]

		if lowest_certainty > match:
			lowest_certainty = match

		if 0 < match < video_certainty:
			timings["tt"] = time.time() - timings["st"]
			timings["fl"] = time.time() - timings["fr"]

			if end_report:
				def print_timing(label, k):
					print("  %s: %dms" % (label, round(timings[k] * 1000)))

				print(_("Time spent"))
				print_timing(_("Starting up"), "in")
				print(_("  Open cam + load libs: %dms") % (round(max(timings["ll"], timings["ic"]) * 1000, )))
				print_timing(_("  Opening the camera"), "ic")
				print_timing(_("  Importing recognition libs"), "ll")
				print_timing(_("Searching for known face"), "fl")
				print_timing(_("Total time"), "tt")

				print(_("\nResolution"))
				width = video_capture.fw or 1
				print(_("  Native: %dx%d") % (height, width))
				scale_height, scale_width = frame.shape[:2]
				print(_("  Used: %dx%d") % (scale_height, scale_width))

				print(_("\nFrames searched: %d (%.2f fps)") % (frames, frames / timings["fl"]))
				print(_("Black frames ignored: %d ") % (black_tries, ))
				print(_("Dark frames ignored: %d ") % (dark_tries, ))
				print(_("Certainty of winning frame: %.3f") % (match * 10, ))
				print(_("Winning model: %d (\"%s\")") % (match_index, models[match_index]["label"]))

			if save_successful:
				make_snapshot(_("SUCCESSFUL"))

			if config.getboolean("rubberstamps", "enabled", fallback=False):
				import rubberstamps

				send_to_ui("S", "")

				if "ui_proc" not in vars():
					ui_proc = None

				rubberstamps.execute(config, ui_proc, {
					"video_capture": video_capture,
					"face_detector": face_detector,
					"pose_predictor": pose_predictor,
					"clahe": clahe
				})

			exit(0)

	if exposure != -1:
		video_capture.internal.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
		video_capture.internal.set(cv2.CAP_PROP_EXPOSURE, float(exposure))
