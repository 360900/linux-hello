# Settings page: edit config values through the CLI (comments preserved)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
	QCheckBox,
	QComboBox,
	QDoubleSpinBox,
	QFormLayout,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QMessageBox,
	QPushButton,
	QScrollArea,
	QSpinBox,
	QVBoxLayout,
	QWidget,
)

import cli_bridge
import pam_services

# (key, label, kind, extra) — kind: bool, invbool, int, float, str, choice
SETTINGS = [
	("disabled", "Face authentication enabled (unchecked = disabled)", "invbool"),
	("no_confirmation", "Authenticate without confirmation after success", "bool"),
	("suppress_unknown", "Do not show an error for unknown users", "bool"),
	("abort_if_ssh", "Disable in remote SSH sessions", "bool"),
	("abort_if_lid_closed", "Disable when the laptop lid is closed", "bool"),
	("detection_notice", "Show a notice when a face is detected", "bool"),
	("timeout_notice", "Show a notice when authentication times out", "bool"),
	("use_cnn", "Use the slower CNN model (more accurate, needs GPU)", "bool"),
	("certainty", "Certainty (1-10, lower is more strict)", "float", (1.0, 10.0, 1, 0.1)),
	("timeout", "Search timeout in seconds", "int", (1, 60)),
	("device_path", "Camera device path (none = auto-detect)", "str"),
	("max_height", "Max frame height (speed vs precision)", "int", (120, 2160)),
	("dark_threshold", "Dark frame threshold (auto = adaptive)", "str"),
	("recording_plugin", "Recorder backend", "choice", ("opencv", "ffmpeg", "pyv4l2")),
	("force_mjpeg", "Force MJPEG decoding", "bool"),
	("exposure", "Camera exposure (-1 = auto)", "int", (-1, 21)),
	("device_fps", "Camera frame rate (-1 = highest)", "int", (-1, 120)),
	("rotate", "Rotate frame (0, 1 = 90° CCW, 2 = 90° CW)", "choice", ("0", "1", "2")),
	("save_failed", "Save snapshot on failed auth", "bool"),
	("save_successful", "Save snapshot on successful login", "bool"),
]


class SettingsPage(QWidget):
	def __init__(self):
		super().__init__()
		self.rows = {}

		outer = QVBoxLayout(self)
		outer.setContentsMargins(40, 20, 40, 20)
		outer.addWidget(QLabel("<h2>Settings</h2>"))

		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		outer.addWidget(scroll, 1)

		body = QWidget()
		form = QFormLayout(body)
		form.setLabelAlignment(Qt.AlignLeft)
		scroll.setWidget(body)

		config = cli_bridge.read_config()

		def get(key, fallback):
			for section in config.sections():
				if config.has_option(section, key):
					return config.get(section, key)
			return fallback

		for spec in SETTINGS:
			key, label, kind = spec[0], spec[1], spec[2]
			extra = spec[3] if len(spec) > 3 else None

			if kind == "bool":
				widget = QCheckBox()
				widget.setChecked(get(key, "false").strip().lower() in ("true", "yes", "1", "on"))
				getter = lambda w=widget: str(w.isChecked()).lower()
			elif kind == "invbool":
				widget = QCheckBox()
				widget.setChecked(get(key, "false").strip().lower() not in ("true", "yes", "1", "on"))
				getter = lambda w=widget: str(not w.isChecked()).lower()
			elif kind == "int":
				widget = QSpinBox()
				widget.setRange(extra[0], extra[1])
				try:
					widget.setValue(int(float(get(key, extra[0]))))
				except ValueError:
					widget.setValue(extra[0])
				getter = lambda w=widget: str(w.value())
			elif kind == "float":
				widget = QDoubleSpinBox()
				widget.setRange(extra[0], extra[1])
				widget.setDecimals(extra[2])
				widget.setSingleStep(extra[3])
				try:
					widget.setValue(float(get(key, extra[0])))
				except ValueError:
					widget.setValue(extra[0])
				getter = lambda w=widget: str(w.value())
			elif kind == "choice":
				widget = QComboBox()
				widget.addItems([str(c) for c in extra])
				current = str(get(key, extra[0])).strip()
				if current in [str(c) for c in extra]:
					widget.setCurrentText(current)
				getter = lambda w=widget: w.currentText()
			else:  # str
				widget = QLineEdit(str(get(key, "none")))
				getter = lambda w=widget: (w.text().strip() or "none")

			save = QPushButton("Save")
			save.clicked.connect(lambda _=False, k=key: self.save_key(k))

			row = QWidget()
			row_layout = QHBoxLayout(row)
			row_layout.setContentsMargins(0, 0, 0, 0)
			row_layout.addWidget(widget, 1)
			row_layout.addWidget(save)
			self.rows[key] = (widget, getter)
			form.addRow("<b>{}</b><br/>{}".format(key, label), row)

		# --- KDE integration card ---
		self.kde_group = QGroupBox("KDE lock screen integration")
		kde_layout = QVBoxLayout(self.kde_group)
		kde_info = QLabel(
			"Enable face authentication for the Plasma lock screen and login flows.\n"
			"A backup of each PAM file is saved next to it (.linux-hello-backup)."
		)
		kde_info.setWordWrap(True)
		kde_layout.addWidget(kde_info)

		self.service_checks = {}
		for service in pam_services.existing_services():
			check = QCheckBox(service)
			check.setChecked(pam_services.is_enabled(service))
			check.toggled.connect(lambda on, s=service: self.toggle_pam_service(s, on))
			kde_layout.addWidget(check)
			self.service_checks[service] = check
		if not self.service_checks:
			kde_layout.addWidget(QLabel("No KDE or login PAM services found on this system."))
		outer.addWidget(self.kde_group)

	def toggle_pam_service(self, service, on):
		if on:
			ok, message = pam_services.enable(service)
		else:
			ok, message = pam_services.disable(service)
		if not ok:
			QMessageBox.warning(self, "KDE integration", message)
		else:
			check = self.service_checks.get(service)
			if check is not None:
				check.blockSignals(True)
				check.setChecked(pam_services.is_enabled(service))
				check.blockSignals(False)

	def save_key(self, key):
		widget, getter = self.rows[key]
		code, output = cli_bridge.set_value(key, getter())
		if code != 0:
			QMessageBox.warning(self, "Save failed", output.strip()[-500:] or "Could not save " + key)
