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


def test_face_enabled_user_value_wins(tmp_path):
	user = str(tmp_path / "user")
	system = str(tmp_path / "system")
	assert not kscreenlocker.face_enabled(user, system)

	write(system, "[Authenticators]\nFace=true\n")
	assert kscreenlocker.face_enabled(user, system)

	write(user, "[Authenticators]\nFace=false\n")
	assert not kscreenlocker.face_enabled(user, system)

	# Later entries win, as in KConfig
	write(user, "[Authenticators]\nFace=false\n[Other]\nx=1\n[Authenticators]\nFace=true\n")
	assert kscreenlocker.face_enabled(user, system)


def fake_users(monkeypatch, tmp_path, choices):
	import pwd
	me = pwd.getpwuid(os.getuid())
	entries = []
	for name, choice in choices.items():
		path = str(tmp_path / name / "kscreenlockerrc")
		os.makedirs(os.path.dirname(path))
		if choice:
			write(path, "[Greeter]\nAuthenticator={}\n".format(choice))
		entry = pwd.struct_passwd((name, "x", me.pw_uid, me.pw_gid, "", str(tmp_path / name), "/bin/sh"))
		entries.append((entry, path))
	monkeypatch.setattr(kscreenlocker, "user_state_paths", lambda: entries)
	return entries


def test_users_preferring_face(monkeypatch, tmp_path):
	fake_users(monkeypatch, tmp_path, {"alice": "Face", "bob": "Regular", "carol": None})
	assert kscreenlocker.users_preferring_face() == ["alice"]


def test_reset_all_users_only_touches_face(monkeypatch, tmp_path):
	entries = fake_users(monkeypatch, tmp_path, {"alice": "Face", "bob": "Fingerprint"})
	assert kscreenlocker.reset_all_users() == ["alice"]
	assert kscreenlocker.read_choice(entries[0][1]) == "Regular"
	assert kscreenlocker.read_choice(entries[1][1]) == "Fingerprint"


def test_as_user_runs_with_the_users_identity(tmp_path):
	import pwd
	me = pwd.getpwuid(os.getuid())
	seen = []
	assert kscreenlocker._as_user(me, lambda: seen.append(os.geteuid()))
	assert seen == [os.getuid()]
