# Recognition backend abstraction
# Based on PR #1088 by haleelrah (https://github.com/boltgolt/howdy/pull/1088)
# Abstracts the face detection / landmark / encoding pipeline behind one
# interface so compare.py, add.py and test.py stay backend agnostic.


class FaceRectangle:
	"""Face bounding box. Method names match dlib.rectangle, so callers can
	treat dlib rectangles and backend-agnostic rectangles identically."""

	def __init__(self, top: int, left: int, right: int, bottom: int):
		self._top = top
		self._left = left
		self._right = right
		self._bottom = bottom

	@classmethod
	def from_dlib(cls, rect):
		return cls(rect.top(), rect.left(), rect.right(), rect.bottom())

	def top(self) -> int:
		return self._top

	def left(self) -> int:
		return self._left

	def right(self) -> int:
		return self._right

	def bottom(self) -> int:
		return self._bottom


class LandmarkPoint:
	"""A single landmark point with x/y attributes."""

	def __init__(self, x: int, y: int):
		self.x = x
		self.y = y


class LandmarkSet:
	"""Landmark results, preserving the .part(index).x/.y interface used by
	nod.py and other dlib-era consumers."""

	def __init__(self, points, raw=None):
		self._points = points
		self._raw = raw  # backend-specific opaque object (e.g. dlib full_object_detection)

	def part(self, index: int) -> LandmarkPoint:
		return self._points[index]


class RecognitionBackend:
	"""Interface for face detection and encoding backends"""

	# Distance metric returned by match(), for UI/debug purposes
	distance = "abstract"

	def detect_faces(self, frame, upsample: int = 1):
		"""Detect faces in frame, returns a list of FaceRectangle"""
		raise NotImplementedError

	def get_landmarks(self, frame, rect):
		"""Landmarks for one detected face, returns a LandmarkSet"""
		raise NotImplementedError

	def compute_encoding(self, frame, rect, num_jitters: int = 1):
		"""Embedding for one detected face, returns a numpy array or None
		when the face could not be encoded"""
		raise NotImplementedError

	def match(self, encodings, encoding):
		"""Distances between enrolled encodings and one face encoding
		(lower is a better match, scale depends on the backend)"""
		raise NotImplementedError
