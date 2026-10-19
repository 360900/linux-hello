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

# Face verification for the PAM module: compare.py <user>
# Communicates only through its exit status, see VerificationResult.

import time

started = time.time()

import sys
import os

# Force singlethreaded BLAS. dlib's OpenBLAS/OpenMP matmuls can livelock when raced against this script.
# Overwrite any inherited value since a pre-set thread count > 1 keeps the livelock alive.
# Set LINUX_HELLO_BLAS_THREADS=1 in the environment to opt out of this (e.g. for CNN detector performance).
if "LINUX_HELLO_BLAS_THREADS" not in os.environ:
	os.environ["OPENBLAS_NUM_THREADS"] = "1"
	os.environ["OMP_NUM_THREADS"] = "1"
	os.environ["GOTO_NUM_THREADS"] = "1"

import atexit
import subprocess
from datetime import timezone, datetime

import models as model_store
import paths_factory
import settings
import snapshot
from engine import Engine
from i18n import _
from verification import VerificationResult

ui_proc = None


def close_ui():
	if ui_proc is not None:
		ui_proc.terminate()


def send_to_ui(type, message):
	if ui_proc is None:
		return
	try:
		if ui_proc.poll() is None:
			ui_proc.stdin.write((type + "=" + message + " \n").encode("utf-8"))
			ui_proc.stdin.flush()
	except IOError:
		pass


if len(sys.argv) < 2:
	sys.exit(VerificationResult.ABORT)

user = sys.argv[1]

try:
	models = model_store.load(user)
except model_store.ModelError:
	sys.exit(VerificationResult.NO_FACE_MODEL)
encodings = [encoding for model in models for encoding in model["data"]]
if not encodings:
	sys.exit(VerificationResult.NO_FACE_MODEL)

config = settings.load()
end_report = config.getboolean("debug", "end_report", fallback=False)
save_failed = config.getboolean("snapshots", "save_failed", fallback=False)
save_successful = config.getboolean("snapshots", "save_successful", fallback=False)
ui_pipe = sys.stdout if config.getboolean("debug", "ui_stdout", fallback=False) else subprocess.DEVNULL

try:
	ui_proc = subprocess.Popen(["linux-hello", "--start-auth-ui"], stdin=subprocess.PIPE, stdout=ui_pipe, stderr=ui_pipe)
	atexit.register(close_ui)
except FileNotFoundError:
	pass

send_to_ui("M", _("Identifying you..."))

engine = Engine(config)
snapframes = [] if (save_failed or save_successful) else None


def progress(update):
	text = _("Scanned {} frames").format(update["frames"] - update["dark_frames"])
	if update["dark_frames"] > 1:
		text += _(" (skipped {} dark frames)").format(update["dark_frames"])
	send_to_ui("S", text)


def rubberstamps_hook(capture, backend):
	if not config.getboolean("rubberstamps", "enabled", fallback=False):
		return True
	import cv2
	import rubberstamps

	send_to_ui("S", "")
	# Exits the process with the final status
	rubberstamps.execute(config, ui_proc, {
		"video_capture": capture,
		"face_detector": backend.detector,
		"pose_predictor": getattr(backend, "pose_predictor", None),
		"clahe": cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)),
	})


def make_snapshot(kind, stats):
	summary = stats.as_dict()
	snapshot.generate(snapframes, [
		kind + _(" LOGIN"),
		_("Date: ") + datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S UTC"),
		_("Scan time: ") + str(summary["seconds"]) + "s",
		_("Frames: ") + str(stats.frames) + " (" + str(summary["fps"]) + "FPS)",
		_("Hostname: ") + os.uname().nodename,
		_("Best certainty value: ") + str(summary["best_certainty"]),
	])


def print_report(stats):
	summary = stats.as_dict()
	print(_("Time spent"))
	print(_("  Starting up and opening the camera: %dms") % round((time.time() - started - stats.elapsed) * 1000))
	print(_("  Searching for known face: %dms") % round(stats.elapsed * 1000))
	print(_("\nResolution"))
	print(_("  Native: %dx%d") % (stats.width, stats.height))
	print(_("  Used: %dx%d") % (stats.used_width, stats.used_height))
	print(_("\nFrames searched: %d (%.2f fps)") % (stats.frames, summary["fps"]))
	print(_("Black frames ignored: %d ") % stats.black)
	print(_("Dark frames ignored: %d ") % stats.dark)
	print(_("Certainty of winning frame: %s") % summary["best_certainty"])


try:
	result, stats = engine.verify(
		encodings,
		on_progress=progress,
		keep_frames=snapframes,
		after_match=rubberstamps_hook,
	)
except FileNotFoundError:
	print(_("Data files have not been downloaded, please run the following commands:"))
	print("\n\tcd " + paths_factory.dlib_data_dir_path())
	print("\tsudo ./install.sh\n")
	sys.exit(1)

if result == VerificationResult.SUCCESS:
	if end_report:
		print_report(stats)
	if save_successful:
		make_snapshot(_("SUCCESSFUL"), stats)
elif result in (VerificationResult.TIMEOUT_REACHED, VerificationResult.TOO_DARK):
	if save_failed:
		make_snapshot(_("FAILED"), stats)
	if result == VerificationResult.TOO_DARK:
		print(_("All frames were too dark, please check dark_threshold in config"))
elif result == VerificationResult.INVALID_DEVICE:
	if config.getboolean("video", "warn_no_device", fallback=True):
		print(stats.error)

sys.exit(int(result))
