import configparser
import builtins
import os
import json
import sys
import time
import cv2
import numpy as np
import paths_factory

from i18n import _
from recorders.video_capture import VideoCapture
from recog import create_backend

config = configparser.ConfigParser()
config.read(paths_factory.config_file_path())

if config.get("video", "recording_plugin", fallback="opencv") != "opencv":
	print(_("Linux Hello has been configured to use a recorder which doesn't support the test command yet, aborting"))
	sys.exit(12)

video_capture = VideoCapture(config)

video_certainty = config.getfloat("video", "certainty", fallback=3.5) / 10
exposure = config.getint("video", "exposure", fallback=-1)
# Same adaptive "auto" mode as compare.py: learn the scene baseline
dark_threshold_raw = str(config.get("video", "dark_threshold", fallback="60")).strip().lower()
dark_threshold_auto = dark_threshold_raw in ("auto", "none")
if not dark_threshold_auto:
	dark_threshold = float(dark_threshold_raw)
dark_history = []

print(_("""
Opening a window with a test feed

Press ctrl+C in this terminal to quit
Click on the image to enable or disable slow mode
"""))


def mouse(event, x, y, flags, param):
	global slow_mode
	if event == cv2.EVENT_LBUTTONDOWN:
		slow_mode = not slow_mode


def print_text(line_number, text):
	cv2.putText(overlay, text, (10, height - 10 - (10 * line_number)), cv2.FONT_HERSHEY_SIMPLEX, .3, (0, 255, 0), 0, cv2.LINE_AA)


use_cnn = config.getboolean('core', 'use_cnn', fallback=False)

encodings = []
models = None

try:
	user = builtins.linux_hello_user
	models = json.load(open(paths_factory.user_model_path(user)))
	for model in models:
		encodings += model["data"]
except FileNotFoundError:
	pass

try:
	backend = create_backend(
		use_cnn=use_cnn,
		enrolled_dims={len(e) for e in encodings},
		mismatch_hint="Delete the old models with 'sudo linux-hello-cli clear' first to switch to OpenVINO.")
except FileNotFoundError:
	print(_("Data files have not been downloaded, please run the following commands:"))
	print("\n\tcd " + paths_factory.dlib_data_dir_path())
	print("\tsudo ./install.sh\n")
	sys.exit(1)

clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

cv2.namedWindow("Linux Hello Test")
cv2.setMouseCallback("Linux Hello Test", mouse)

slow_mode = False
total_frames = 0
sec_frames = 0
fps = 0
sec = int(time.time())
rec_tm = 0

try:
	while True:
		frame_tm = time.time()
		total_frames += 1
		sec_frames += 1

		if sec != int(frame_tm):
			fps = sec_frames
			sec = int(frame_tm)
			sec_frames = 0

		orig_frame, frame = video_capture.read_frame()
		frame = clahe.apply(frame)
		overlay = frame.copy()
		overlay = cv2.cvtColor(overlay, cv2.COLOR_GRAY2BGR)
		height, width = frame.shape[:2]

		# Create a histogram of the image with 8 values
		# ravel because the returned shape differs across OpenCV versions:
		# 4.x returns (8, 1), 5.x returns (8,)
		hist = cv2.calcHist([frame], [0], None, [8], [0, 256]).ravel()
		# All values combined for percentage calculation
		hist_total = int(hist.sum())
		# Fill with the overall containing percentage
		hist_perc = []

		for index, value in enumerate(hist):
			value_perc = float(value) / hist_total * 100
			hist_perc.append(value_perc)
			p1 = (20 + (10 * index), 10)
			p2 = (10 + (10 * index), int(value_perc / 2 + 10))
			cv2.rectangle(overlay, p1, p2, (0, 200, 0), thickness=cv2.FILLED)

		print_text(0, _("RESOLUTION: %dx%d") % (height, width))
		print_text(1, _("FPS: %d") % (fps, ))
		print_text(2, _("FRAMES: %d") % (total_frames, ))
		print_text(3, _("RECOGNITION: %dms") % (round(rec_tm * 1000), ))

		if slow_mode:
			cv2.putText(overlay, _("SLOW MODE"), (width - 66, height - 10), cv2.FONT_HERSHEY_SIMPLEX, .3, (0, 0, 255), 0, cv2.LINE_AA)

		darkness = hist_perc[0]
		if dark_threshold_auto:
			if len(dark_history) >= 5:
				baseline = sorted(dark_history)[len(dark_history) // 2]
				too_dark = darkness > min(95.0, baseline * 1.25 + 10)
			else:
				too_dark = False
			dark_history.append(darkness)
			if len(dark_history) > 30:
				dark_history.pop(0)
		else:
			too_dark = darkness > dark_threshold

		if too_dark:
			cv2.putText(overlay, _("DARK FRAME"), (width - 68, 16), cv2.FONT_HERSHEY_SIMPLEX, .3, (0, 0, 255), 0, cv2.LINE_AA)
		else:
			cv2.putText(overlay, _("SCAN FRAME"), (width - 68, 16), cv2.FONT_HERSHEY_SIMPLEX, .3, (0, 255, 0), 0, cv2.LINE_AA)

			rec_tm = time.time()
			face_locations = backend.detect_faces(frame, 1)
			rec_tm = time.time() - rec_tm

			for loc in face_locations:
				color = (0, 0, 230)

				x = int((loc.right() - loc.left()) / 2) + loc.left()
				y = int((loc.bottom() - loc.top()) / 2) + loc.top()
				r = (loc.right() - loc.left()) / 2
				r = int(r + (r * 0.2))

				if models:
					face_encoding = backend.compute_encoding(orig_frame, loc)
					if face_encoding is None:
						continue
					matches = backend.match(encodings, face_encoding)

					match_index = np.argmin(matches)
					match = matches[match_index]

					if 0 < match < video_certainty:
						color = (0, 230, 0)
						circle_text = "{} (certainty: {})".format(models[match_index]["label"], round(match * 10, 3))
						cv2.putText(overlay, circle_text, (int(x + r / 3), y - r), cv2.FONT_HERSHEY_SIMPLEX, .3, (0, 255, 0), 0, cv2.LINE_AA)
					else:
						cv2.putText(overlay, "no match ({:.3f})".format(match * 10), (int(x + r / 3), y - r), cv2.FONT_HERSHEY_SIMPLEX, .3, (0, 0, 255), 0, cv2.LINE_AA)

				cv2.circle(overlay, (x, y), r, color, 2)

		alpha = 0.65
		frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
		cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
		cv2.imshow("Linux Hello Test", frame)

		if cv2.waitKey(1) != -1:
			raise KeyboardInterrupt()

		frame_time = time.time() - frame_tm
		if slow_mode:
			time.sleep(max([.5 - frame_time, 0.0]))

		if exposure != -1:
			video_capture.internal.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
			video_capture.internal.set(cv2.CAP_PROP_EXPOSURE, float(exposure))

except KeyboardInterrupt:
	print(_("\nClosing window"))
	cv2.destroyAllWindows()
