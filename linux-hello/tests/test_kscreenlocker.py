import os

from integration import kscreenlocker


def write(path, text):
	with open(path, "w") as file:
		file.write(text)


def read(path):
	with open(path) as file:
		return file.read()


def test_creates_the_state_file(tmp_path):
	path = str(tmp_path / "state" / "kscreenlockerrc")
	kscreenlocker.prefer_face(path)
	assert read(path) == "[Greeter]\nAuthenticator=Face\n"
	assert os.stat(path).st_mode & 0o777 == 0o600


def test_replaces_the_choice_and_keeps_the_rest(tmp_path):
	path = str(tmp_path / "kscreenlockerrc")
	write(path, "[Greeter]\nAuthenticator=Regular\nOther=1\n\n[Daemon]\nTimeout=5\n")
	kscreenlocker.prefer_face(path)
	assert read(path) == "[Greeter]\nAuthenticator=Face\nOther=1\n\n[Daemon]\nTimeout=5\n"


def test_adds_the_key_inside_an_existing_group(tmp_path):
	path = str(tmp_path / "kscreenlockerrc")
	write(path, "[Greeter]\nOther=1\n[Daemon]\nTimeout=5\n")
	kscreenlocker.prefer_face(path)
	assert read(path) == "[Greeter]\nOther=1\nAuthenticator=Face\n[Daemon]\nTimeout=5\n"
	assert kscreenlocker.read_choice(path) == "Face"


def test_adds_the_group_after_others(tmp_path):
	path = str(tmp_path / "kscreenlockerrc")
	write(path, "[Daemon]\nTimeout=5\n")
	kscreenlocker.prefer_face(path)
	assert read(path) == "[Daemon]\nTimeout=5\n\n[Greeter]\nAuthenticator=Face\n"


def test_disabling_only_undoes_face(tmp_path):
	path = str(tmp_path / "kscreenlockerrc")
	write(path, "[Greeter]\nAuthenticator=Fingerprint\n")
	kscreenlocker.stop_preferring_face(path)
	assert kscreenlocker.read_choice(path) == "Fingerprint"

	kscreenlocker.prefer_face(path)
	kscreenlocker.stop_preferring_face(path)
	assert kscreenlocker.read_choice(path) == "Regular"


def test_state_path_follows_xdg():
	assert kscreenlocker.state_path({"XDG_STATE_HOME": "/x"}) == "/x/kscreenlockerrc"
