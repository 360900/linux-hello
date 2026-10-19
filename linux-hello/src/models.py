# Face model storage.
#
# One JSON file per user at <user_models_dir>/<user>.dat holding a list of
# {"id", "label", "time", "data"} entries. Files are root-only (0600) and
# written atomically. Older versions kept them in /etc/linux-hello/models;
# they are moved to the state directory the first time root reads them.

import json
import os
import pwd
import re
import tempfile

import paths

# Usernames as accepted by shadow-utils, so a name can never escape the
# models directory
_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_.-]*\$?$", re.IGNORECASE)


class ModelError(Exception):
	pass


def models_dir():
	return str(paths.user_models_dir)


def _path(user):
	if not _USER_PATTERN.match(user or "") or len(user) > 256:
		raise ModelError("invalid user name: {!r}".format(user))
	return os.path.join(models_dir(), user + ".dat")


def migrate_legacy():
	"""
	Move <user>.dat files from the old models directory. Only runs as root,
	never overwrites, and removes the old directory once it is empty.
	Returns the list of users moved.
	"""
	legacy = str(getattr(paths, "legacy_models_dir", ""))
	if not legacy or legacy == models_dir() or not os.path.isdir(legacy) or os.geteuid() != 0:
		return []

	os.makedirs(models_dir(), mode=0o700, exist_ok=True)
	moved = []
	for name in sorted(os.listdir(legacy)):
		source = os.path.join(legacy, name)
		target = os.path.join(models_dir(), name)
		if not name.endswith(".dat") or not os.path.isfile(source) or os.path.exists(target):
			continue
		if _same_device(source, models_dir()):
			os.replace(source, target)
		else:
			_copy_then_remove(source, target)
		os.chmod(target, 0o600)
		moved.append(name[:-4])
	try:
		os.rmdir(legacy)
	except OSError:
		pass
	return moved


def _same_device(path, directory):
	return os.stat(path).st_dev == os.stat(directory).st_dev


def _copy_then_remove(source, target):
	with open(source, "rb") as file:
		data = file.read()
	_atomic_write(target, data)
	os.unlink(source)


def load(user):
	"""Return the list of models of a user, empty when there are none"""
	migrate_legacy()
	try:
		with open(_path(user), "r") as file:
			models = json.load(file)
	except FileNotFoundError:
		return []
	except ValueError as error:
		raise ModelError("corrupt model file for {}: {}".format(user, error))
	if not isinstance(models, list):
		raise ModelError("corrupt model file for {}".format(user))
	return models


def save(user, models):
	"""Replace a user's models; an empty list removes the file"""
	path = _path(user)
	if not models:
		delete(user)
		return
	os.makedirs(models_dir(), mode=0o700, exist_ok=True)
	_atomic_write(path, json.dumps(models).encode("utf-8"))


def delete(user):
	try:
		os.unlink(_path(user))
	except FileNotFoundError:
		pass


def users():
	"""Users that have at least one model"""
	migrate_legacy()
	try:
		names = os.listdir(models_dir())
	except FileNotFoundError:
		return []
	found = []
	for name in sorted(names):
		if name.endswith(".dat") and _USER_PATTERN.match(name[:-4]):
			try:
				if load(name[:-4]):
					found.append(name[:-4])
			except ModelError:
				continue
	return found


def add(user, label, encodings, now):
	"""Append a model and return it (without the encoding data)"""
	existing = load(user)
	model = {
		"time": int(now),
		"label": label.replace(",", "")[:24] or "Model",
		"id": (max(m["id"] for m in existing) + 1) if existing else 0,
		"data": [list(map(float, e)) for e in encodings],
	}
	existing.append(model)
	save(user, existing)
	return summary(model)


def remove(user, model_id):
	"""Remove one model by id. Returns False when it did not exist."""
	existing = load(user)
	kept = [m for m in existing if m["id"] != model_id]
	if len(kept) == len(existing):
		return False
	save(user, kept)
	return True


def rename(user, model_id, label):
	existing = load(user)
	for model in existing:
		if model["id"] == model_id:
			model["label"] = label.replace(",", "")[:24] or model["label"]
			save(user, existing)
			return True
	return False


def summary(model):
	"""A model without its biometric data, safe to hand to clients"""
	return {"id": model["id"], "label": model.get("label", ""), "time": model.get("time", 0)}


def user_exists(user):
	try:
		pwd.getpwnam(user)
	except KeyError:
		return False
	return True


def _atomic_write(path, data):
	directory = os.path.dirname(path)
	descriptor, temporary = tempfile.mkstemp(prefix=".model-", dir=directory)
	try:
		os.fchmod(descriptor, 0o600)
		with os.fdopen(descriptor, "wb") as file:
			file.write(data)
			file.flush()
			os.fsync(file.fileno())
		os.replace(temporary, path)
	except BaseException:
		if os.path.exists(temporary):
			os.unlink(temporary)
		raise
