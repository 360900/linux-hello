# Welcome page: shows setup status and quick actions
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
	QFrame,
	QHBoxLayout,
	QLabel,
	QPushButton,
	QVBoxLayout,
)

import cli_bridge
import paths


class WelcomePage(QWidget):
	def __init__(self, on_change=None):
		super().__init__()
		self.on_change = on_change

		root = QVBoxLayout(self)
		root.setContentsMargins(40, 40, 40, 40)
		root.setSpacing(16)
		root.setAlignment(Qt.AlignTop)

		if os.path.exists(str(paths.logo_path)):
			logo = QLabel()
			logo.setPixmap(QPixmap(str(paths.logo_path)).scaledToHeight(96, Qt.SmoothTransformation))
			logo.setAlignment(Qt.AlignHCenter)
			root.addWidget(logo)

		title = QLabel("<h1>Linux Hello</h1>")
		title.setAlignment(Qt.AlignHCenter)
		root.addWidget(title)

		subtitle = QLabel("Face authentication for Linux, in the style of Windows Hello")
		subtitle.setAlignment(Qt.AlignHCenter)
		root.addWidget(subtitle)

		self.status_box = QFrame()
		self.status_box.setFrameShape(QFrame.StyledPanel)
		status_layout = QVBoxLayout(self.status_box)
		self.status_label = QLabel("Checking status…")
		self.status_label.setWordWrap(True)
		status_layout.addWidget(self.status_label)
		root.addWidget(self.status_box)

		buttons = QHBoxLayout()
		self.setup_button = QPushButton("Run setup")
		self.setup_button.clicked.connect(self.start_setup)
		buttons.addWidget(self.setup_button)
		root.addLayout(buttons)

		root.addStretch(1)
		self.reload()

	def start_setup(self):
		window = self.window()
		if hasattr(window, "sidebar"):
			window.sidebar.setCurrentRow(1)

	def reload(self):
		lines = []
		config = cli_bridge.read_config()

		device = config.get("video", "device_path", fallback="none")
		if device in ("none", "", "auto"):
			device = "auto-detect on first use"
		lines.append("Camera: " + device)

		users = cli_bridge.list_users_with_models()
		if users:
			counts = ", ".join("{} ({} model{})".format(u, len(m), "s" if len(m) != 1 else "") for u, m in users.items())
			lines.append("Enrolled models: " + counts)
			if config.getboolean("core", "disabled", fallback=False):
				lines.append("Face authentication is currently DISABLED")
			else:
				lines.append("Face authentication is enabled for supported logins")
		else:
			lines.append("No face models enrolled yet. Run the setup wizard")

		self.status_label.setText("\n".join(lines))
