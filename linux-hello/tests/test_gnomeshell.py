import os


from integration import gnomeshell


def fake_tools(tmp_path, monkeypatch, enabled, info=None, disabled="@as []", user_off="false", hang=False):
	"""
	gsettings keeps each key in a file of that name; gdbus answers
	GetExtensionInfo with info. Returns the enabled list's file.
	"""
	bin_dir = tmp_path / "bin"
	bin_dir.mkdir(parents=True)
	keys = tmp_path / "keys"
	keys.mkdir()
	(keys / gnomeshell.ENABLED).write_text(enabled)
	(keys / gnomeshell.DISABLED).write_text(disabled)
	(keys / gnomeshell.USER_OFF).write_text(user_off)
	# Every set is counted in "sets"
	(bin_dir / "gsettings").write_text(
		'#!/bin/sh\n' + ('sleep 5\n' if hang else '') +
		'case "$1" in\n\tget) cat "{0}/$3" ;;\n\tset) printf "%s\\n" "$4" > "{0}/$3"; echo "$3" >> "{1}" ;;\nesac\n'.format(keys, tmp_path / "sets"))
	(bin_dir / "gdbus").write_text("#!/bin/sh\n" + ("exit 1\n" if info is None else "printf '%s\\n' \"{}\"\n".format(info)))
	for name in ("gsettings", "gdbus"):
		(bin_dir / name).chmod(0o755)
	monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
	return keys / gnomeshell.ENABLED


def test_enable_appends_to_the_shells_list(tmp_path, monkeypatch):
	store = fake_tools(tmp_path, monkeypatch, "['other@example.com']")
	assert not gnomeshell.enabled_for_user()
	gnomeshell.set_enabled(True)
	assert store.read_text().strip() == "['other@example.com', '{}']".format(gnomeshell.UUID)
	assert gnomeshell.enabled_for_user()
	# Already there: nothing rewritten
	gnomeshell.set_enabled(True)
	assert (tmp_path / "sets").read_text() == gnomeshell.ENABLED + "\n"


def test_disable_removes_only_ours(tmp_path, monkeypatch):
	store = fake_tools(tmp_path, monkeypatch, "['{}', 'other@example.com']".format(gnomeshell.UUID))
	gnomeshell.set_enabled(False)
	assert store.read_text().strip() == "['other@example.com']"


def test_empty_list_is_understood(tmp_path, monkeypatch):
	store = fake_tools(tmp_path, monkeypatch, "@as []")
	assert not gnomeshell.enabled_for_user()
	gnomeshell.set_enabled(True)
	assert store.read_text().strip() == "['{}']".format(gnomeshell.UUID)


def test_enable_takes_it_off_the_disabled_list(tmp_path, monkeypatch):
	# Switched off once in the Extensions app: that list wins over ours
	store = fake_tools(tmp_path, monkeypatch, "@as []",
		disabled="['other@example.com', '{}']".format(gnomeshell.UUID))
	assert not gnomeshell.enabled_for_user()
	gnomeshell.set_enabled(True)
	assert (store.parent / gnomeshell.DISABLED).read_text().strip() == "['other@example.com']"
	assert store.read_text().strip() == "['{}']".format(gnomeshell.UUID)
	assert gnomeshell.enabled_for_user()


def test_disable_leaves_the_disabled_list_alone(tmp_path, monkeypatch):
	store = fake_tools(tmp_path, monkeypatch, "['{}']".format(gnomeshell.UUID))
	gnomeshell.set_enabled(False)
	assert (store.parent / gnomeshell.DISABLED).read_text().strip() == "@as []"


def test_all_extensions_off_is_not_enabled_and_stays_the_users(tmp_path, monkeypatch):
	store = fake_tools(tmp_path, monkeypatch, "['{}']".format(gnomeshell.UUID), user_off="true")
	assert gnomeshell.user_extensions_off()
	assert not gnomeshell.enabled_for_user()
	gnomeshell.set_enabled(True)
	assert (store.parent / gnomeshell.USER_OFF).read_text().strip() == "true"


def test_a_hanging_gsettings_is_an_os_error(tmp_path, monkeypatch):
	import pytest
	import subprocess
	fake_tools(tmp_path, monkeypatch, "@as []", hang=True)
	real_run = subprocess.run
	monkeypatch.setattr(subprocess, "run", lambda *a, **k: real_run(*a, **{**k, "timeout": 0.2}))
	with pytest.raises(OSError):
		gnomeshell.set_enabled(True)
	assert not gnomeshell.enabled_for_user()
	assert not gnomeshell.user_extensions_off()


def test_loaded_reads_the_state(tmp_path, monkeypatch):
	fake_tools(tmp_path, monkeypatch, "@as []", info="({'uuid': <'x'>, 'state': <1.0>, 'type': <1.0>},)")
	assert gnomeshell.loaded() == 1
	fake_tools(tmp_path / "b", monkeypatch, "@as []", info="({},)")
	assert gnomeshell.loaded() == gnomeshell.NOT_LOADED
	# Loaded, not turned on yet: the state right after turning it on
	fake_tools(tmp_path / "d", monkeypatch, "@as []", info="({'uuid': <'x'>, 'state': <6.0>},)")
	assert gnomeshell.loaded() == 6 != gnomeshell.NOT_LOADED
	fake_tools(tmp_path / "c", monkeypatch, "@as []")
	assert gnomeshell.loaded() is None


def test_in_gnome_needs_the_desktop_and_a_session_bus():
	assert gnomeshell.in_gnome({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"})
	assert not gnomeshell.in_gnome({"XDG_CURRENT_DESKTOP": "KDE", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"})
	assert not gnomeshell.in_gnome({"XDG_CURRENT_DESKTOP": "GNOME"})


def test_installed_looks_for_the_metadata(tmp_path):
	assert not gnomeshell.installed(str(tmp_path))
	(tmp_path / gnomeshell.UUID).mkdir()
	(tmp_path / gnomeshell.UUID / "metadata.json").write_text("{}")
	assert gnomeshell.installed(str(tmp_path))
	# The real one must agree with the extension's metadata
	import json
	metadata = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "gnome", "extension", "metadata.json")))
	assert metadata["uuid"] == gnomeshell.UUID
