# Linux Hello: main entry point.
# Modes:
#   linux-hello               open the settings app
#   linux-hello --start-auth-ui  show the authentication window (used by the PAM module)
import os
import sys

sys.dont_write_bytecode = False

# Make our own directory importable and add the core lib for shared code
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def restore_session_env():
	# pkexec hands us a scrubbed environment: no DISPLAY, no WAYLAND_DISPLAY,
	# no XDG_RUNTIME_DIR, only PKEXEC_UID. Rebuild enough of the session for
	# Qt to reach the compositor, and prefer Wayland over the X11 fallback.
	runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
	if not runtime_dir and os.environ.get("PKEXEC_UID"):
		runtime_dir = "/run/user/" + os.environ["PKEXEC_UID"]
		os.environ["XDG_RUNTIME_DIR"] = runtime_dir
	if not runtime_dir:
		return

	if not os.environ.get("WAYLAND_DISPLAY"):
		try:
			sockets = sorted(
				name
				for name in os.listdir(runtime_dir)
				if name.startswith("wayland-") and not name.endswith(".lock")
			)
		except OSError:
			sockets = []
		if sockets:
			os.environ["WAYLAND_DISPLAY"] = sockets[0]

	if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
		os.environ["QT_QPA_PLATFORM"] = "wayland"


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
	restore_session_env()

	if "--start-auth-ui" in sys.argv:
		run_auth_ui()
	else:
		run_app()
