import os
import stat

import pytest

from daemon import secrets


@pytest.fixture
def store(fake_paths, monkeypatch, tmp_path):
	# No TPM unless a test adds one
	monkeypatch.setattr(secrets, "TPM_DEVICE", str(tmp_path / "no-tpm"))
	return secrets


def test_round_trip_root_only(store):
	store.store("alice", b"hunter2")
	assert store.load("alice") == b"hunter2"
	path = store._path("alice")
	assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
	assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700


def test_forget(store):
	store.store("alice", b"hunter2")
	store.forget("alice")
	assert store.load("alice") is None and not store.exists("alice")
	store.forget("alice")


@pytest.mark.parametrize("secret", [b"", b"x" * (secrets.MAX_SIZE + 1)])
def test_not_a_password(store, secret):
	with pytest.raises(secrets.SecretError):
		store.store("alice", secret)


def fake_creds(tmp_path, monkeypatch, works=True):
	"""systemd-creds that "seals" by reversing, or fails"""
	bin_dir = tmp_path / "bin"
	bin_dir.mkdir()
	script = bin_dir / "systemd-creds"
	script.write_text("#!/bin/sh\n" + ("rev\n" if works else "echo no TPM >&2; exit 1\n"))
	script.chmod(0o755)
	monkeypatch.setenv("PATH", str(bin_dir) + ":" + os.environ["PATH"])
	(tmp_path / "tpm").touch()
	monkeypatch.setattr(secrets, "TPM_DEVICE", str(tmp_path / "tpm"))


def test_sealed_with_the_tpm(store, tmp_path, monkeypatch):
	fake_creds(tmp_path, monkeypatch)
	store.store("alice", b"hunter2")
	with open(store._path("alice"), "rb") as file:
		assert b"hunter2" not in file.read()
	assert store.load("alice") == b"hunter2"


def test_unsealed_when_the_tpm_fails(store, tmp_path, monkeypatch):
	fake_creds(tmp_path, monkeypatch, works=False)
	store.store("alice", b"hunter2")
	assert store.load("alice") == b"hunter2"


def test_unreadable_seal_is_no_password(store, tmp_path, monkeypatch):
	# Secure Boot or the TPM changed since
	fake_creds(tmp_path, monkeypatch)
	store.store("alice", b"hunter2")
	(tmp_path / "bin" / "systemd-creds").write_text("#!/bin/sh\nexit 1\n")
	assert store.load("alice") is None
