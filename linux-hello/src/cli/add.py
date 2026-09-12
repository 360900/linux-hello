import time
import os
import sys
import json
import configparser
import builtins
import numpy as np
import paths_factory

from recorders.video_capture import VideoCapture
from i18n import _

try:
	import dlib
except ImportError as err:
	print(err)
	print(_("\nCan't import the dlib module, check the output of"))
	print("pip3 show dlib")
	sys.exit(1)

import cv2

if not os.path.isfile(paths_factory.shape_predictor_5_face_landmarks_path()):
	print(_("Data files have not been downloaded, please run the following commands:"))
	print("\n\tcd " + paths_factory.dlib_data_dir_path())
	print("\tsudo ./install.sh\n")
	sys.exit(1)

config = configparser.ConfigParser()
config.read(paths_factory.config_file_path())

use_cnn = config.getboolean("core", "use_cnn", fallback=False)
use_openvino = False

user = builtins.linux_hello_user
enc_file = paths_factory.user_model_path(user)
encodings = []

if not os.path.exists(paths_factory.user_models_dir_path()):
	print(_("No face model folder found, creating one"))
	os.makedirs(paths_factory.user_models_dir_path(), mode=0o700)

# Load existing models before picking a backend so we can check
# embedding compatibility with OpenVINO below
try:
	encodings = json.load(open(enc_file))
except FileNotFoundError:
	encodings = []

try:
	import openvino_face
	if openvino_face.is_available():
		face_detector = openvino_face.FaceDetector("GPU")
		face_encoder_ov = openvino_face.FaceEncoder("GPU")
		# OpenVINO embeddings (256-D) cannot be mixed with models enrolled
		# using dlib (128-D); fall back to dlib so old models keep working
		model_dims = {len(e) for model in encodings for e in model["data"]}
		if model_dims and model_dims != {face_encoder_ov.embedding_dim}:
			print("Enrolled models use {}-D encodings but the OpenVINO encoder outputs {}-D; using dlib. Delete the old models with 'sudo linux-hello-cli clear' first to switch to OpenVINO.".format(
				"/".join(str(d) for d in sorted(model_dims)), face_encoder_ov.embedding_dim))
		else:
			use_openvino = True
			print("Using OpenVINO GPU for face detection and encoding")
except Exception as e:
	print(f"OpenVINO not available ({e}), using dlib")

if not use_openvino:
	if use_cnn:
		face_detector = dlib.cnn_face_detection_model_v1(paths_factory.mmod_human_face_detector_path())
	else:
		face_detector = dlib.get_frontal_face_detector()

pose_predictor = dlib.shape_predictor(paths_factory.shape_predictor_5_face_landmarks_path())
if not use_openvino:
	face_encoder_dlib = dlib.face_recognition_model_v1(paths_factory.dlib_face_recognition_resnet_model_v1_path())

if len(encodings) > 3:
	print(_("NOTICE: Each additional model slows down the face recognition engine slightly"))
	print(_("Press Ctrl+C to cancel\n"))

if not builtins.linux_hello_args.plain:
	print(_("Adding face model for the user ") + user)

label = "Initial model"
next_id = encodings[-1]["id"] + 1 if encodings else 0

if builtins.linux_hello_args.arguments:
	label = builtins.linux_hello_args.arguments[0]
else:
	label = _("Model #") + str(next_id)

if builtins.linux_hello_args.y:
	print(_('Using default label "%s" because of -y flag') % (label, ))
else:
	label_in = input(_("Enter a label for this new model [{}]: ").format(label))
	if label_in != "":
		label = label_in[:24]

if "," in label:
	print(_("NOTICE: Removing illegal character \",\" from model name"))
	label = label.replace(",", "")

insert_model = {
	"time": int(time.time()),
	"label": label,
	"id": next_id,
	"data": []
}

video_capture = VideoCapture(config)

print(_("\nPlease look straight into the camera"))
time.sleep(2)

enc = []
frames = 0
valid_frames = 0
dark_tries = 0
dark_running_total = 0
face_locations = None

dark_threshold = config.getfloat("video", "dark_threshold", fallback=60)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

while frames < 60:
	frames += 1
	frame, gsframe = video_capture.read_frame()
	gsframe = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
	gsframe = clahe.apply(gsframe)

	hist = cv2.calcHist([gsframe], [0], None, [8], [0, 256])
	hist_total = np.sum(hist)
	darkness = (hist[0] / hist_total * 100)

	if (hist_total == 0) or (darkness == 100):
		continue

	dark_running_total += darkness
	valid_frames += 1

	if (darkness > dark_threshold):
		dark_tries += 1
		continue

	face_locations = face_detector(gsframe, 1)

	if face_locations:
		break

video_capture.release()

if not face_locations:
	if valid_frames == 0:
		print(_("Camera saw only black frames - is IR emitter working?"))
	elif valid_frames == dark_tries:
		print(_("All frames were too dark, please check dark_threshold in config"))
		print(_("Average darkness: {avg}, Threshold: {threshold}").format(avg=str(dark_running_total / valid_frames), threshold=str(dark_threshold)))
	else:
		print(_("No face detected, aborting"))
	sys.exit(1)

elif len(face_locations) > 1:
	print(_("Multiple faces detected, aborting"))
	sys.exit(1)

face_location = face_locations[0]
if use_cnn and not use_openvino:
	face_location = face_location.rect

if use_openvino:
	face_encoding = face_encoder_ov.encode(frame, face_location)
	if face_encoding is None:
		print("Failed to encode face, aborting")
		sys.exit(1)
else:
	face_landmark = pose_predictor(frame, face_location)
	face_encoding = np.array(face_encoder_dlib.compute_face_descriptor(frame, face_landmark, 1))

insert_model["data"].append(face_encoding.tolist())

encodings.append(insert_model)

with open(enc_file, "w") as datafile:
	json.dump(encodings, datafile)

# Restrict permissions so only root can read the biometric model
os.chmod(enc_file, 0o600)

# Give let the user know how it went
print(_("""\nScan complete
Added a new model to """) + user)
