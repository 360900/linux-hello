import json
import os

import pytest


@pytest.fixture
def models(fake_paths, monkeypatch):
	import models
	monkeypatch.setattr(os, "geteuid", lambda: 0)
	return models


def test_add_list_remove(models):
	assert models.load("alice") == []
	first = models.add("alice", "Initial, model", [[0.1, 0.2]], 1700000000)
	second = models.add("alice", "glasses", [[0.3, 0.4]], 1700000100)
	assert first == {"id": 0, "label": "Initial model", "time": 1700000000}
	assert second["id"] == 1
	assert models.users() == ["alice"]

	path = os.path.join(models.models_dir(), "alice.dat")
	assert os.stat(path).st_mode & 0o777 == 0o600
	assert os.stat(models.models_dir()).st_mode & 0o777 == 0o700

	assert models.remove("alice", 0)
	assert not models.remove("alice", 0)
	assert [m["label"] for m in models.load("alice")] == ["glasses"]

	# New ids continue after the highest existing one
	assert models.add("alice", "third", [[0.5]], 1700000200)["id"] == 2

	assert models.remove("alice", 1) and models.remove("alice", 2)
	assert not os.path.exists(path)
	assert models.users() == []


def test_rename_truncates(models):
	models.add("bob", "a", [[0.1]], 1)
	assert models.rename("bob", 0, "x" * 40)
	assert models.load("bob")[0]["label"] == "x" * 24
	assert not models.rename("bob", 7, "nope")


def test_summary_has_no_biometrics(models):
	models.add("bob", "a", [[0.1]], 1)
	assert "data" not in models.summary(models.load("bob")[0])


@pytest.mark.parametrize("name", ["../etc/shadow", "a/b", "", ".hidden", "x" * 300])
def test_rejects_unsafe_user_names(models, name):
	with pytest.raises(models.ModelError):
		models.load(name)


def test_corrupt_file_raises(models):
	os.makedirs(models.models_dir())
	with open(os.path.join(models.models_dir(), "eve.dat"), "w") as file:
		file.write("{not json")
	with pytest.raises(models.ModelError):
		models.load("eve")
	assert models.users() == []


def test_legacy_models_are_migrated(models, fake_paths):
	legacy = str(fake_paths.legacy_models_dir)
	os.makedirs(legacy)
	entry = [{"time": 1, "label": "old", "id": 0, "data": [[0.1]]}]
	with open(os.path.join(legacy, "carol.dat"), "w") as file:
		json.dump(entry, file)

	assert models.users() == ["carol"]
	assert models.load("carol") == entry
	assert not os.path.exists(legacy)
	assert os.stat(os.path.join(models.models_dir(), "carol.dat")).st_mode & 0o777 == 0o600


def test_migration_never_overwrites(models, fake_paths):
	models.add("dave", "new", [[0.9]], 2)
	legacy = str(fake_paths.legacy_models_dir)
	os.makedirs(legacy)
	with open(os.path.join(legacy, "dave.dat"), "w") as file:
		json.dump([{"time": 1, "label": "old", "id": 0, "data": [[0.1]]}], file)

	assert models.load("dave")[0]["label"] == "new"
	# The conflicting legacy file is left for the admin to look at
	assert os.path.exists(os.path.join(legacy, "dave.dat"))


def test_migration_skipped_without_root(models, fake_paths, monkeypatch):
	monkeypatch.setattr(os, "geteuid", lambda: 1000)
	legacy = str(fake_paths.legacy_models_dir)
	os.makedirs(legacy)
	with open(os.path.join(legacy, "erin.dat"), "w") as file:
		json.dump([{"time": 1, "label": "old", "id": 0, "data": [[0.1]]}], file)
	assert models.migrate_legacy() == []
	assert os.path.exists(os.path.join(legacy, "erin.dat"))


def test_migration_failure_is_not_fatal(models, fake_paths, monkeypatch):
	legacy = str(fake_paths.legacy_models_dir)
	os.makedirs(legacy)
	with open(os.path.join(legacy, "frank.dat"), "w") as file:
		json.dump([{"time": 1, "label": "old", "id": 0, "data": [[0.1]]}], file)

	def read_only(*args, **kwargs):
		raise OSError(30, "Read-only file system")

	monkeypatch.setattr(os, "makedirs", read_only)
	assert models.load("frank") == []
	assert os.path.exists(os.path.join(legacy, "frank.dat"))
