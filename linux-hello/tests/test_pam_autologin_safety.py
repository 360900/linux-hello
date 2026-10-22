# Ways the auto-login drop-in or our PAM lines could outlive turning them off,
# or take more with them than they should

import json
import os
import shutil

import pytest

from integration import pam
from integration.pam import PamError, PamIntegration

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
AUTOLOGIN = "etc/plasmalogin.conf.d/linux-hello.conf"
MAIN = "etc/plasmalogin.conf"


def arch(tmp_path):
	root = tmp_path / "arch"
	shutil.copytree(os.path.join(FIXTURES, "arch"), root)
	return str(root)


def snapshot(root):
	files = {}
	for directory, _dirs, names in os.walk(root):
		for name in names:
			path = os.path.join(directory, name)
			relative = os.path.relpath(path, root)
			if relative != pam.STATE_FILE:
				with open(path, "rb") as file:
					files[relative] = file.read()
	return files


def read(root, relative):
	with open(os.path.join(root, relative)) as file:
		return file.read()


def write(root, relative, text):
	path = os.path.join(root, relative)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as file:
		file.write(text)


def test_login_screen_settings_copying_our_user_is_undone(tmp_path):
	# Applying System Settings > Login Screen saves the drop-in's values
	# into plasmalogin.conf too
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, MAIN, "[Autologin]\nSession=plasma\nUser=alice\n")
	# Still ours, so a refresh at start keeps working
	integration.refresh()

	integration.disable("auto-login")
	assert read(root, MAIN) == "[Autologin]\nSession=plasma\n"
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))


def test_reset_also_undoes_that_copy(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, MAIN, "[Autologin]\nUser=alice\nSession=plasma\n")
	integration.reset()
	assert read(root, MAIN) == "[Autologin]\nSession=plasma\n"


def test_someone_elses_user_is_left_alone(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, MAIN, "[Autologin]\nUser=bob\n")
	integration.disable("auto-login")
	assert read(root, MAIN) == "[Autologin]\nUser=bob\n"


def test_edited_drop_in_still_goes(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, AUTOLOGIN, "# keep for kiosk\n" + read(root, AUTOLOGIN))
	assert {t["id"]: t["enabled"] for t in integration.targets()}["auto-login"]
	integration.disable("auto-login")
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))


def test_reset_with_a_corrupt_manifest_still_removes_the_drop_in(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, pam.STATE_FILE, "{")
	integration.reset()
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))


def test_failed_enable_leaves_everything_as_it_was(tmp_path):
	root = arch(tmp_path)
	# The KWallet line goes after an auth line this file does not have
	write(root, "etc/pam.d/plasmalogin", "#%PAM-1.0\n@include plasmalogin-common\n")
	before = snapshot(root)
	integration = PamIntegration(root)
	with pytest.raises(PamError):
		integration.enable("auto-login", user="alice")
	assert snapshot(root) == before
	assert not {t["id"]: t["enabled"] for t in integration.targets()}["auto-login"]


def test_drop_in_without_kwallet_lines_shows_as_on_and_refresh_repairs_it(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	os.unlink(os.path.join(root, "etc/pam.d/plasmalogin"))
	assert {t["id"]: t["enabled"] for t in integration.targets()}["auto-login"]
	integration.refresh()
	assert pam.WALLET_BEGIN in read(root, "etc/pam.d/plasmalogin")


def test_disable_works_once_plasma_login_is_gone(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	os.unlink(os.path.join(root, "usr/bin/plasmalogin"))
	integration.disable("auto-login")
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))
	# Our copy goes, so the vendor file applies again
	assert not os.path.exists(os.path.join(root, "etc/pam.d/plasmalogin-autologin"))


def test_admins_own_file_is_not_deleted_for_an_old_manifest_entry(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	integration.disable("auto-login")
	# The manifest once said "copied"; the admin then writes their own file
	integration.enable("auto-login", user="alice")
	os.unlink(os.path.join(root, "etc/pam.d/plasmalogin"))
	own = read(root, "usr/lib/pam.d/plasmalogin") + "auth required pam_exec.so /usr/local/bin/admin-hook\n"
	write(root, "etc/pam.d/plasmalogin", own)
	integration.enable("login")
	integration.disable("login")
	assert read(root, "etc/pam.d/plasmalogin") != ""
	assert "admin-hook" in read(root, "etc/pam.d/plasmalogin")


def test_block_without_its_end_keeps_the_rest_of_the_stack():
	text = "auth include system-login\n" + pam.BEGIN + "\n" + pam.INSERT_LINE + "\naccount include system-login\nsession include system-login\n"
	cleaned = pam.remove_block(text)
	assert cleaned == "auth include system-login\naccount include system-login\nsession include system-login\n"


def test_login_screen_line_ends_the_stack_on_a_failed_match(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("login")
	integration.enable("sudo")
	assert pam.LOGIN_INSERT_LINE in read(root, "etc/pam.d/plasmalogin")
	assert pam.LOGIN_INSERT_LINE not in read(root, "etc/pam.d/sudo")
	assert pam.INSERT_LINE in read(root, "etc/pam.d/sudo")


def test_older_kwallet_lines_are_brought_up_to_date(tmp_path):
	root = arch(tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	path = "etc/pam.d/plasmalogin-autologin"
	write(root, path, read(root, path).replace(pam.WALLET_RELEASE_LINE, "-auth optional pam_linux_hello.so old_kwallet"))
	integration.refresh()
	text = read(root, path)
	assert pam.WALLET_RELEASE_LINE in text and "old_kwallet" not in text
	assert text.count(pam.WALLET_BEGIN) == 1


def test_manifest_stays_valid_json_after_a_rollback(tmp_path):
	root = arch(tmp_path)
	write(root, "etc/pam.d/plasmalogin", "#%PAM-1.0\n@include plasmalogin-common\n")
	integration = PamIntegration(root)
	with pytest.raises(PamError):
		integration.enable("auto-login", user="alice")
	with open(os.path.join(root, pam.STATE_FILE)) as file:
		json.load(file)
