# dlib backend: HOG/CNN detection, 5-point landmarks, ResNet embeddings
# Based on PR #1088 by haleelrah (https://github.com/boltgolt/howdy/pull/1088)

import os

import dlib
import numpy as np

import paths_factory
from recog.backend import FaceRectangle, LandmarkPoint, LandmarkSet, RecognitionBackend


class DlibBackend(RecognitionBackend):
	distance = "euclidean"

	def __init__(self, use_cnn: bool = False):
		if not os.path.isfile(paths_factory.shape_predictor_5_face_landmarks_path()):
			raise FileNotFoundError("dlib data files not found")

		self.use_cnn = use_cnn
		if use_cnn:
			self.detector = dlib.cnn_face_detection_model_v1(
				paths_factory.mmod_human_face_detector_path())
		else:
			self.detector = dlib.get_frontal_face_detector()

		self.pose_predictor = dlib.shape_predictor(
			paths_factory.shape_predictor_5_face_landmarks_path())
		self.encoder = dlib.face_recognition_model_v1(
			paths_factory.dlib_face_recognition_resnet_model_v1_path())

	def detect_faces(self, frame, upsample: int = 1):
		raw = self.detector(frame, upsample)
		# The CNN detector returns mmod_rectangles which wrap the actual
		# rectangle in .rect
		return [FaceRectangle.from_dlib(det.rect if self.use_cnn else det) for det in raw]

	def get_landmarks(self, frame, rect):
		dlib_rect = dlib.rectangle(rect.left(), rect.top(), rect.right(), rect.bottom())
		raw_landmarks = self.pose_predictor(frame, dlib_rect)
		points = [LandmarkPoint(x=raw_landmarks.part(i).x, y=raw_landmarks.part(i).y)
			for i in range(raw_landmarks.num_parts)]
		return LandmarkSet(points, raw=raw_landmarks)

	def compute_encoding(self, frame, rect, num_jitters: int = 1):
		landmarks = self.get_landmarks(frame, rect)
		return np.array(self.encoder.compute_face_descriptor(frame, landmarks._raw, num_jitters))

	def match(self, encodings, encoding):
		# Euclidean distance over 128-D ResNet descriptors
		return np.linalg.norm(np.asarray(encodings) - encoding, axis=1)
