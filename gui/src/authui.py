# Frameless auth window shown by compare.py during face authentication.
# Replaces the GTK authsticky window. compare.py spawns:
#   linux-hello --start-auth-ui
# and sends commands over stdin as "TYPE=message \n":
#   M = main status message, S = subtext
import os
import sys

from PySide6.QtCore import Qt, QSocketNotifier, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import paths


class AuthWindow(QWidget):
	def __init__(self):
		super().__init__()
		self.message = "Loading…"
		self.subtext = ""

		self.setWindowFlags(
			Qt.FramelessWindowHint
			| Qt.WindowStaysOnTopHint
			| Qt.X11BypassWindowManagerHint
			| Qt.Tool
		)
		self.setAttribute(Qt.WA_TranslucentBackground, True)

		self.setStyleSheet(
			"QWidget { background: rgba(30, 30, 30, 235); border-radius: 12px; }"
			"QLabel { color: white; background: transparent; }"
		)
		self.setFixedSize(420, 110)

		layout = QVBoxLayout(self)
		layout.setContentsMargins(18, 12, 18, 12)

		if os.path.exists(str(paths.logo_path)):
			logo = QLabel()
			logo.setPixmap(QPixmap(str(paths.logo_path)).scaledToHeight(40, Qt.SmoothTransformation))
			layout.addWidget(logo, 0, Qt.AlignLeft)

		self.main_label = QLabel(self.message)
		self.main_label.setStyleSheet("font-size: 15px; font-weight: bold;")
		layout.addWidget(self.main_label)

		self.sub_label = QLabel("")
		self.sub_label.setStyleSheet("font-size: 12px;")
		layout.addWidget(self.sub_label)

		# Listen for compare.py status updates on stdin
		self.notifier = QSocketNotifier(os.fileno(sys.stdin), QSocketNotifier.Read, self)
		self.notifier.activated.connect(self.read_stdin)

		self.position()
		self.show()

	def position(self):
		# Top-center of the primary screen, below any panel
		screen = QApplication_primaryScreen()
		if screen is None:
			return
		geometry = screen.availableGeometry()
		x = geometry.x() + (geometry.width() - self.width()) // 2
		self.move(x, geometry.y() + 48)

	def read_stdin_commands(self):
		# Non-blocking read of available stdin data
		import fcntl
		import select
		data = b""
		while True:
			ready, _, _ = select.select([sys.stdin], [], [], 0)
			if not ready:
				break
			chunk = os.read(sys.stdin.fileno(), 4096)
			if not chunk:
				# compare.py closed the pipe: authentication finished
				QApplication.quit()
				return
			data += chunk
		return data

	def read_stdin(self):
		data = self.read_stdin_commands()
		if not data:
			return
		for line in data.decode("utf-8", "replace").splitlines():
			line = line.strip()
			if not line or "=" not in line:
				continue
			type_char, _, message = line.partition("=")
			message = message.rstrip()
			if type_char == "M":
				self.message = message
			elif type_char == "S":
				self.subtext = message
		self.main_label.setText(self.message)
		self.sub_label.setText(self.subtext)
		self.adjustSize()
		self.position()


def QApplication_primaryScreen():
	from PySide6.QtGui import QGuiApplication
	return QGuiApplication.primaryScreen()


def main():
	app = QApplication.instance() or QApplication(sys.argv[:1])
	window = AuthWindow()
	app.aboutToQuit.connect(lambda: None)
	sys.exit(app.exec())
