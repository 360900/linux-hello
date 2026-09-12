# Face models page: list, add and remove models per user
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
	QComboBox,
	QHBoxLayout,
	QInputDialog,
	QLabel,
	QMessageBox,
	QPushButton,
	QTableWidget,
	QTableWidgetItem,
	QVBoxLayout,
	QWidget,
)

import cli_bridge


class ModelsPage(QWidget):
	def __init__(self, on_change=None):
		super().__init__()
		self.on_change = on_change

		root = QVBoxLayout(self)
		root.setContentsMargins(40, 40, 40, 40)
		root.setSpacing(16)

		root.addWidget(QLabel("<h2>Face models</h2>"))

		controls = QHBoxLayout()
		controls.addWidget(QLabel("User:"))
		self.user_combo = QComboBox()
		self.user_combo.currentTextChanged.connect(self.reload)
		controls.addWidget(self.user_combo, 1)

		add_button = QPushButton("Add model")
		add_button.clicked.connect(self.add_model)
		controls.addWidget(add_button)
		root.addLayout(controls)

		self.table = QTableWidget(0, 2)
		self.table.setHorizontalHeaderLabels(["ID", "Name"])
		self.table.horizontalHeader().setStretchLastSection(True)
		self.table.verticalHeader().setVisible(False)
		self.table.setSelectionBehavior(QTableWidget.SelectRows)
		self.table.setEditTriggers(QTableWidget.NoEditTriggers)
		root.addWidget(self.table, 1)

		buttons = QHBoxLayout()
		remove_button = QPushButton("Remove selected")
		remove_button.clicked.connect(self.remove_selected)
		buttons.addWidget(remove_button)
		buttons.addStretch(1)
		root.addLayout(buttons)

		self.status = QLabel("")
		self.status.setWordWrap(True)
		root.addWidget(self.status)

		self.reload()

	def reload(self):
		# Refresh user list
		users = sorted(cli_bridge.list_users_with_models().keys())
		current = self.user_combo.currentText()
		self.user_combo.blockSignals(True)
		self.user_combo.clear()
		self.user_combo.addItems(users)
		if current:
			self.user_combo.setCurrentText(current)
		self.user_combo.blockSignals(False)

		self.reload_table()

	def reload_table(self):
		user = self.user_combo.currentText()
		self.table.setRowCount(0)
		if not user:
			return
		users = cli_bridge.list_users_with_models()
		for model in users.get(user, []):
			row = self.table.rowCount()
			self.table.insertRow(row)
			self.table.setItem(row, 0, QTableWidgetItem(str(model.get("id", ""))))
			self.table.setItem(row, 1, QTableWidgetItem(str(model.get("name", ""))))

	def add_model(self):
		user = self.user_combo.currentText()
		if not user:
			return
		name, ok = QInputDialog.getText(self, "Add model", "Model name for user {}:".format(user))
		if not ok or not name.strip():
			return
		code, output = cli_bridge.add_model(user, name.strip())
		if code != 0:
			QMessageBox.warning(self, "Add model", "Failed:\n" + output.strip()[-500:])
		self.reload()
		if self.on_change is not None:
			self.on_change()

	def remove_selected(self):
		user = self.user_combo.currentText()
		row = self.table.currentRow()
		if not user or row < 0:
			return
		model_id = self.table.item(row, 0).text()
		name = self.table.item(row, 1).text()
		confirm = QMessageBox.question(
			self, "Remove model",
			"Remove model '{}' (id {}) for user {}?".format(name, model_id, user)
		)
		if confirm != QMessageBox.Yes:
			return
		code, output = cli_bridge.remove_model(user, model_id)
		if code != 0:
			QMessageBox.warning(self, "Remove model", "Failed:\n" + output.strip()[-500:])
		self.reload_table()
		if self.on_change is not None:
			self.on_change()
