# Linux Hello — main entry point.
# Modes:
#   linux-hello               open the settings app
#   linux-hello --start-auth-ui  show the authentication window (used by the PAM module)
import os
import sys

sys.dont_write_bytecode = False

# Make our own directory importable and add the core lib for shared code
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_app():
	from PySide6.QtWidgets import QApplication
	from PySide6.QtGui import QGuiApplication, QIcon

	QGuiApplication.setApplicationName("Linux Hello")
	QGuiApplication.setDesktopFileName("linux-hello")

	app = QApplication(sys.argv[:1])

	# Prefer the KDE Breeze style, fall back to Fusion
	for style in ("Breeze", "Fusion"):
		if style.lower() in [s.lower() for s in QStyle_keys(app)]:
			app.setStyle(style)
			break

	import paths
	if os.path.exists(str(paths.logo_path)):
		app.setWindowIcon(QIcon(str(paths.logo_path)))

	import mainwindow
	window = mainwindow.MainWindow()
	window.show()
	sys.exit(app.exec())


def QStyle_keys(app):
	try:
		return list(app.factory.keys("QStyle")) if hasattr(app.factory, "keys") else []
	except Exception:
		return []


def run_auth_ui():
	import authui
	authui.main()


if __name__ == "__main__":
	if "--start-auth-ui" in sys.argv:
		run_auth_ui()
	else:
		run_app()
