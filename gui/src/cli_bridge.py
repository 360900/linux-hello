# Helpers for talking to the linux-hello-cli and reading the config
import configparser
import subprocess

import paths


def run_cli(arguments, timeout=120):
	"""
	Run a linux-hello-cli subcommand, returns (returncode, combined_output)
	"""
	try:
		completed = subprocess.run(
			[str(paths.cli_path)] + [str(a) for a in arguments],
			capture_output=True, text=True, timeout=timeout
		)
		return completed.returncode, (completed.stdout or "") + (completed.stderr or "")
	except FileNotFoundError:
		return 127, "linux-hello-cli not found"
	except subprocess.TimeoutExpired:
		return 124, "command timed out"


def config_file_path():
	return str(paths.config_dir / "config.ini")


def read_config():
	"""Parse the config file, returns a configparser (comments preserved on disk)"""
	config = configparser.ConfigParser()
	config.read(config_file_path())
	return config


def get_value(section, key, fallback=None):
	config = read_config()
	if not config.has_option(section, key):
		return fallback
	return config.get(section, key)


def set_value(key, value, section=None):
	"""
	Change a config value through the CLI so the file formatting and
	comments stay intact. Returns (returncode, output).
	"""
	arguments = ["set"]
	if section:
		arguments += ["--section", section]
	arguments += [key, str(value)]
	return run_cli(arguments)


def detect_camera():
	"""
	Auto-detect the best camera using the core discovery code.
	Returns the device path string or None.
	"""
	try:
		import sys
		if str(paths.core_lib_dir) not in sys.path:
			sys.path.insert(0, str(paths.core_lib_dir))
		from recorders.device_discovery import autodetect_device
		return autodetect_device()
	except Exception:
		return None


def discover_cameras():
	"""
	Probe all cameras, returns the sorted list of probe dicts from the
	core discovery code (best first), or None when unavailable.
	"""
	try:
		import sys
		if str(paths.core_lib_dir) not in sys.path:
			sys.path.insert(0, str(paths.core_lib_dir))
		from recorders.device_discovery import discover_cameras
		return [r for r in discover_cameras() if r.get("ok")]
	except Exception:
		return None


def list_users_with_models():
	"""
	Returns a dict {username: [model dicts]} read directly from the models
	directory. Model files are JSON with at least a "name" and "data" key.
	"""
	import os
	import json

	users = {}
	try:
		for user in os.listdir(str(paths.user_models_dir)):
			model_dir = str(paths.user_models_dir / user)
			if not os.path.isdir(model_dir):
				continue
			models = []
			for model_file in sorted(os.listdir(model_dir)):
				if not model_file_valid(model_file_name=model_file):
					continue
				try:
					with open(os.path.join(model_dir, model_file), "r") as f:
						model = json.load(f)
					model["id"] = int(os.path.splitext(model_file)[0])
					models.append(model)
				except (ValueError, OSError):
					continue
			users[user] = models
	except FileNotFoundError:
		pass
	return users


def model_file_valid(model_file_name):
	# Model files are <id>.dat where id is an integer
	name = model_file_name.split(".")
	if len(name) != 2 or name[1] != "dat":
		return False
	try:
		int(name[0])
	except ValueError:
		return False
	return True


def add_model(user, name):
	return run_cli(["add", name, "-y", "-U", user], timeout=120)


def remove_model(user, model_id):
	return run_cli(["remove", str(model_id), "-y", "-U", user], timeout=60)
