# Kept free of heavy imports so callers can catch camera errors cheaply


class CameraError(Exception):
	"""The camera could not be found, opened or read"""
