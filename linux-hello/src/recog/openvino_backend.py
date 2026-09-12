# OpenVINO backend: Intel iGPU/iGPU-adjacent inference using the
# face-detection-adas-0001 and face-reidentification-retail-0095 models.
# Wraps the detectors in openvino_face.py behind the recog backend interface.

import numpy as np

from recog.backend import FaceRectangle, LandmarkPoint, LandmarkSet, RecognitionBackend


class OpenVinoBackend(RecognitionBackend):
	distance = "cosine"

	def __init__(self, device: str = "GPU"):
		import openvino_face
		self.detector = openvino_face.FaceDetector(device)
		self.encoder = openvino_face.FaceEncoder(device)
		self._pose_predictor = None

	@property
	def embedding_dim(self):
		"""Size of the embedding vector, e.g. 256 for face-reidentification-retail-0095"""
		return self.encoder.embedding_dim

	@property
	def pose_predictor(self):
		# dlib landmarks, provided for consumers like the nod rubberstamp that
		# need .part(i) points. Loaded lazily so OpenVINO-only setups without
		# the dlib data files still boot (only rubberstamps need landmarks).
		if self._pose_predictor is None:
			import paths_factory
			import dlib
			self._pose_predictor = dlib.shape_predictor(
				paths_factory.shape_predictor_5_face_landmarks_path())
		return self._pose_predictor

	def detect_faces(self, frame, upsample: int = 1):
		# openvino_face.FaceDetector returns dlib rectangles, which already
		# satisfy the FaceRectangle interface
		return [FaceRectangle.from_dlib(rect) for rect in self.detector(frame, upsample)]

	def get_landmarks(self, frame, rect):
		dlib_rect = self._to_dlib_rect(rect)
		raw_landmarks = self.pose_predictor(frame, dlib_rect)
		points = [LandmarkPoint(x=raw_landmarks.part(i).x, y=raw_landmarks.part(i).y)
			for i in range(raw_landmarks.num_parts)]
		return LandmarkSet(points, raw=raw_landmarks)

	def compute_encoding(self, frame, rect, num_jitters: int = 1):
		# num_jitters is accepted for interface compatibility, the retail
		# encoder has no jitter concept
		return self.encoder.encode(frame, rect)

	def match(self, encodings, encoding):
		# Cosine distance on L2-normalized embeddings, not the Euclidean
		# distance dlib uses: the scale differs, so the "certainty" config
		# value may need recalibrating for OpenVINO
		return 1.0 - np.dot(np.asarray(encodings), encoding)

	@staticmethod
	def _to_dlib_rect(rect):
		import dlib
		return dlib.rectangle(rect.left(), rect.top(), rect.right(), rect.bottom())
