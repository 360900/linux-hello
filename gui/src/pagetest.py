# Camera test page: live feed with face overlay and frame diagnostics
import cv2
import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
	QHBoxLayout,
	QLabel,
	QPushButton,
	QVBoxLayout,
	QWidget,
)

import cli_bridge


def cli_device_path():
	"""Resolve the configured device path, auto-detecting when needed"""
	device = cli_bridge.get_value("video", "device_path", "none")
	if device in (None, "none", "", "auto"):
		device = cli_bridge.detect_camera()
	return device


class TestPage(QWidget):
	def __init__(self):
		super().__init__()
		self.capture = None
		self.timer = None
		self.cascade = None
		try:
			self.cascade = cv2.CascadeClassifier(
				cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
			)
			if self.cascade.empty():
				self.cascade = None
		except Exception:
			self.cascade = None

		root = QVBoxLayout(self)
		root.setContentsMargins(40, 20, 40, 20)
		root.setSpacing(12)

		root.addWidget(QLabel("<h2>Camera test</h2>"))

		self.preview = QLabel("Camera preview stopped")
		self.preview.setAlignment(Qt.AlignCenter)
		self.preview.setMinimumSize(640, 360)
		self.preview.setStyleSheet("background: black; color: white;")
		root.addWidget(self.preview, 1)

		buttons = QHBoxLayout()
		self.toggle_button = QPushButton("Start preview")
		self.toggle_button.clicked.connect(self.toggle)
		buttons.addWidget(self.toggle_button)
		buttons.addStretch(1)
		root.addLayout(buttons)

		self.info = QLabel("")
		root.addWidget(self.info)

	def start(self):
		if self.capture is not None:
			return
		try:
			self.capture = cv2.VideoCapture(cli_device_path(), cv2.CAP_V4L2)
			if not self.capture.isOpened():
				self.capture = None
				self.info.setText("Could not open the camera, check the device_path setting")
				return
		except Exception as error:
			self.capture = None
			self.info.setText("Could not open the camera: " + str(error))
			return
		self.timer = QTimer(self)
		self.timer.timeout.connect(self.tick)
		self.timer.start(33)
		self.toggle_button.setText("Stop preview")

	def stop(self):
		if self.timer is not None:
			self.timer.stop()
			self.timer = None
		if self.capture is not None:
			self.capture.release()
			self.capture = None
		self.preview.setText("Camera preview stopped")
		self.preview.setPixmap(QPixmap())
		self.toggle_button.setText("Start preview")

	def toggle(self):
		if self.capture is None:
			self.start()
		else:
			self.stop()

	def tick(self):
		if self.capture is None:
			return
		ok, frame = self.capture.read()
		if not ok or frame is None:
			return

		gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

		if self.cascade is not None:
			faces = self.cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))
			for (x, y, w, h) in faces:
				cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 200, 0), 2)

		hist = cv2.calcHist([gray], [0], None, [8], [0, 256]).ravel()
		darkness = float(hist[0]) / max(1.0, float(hist.sum())) * 100.0

		self.info.setText(
			"Resolution: {}x{}    Dark pixels: {:.0f}% (adaptive mode rejects unlit frames)".format(
				frame.shape[1], frame.shape[0], darkness
			)
		)

		height, width, channels = frame.shape
		image = QImage(frame.data, width, height, channels * width, QImage.Format_BGR888)
		self.preview.setPixmap(QPixmap.fromImage(image).scaled(
			self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
		))
