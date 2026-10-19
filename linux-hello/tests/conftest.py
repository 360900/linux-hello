import os
import sys

# The python sources are not a package: make them importable as installed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


import shutil
import types
from pathlib import PurePath

import pytest

CONFIG_INI = os.path.join(os.path.dirname(__file__), "..", "src", "config.ini")


@pytest.fixture
def fake_paths(tmp_path, monkeypatch):
	"""Point the generated paths module at a scratch tree"""
	vendor = tmp_path / "usr/share/linux-hello/config.ini"
	vendor.parent.mkdir(parents=True)
	shutil.copy(CONFIG_INI, vendor)

	module = types.ModuleType("paths")
	module.config_dir = PurePath(tmp_path / "etc/linux-hello")
	module.state_dir = PurePath(tmp_path / "var/lib/linux-hello")
	module.user_models_dir = PurePath(tmp_path / "var/lib/linux-hello/models")
	module.legacy_models_dir = PurePath(tmp_path / "etc/linux-hello/models")
	module.vendor_config_path = PurePath(vendor)
	module.log_path = PurePath(tmp_path / "var/log/linux-hello")
	module.dlib_data_dir = PurePath(tmp_path / "usr/share/dlib-data")
	module.data_dir = PurePath(tmp_path / "usr/share/linux-hello")
	monkeypatch.setitem(sys.modules, "paths", module)

	# Modules bind "paths" at import time, so reload them against the fake
	for name in ("settings", "models"):
		sys.modules.pop(name, None)
	return module
