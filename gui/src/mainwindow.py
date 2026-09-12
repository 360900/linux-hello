# Main window with a KDE-style sidebar navigation
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
	QHBoxLayout,
	QLabel,
	QListWidget,
	QStackedWidget,
	QWidget,
)

import cli_bridge
import paths
import pagewelcome
import pagewizard
import pagemodels
import pagesettings
import pagetest


class MainWindow(QWidget):
	def __init__(self):
		super().__init__()
		self.setWindowTitle("Linux Hello")
		self.resize(880, 560)

		if os.path.exists(str(paths.logo_path)):
			self.setWindowIcon(QIcon(str(paths.logo_path)))

		layout = QHBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(0)

		# Sidebar navigation
		self.sidebar = QListWidget()
		self.sidebar.setFixedWidth(180)
		self.sidebar.addItems([
			"Welcome",
			"Setup",
			"Face models",
			"Settings",
			"Camera test",
		])
		layout.addWidget(self.sidebar)

		# Page stack
		self.stack = QStackedWidget()
		self.pages = {
			0: pagewelcome.WelcomePage(self.refresh_status),
			1: pagewizard.WizardPage(self.goto_models),
			2: pagemodels.ModelsPage(self.refresh_status),
			3: pagesettings.SettingsPage(),
			4: pagetest.TestPage(),
		}
		for index in sorted(self.pages):
			self.stack.addWidget(self.pages[index])
		layout.addWidget(self.stack, 1)

		self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
		self.sidebar.setCurrentRow(0)

	def goto_models(self):
		self.pages[2].reload()
		self.sidebar.setCurrentRow(2)

	def refresh_status(self):
		self.pages[0].reload()

	def closeEvent(self, event):
		test_page = self.pages.get(4)
		if test_page is not None:
			test_page.stop()
		super().closeEvent(event)
