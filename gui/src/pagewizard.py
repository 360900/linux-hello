# First-run wizard: detect camera, enroll a face model, confirm
import os
import pwd

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
	QButtonGroup,
	QComboBox,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QProgressBar,
	QPushButton,
	QRadioButton,
	QStackedWidget,
	QVBoxLayout,
	QWidget,
)

import cli_bridge


class WizardPage(QWidget):
	def __init__(self, on_finished=None):
		super().__init__()
		self.on_finished = on_finished
		self.selected_camera = None
		self.camera_choices = QButtonGroup(self)

		root = QVBoxLayout(self)
		root.setContentsMargins(40, 40, 40, 40)
		root.setSpacing(16)

		self.steps = QStackedWidget()
		root.addWidget(self.steps, 1)

		# --- Step 0: intro ---
		intro = QWidget()
		intro_layout = QVBoxLayout(intro)
		intro_layout.setSpacing(12)
		intro_layout.addWidget(QLabel("<h2>Setup</h2>"))
		intro_text = QLabel(
			"This wizard will set up face authentication on this machine.\n\n"
			"1. Your camera is detected automatically\n"
			"2. Your face is enrolled as a model\n"
			"3. Face recognition is enabled for system logins\n\n"
			"Look straight at the camera during enrollment, in normal lighting."
		)
		intro_text.setWordWrap(True)
		intro_layout.addWidget(intro_text)
		start = QPushButton("Start")
		start.clicked.connect(lambda: (self.steps.setCurrentIndex(1), self.detect_camera()))
		intro_layout.addWidget(start)
		intro_layout.addStretch(1)
		self.steps.addWidget(intro)

		# --- Step 1: camera detection ---
		camera = QWidget()
		camera_layout = QVBoxLayout(camera)
		camera_layout.setSpacing(12)
		camera_layout.addWidget(QLabel("<h2>Camera</h2>"))
		self.camera_status = QLabel("Detecting cameras…")
		self.camera_status.setWordWrap(True)
		camera_layout.addWidget(self.camera_status)
		camera_layout.addWidget(QLabel("Choose the camera to use:"))
		self.camera_box = QVBoxLayout()
		camera_layout.addLayout(self.camera_box)
		camera_layout.addStretch(1)

		camera_buttons = QHBoxLayout()
		redetect = QPushButton("Detect again")
		redetect.clicked.connect(self.detect_camera)
		camera_buttons.addWidget(redetect)
		self.camera_next = QPushButton("Continue")
		self.camera_next.setEnabled(False)
		self.camera_next.clicked.connect(self.camera_chosen)
		camera_buttons.addWidget(self.camera_next)
		camera_layout.addLayout(camera_buttons)
		self.steps.addWidget(camera)

		# --- Step 2: enrollment ---
		enroll = QWidget()
		enroll_layout = QVBoxLayout(enroll)
		enroll_layout.setSpacing(12)
		enroll_layout.addWidget(QLabel("<h2>Enroll your face</h2>"))

		user_row = QHBoxLayout()
		user_row.addWidget(QLabel("User:"))
		self.user_combo = QComboBox()
		user_row.addWidget(self.user_combo, 1)
		enroll_layout.addLayout(user_row)

		name_row = QHBoxLayout()
		name_row.addWidget(QLabel("Model name:"))
		self.name_edit = QLineEdit("default")
		name_row.addWidget(self.name_edit, 1)
		enroll_layout.addLayout(name_row)

		self.enroll_progress = QProgressBar()
		self.enroll_progress.setRange(0, 0)  # busy indicator
		self.enroll_progress.hide()
		enroll_layout.addWidget(self.enroll_progress)

		self.enroll_status = QLabel("")
		self.enroll_status.setWordWrap(True)
		enroll_layout.addWidget(self.enroll_status)

		self.enroll_button = QPushButton("Start enrollment")
		self.enroll_button.clicked.connect(self.enroll)
		enroll_layout.addWidget(self.enroll_button)
		enroll_layout.addStretch(1)
		self.steps.addWidget(enroll)

		# --- Step 3: done ---
		done = QWidget()
		done_layout = QVBoxLayout(done)
		done_layout.addWidget(QLabel("<h2>All set!</h2>"))
		done_text = QLabel(
			"Your face model has been enrolled.\n"
			"Linux Hello will now be offered on login and unlock screens.\n\n"
			"You can fine-tune everything in the Settings and Camera test pages."
		)
		done_text.setWordWrap(True)
		done_layout.addWidget(done_text)
		finish = QPushButton("Finish")
		finish.clicked.connect(self.finish)
		done_layout.addWidget(finish)
		done_layout.addStretch(1)
		self.steps.addWidget(done)

		self.steps.setCurrentIndex(0)

	def populate_users(self):
		self.user_combo.clear()
		for name in sorted(os.listdir("/home")) if os.path.isdir("/home") else []:
			self.user_combo.addItem(name)
		sudo_user = os.environ.get("SUDO_USER")
		if sudo_user and self.user_combo.findText(sudo_user) < 0:
			self.user_combo.insertItem(0, sudo_user)
		if self.user_combo.count() == 0:
			self.user_combo.addItem("root")

	def detect_camera(self):
		self.camera_next.setEnabled(False)
		self.camera_status.setText("Probing video devices, this can take a few seconds…")

		# Clear old radio buttons
		for button in list(self.camera_choices.buttons()):
			self.camera_choices.removeButton(button)
			self.camera_box.removeWidget(button)
			button.deleteLater()

		def probe():
			results = cli_bridge.discover_cameras() or []
			if not results:
				self.camera_status.setText(
					"No usable camera found.\n"
					"Connect a camera and click 'Detect again'."
				)
				return

			self.camera_status.setText(
				"Found {} usable device{}.".format(len(results), "s" if len(results) != 1 else "")
			)
			for index, result in enumerate(results):
				text = "{}: {}{} frames, {}% gray".format(
					result["node"],
					"IR (grayscale), " if result.get("saturation", 255) <= 16 else "",
					result.get("frames", 0),
					int(result.get("brightness", 0) / 255 * 100),
				)
				button = QRadioButton(text)
				if index == 0:
					button.setChecked(True)
				self.camera_choices.addButton(button)
				self.camera_box.addWidget(button)
			self.camera_next.setEnabled(True)

		QTimer.singleShot(50, probe)

	def camera_chosen(self):
		for button in self.camera_choices.buttons():
			if button.isChecked():
				self.selected_camera = button.text().split(": ")[0]
				break

		if self.selected_camera:
			cli_bridge.set_value("device_path", self.selected_camera)

		self.populate_users()
		self.steps.setCurrentIndex(2)

	def enroll(self):
		user = self.user_combo.currentText()
		name = self.name_edit.text().strip() or "default"
		self.enroll_button.setEnabled(False)
		self.enroll_progress.show()
		self.enroll_status.setText("Enrolling {} for user {}…".format(name, user))

		def run():
			code, output = cli_bridge.add_model(user, name)
			self.enroll_progress.hide()
			self.enroll_button.setEnabled(True)
			if code == 0:
				self.enroll_status.setText("Enrollment complete.")
				QTimer.singleShot(400, lambda: self.steps.setCurrentIndex(3))
			else:
				self.enroll_status.setText("Enrollment failed:\n" + output.strip()[-500:])

		QTimer.singleShot(50, run)

	def finish(self):
		if self.on_finished is not None:
			self.on_finished()
