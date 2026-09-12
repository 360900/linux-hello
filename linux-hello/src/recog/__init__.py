# Recognition backend factory
# Based on PR #1088 by haleelrah (https://github.com/boltgolt/howdy/pull/1088)
# Centralizes the backend choice shared by compare.py, cli/add.py and
# cli/test.py: prefer the OpenVINO GPU pipeline when models are present and
# compatible with the enrolled encodings, otherwise fall back to dlib.

import sys

from recog.backend import FaceRectangle, LandmarkPoint, LandmarkSet, RecognitionBackend

__all__ = [
	"FaceRectangle", "LandmarkPoint", "LandmarkSet", "RecognitionBackend",
	"DimensionMismatchError", "create_backend",
]


class DimensionMismatchError(Exception):
	"""Raised internally when OpenVINO embeddings are incompatible with the
	enrolled models, which triggers the dlib fallback"""


def create_backend(use_cnn=False, enrolled_dims=None, mismatch_hint=""):
	"""Instantiate the best available recognition backend

	enrolled_dims is the set of embedding dimensions found in the user's
	enrolled models (or None/empty when enrolling fresh models). When the
	OpenVINO encoder outputs a different size, dlib is used instead and a
	message with mismatch_hint is printed so the user knows how to re-enroll.
	Raises FileNotFoundError when the dlib data files have not been downloaded.
	"""
	try:
		import openvino_face
		if openvino_face.is_available():
			try:
				from recog.openvino_backend import OpenVinoBackend
				backend = OpenVinoBackend()
			except Exception as err:
				print("OpenVINO not available ({}), using dlib".format(err), file=sys.stderr)
			else:
				dims = {backend.embedding_dim}
				if enrolled_dims and dims != enrolled_dims:
					print("Enrolled models use {}-D encodings but the OpenVINO encoder outputs {}-D; using dlib. {}".format(
						"/".join(str(d) for d in sorted(enrolled_dims)),
						backend.embedding_dim,
						mismatch_hint), file=sys.stderr)
				else:
					print("Using OpenVINO GPU for face detection and encoding")
					return backend
	except ImportError:
		pass

	from recog.dlib_backend import DlibBackend
	return DlibBackend(use_cnn=use_cnn)
