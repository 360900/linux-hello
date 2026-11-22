# GNOME: the lock screen and login screen go through GDM. Face unlock on the
# lock screen is the non-interactive gdm-face stack, started next to the
# password by the shell extension; the login screen gets a block in
# gdm-password; logging in automatically is a block in GDM's configuration.

import os
import shutil

import pytest

from integration import pam
from integration.pam import PamError, PamIntegration
from test_pam_integration import read, snapshot, tree, write

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
DM_LINK = "etc/systemd/system/display-manager.service"


def auth_lines(text):
	return [line for line in text.splitlines() if line.lstrip("-").startswith("auth")]


def modules(lines):
	return [os.path.basename(pam._module_of(line)[1]) for line in lines]


def gnome_only(distro, tmp_path):
	"""A tree without Plasma: Fedora Workstation, Debian GNOME"""
	root = tree(distro, tmp_path)
	for relative in ("etc/pam.d/kde", "etc/pam.d/kde-fingerprint", "etc/pam.d/sddm", "etc/pam.d/sddm-autologin",
			"usr/bin/sddm", "usr/lib/pam.d/kde", "usr/lib/pam.d/kde-fingerprint", "usr/libexec/kscreenlocker_greet",
			"usr/lib/x86_64-linux-gnu/libexec/kscreenlocker_greet"):
		path = os.path.join(root, relative)
		if os.path.exists(path):
			os.unlink(path)
	return root


# -- the lock screen's own stack ---------------------------------------------
def test_gdm_face_from_arch_fingerprint_stack(tmp_path):
	root = tree("arch", tmp_path)
	PamIntegration(root).enable("lock-screen")
	text = read(root, "etc/pam.d/gdm-face")
	auth = auth_lines(text)

	assert text.startswith(pam.OWN_HEADER)
	assert "the GNOME lock screen" in text
	# The fingerprint module gives way to ours; nothing else may grant access
	assert pam.OWN_STACK_LINE in auth
	assert not any("pam_fprintd.so" in line or "pam_permit.so" in line for line in auth)
	# The checks before it stay in front, and run once
	before = auth[:auth.index(pam.OWN_STACK_LINE)]
	assert modules(before) == ["pam_shells.so", "pam_nologin.so", "pam_faillock.so"]
	assert text.count("pam_faillock.so") == 1
	# GDM's own lines after it stay: the disk password for the keyring
	after = auth[auth.index(pam.OWN_STACK_LINE) + 1:]
	assert modules(after) == ["pam_env.so", "pam_gdm.so", "pam_gnome_keyring.so", "pam_oo7.so"]
	assert "session    include                     system-local-login" in text


def test_gdm_face_from_debian_fingerprint_stack(tmp_path):
	root = tree("debian", tmp_path)
	PamIntegration(root).enable("lock-screen")
	text = read(root, "etc/pam.d/gdm-face")
	auth = auth_lines(text)
	assert modules(auth) == ["pam_nologin.so", "pam_succeed_if.so", pam.MODULE, "pam_gnome_keyring.so"]
	# A password change through this stack must not reach the fingerprint
	# module either
	assert "pam_fprintd.so" not in text
	assert "@include common-account" in text
	assert "session optional        pam_gnome_keyring.so auto_start" in text


def test_gdm_face_from_fedora_substack_copies_the_lockout_check(tmp_path):
	root = tree("fedora", tmp_path)
	PamIntegration(root).enable("lock-screen")
	text = read(root, "etc/pam.d/gdm-face")
	assert auth_lines(text) == ["-auth\trequired\tpam_faillock.so preauth silent", pam.OWN_STACK_LINE]
	assert "account     include       fingerprint-auth" in text
	assert "session     include       postlogin" in text


def test_gdm_face_from_the_password_stack_without_fingerprint(tmp_path):
	root = tree("arch", tmp_path)
	os.unlink(os.path.join(root, "usr/lib/pam.d/gdm-fingerprint"))
	PamIntegration(root).enable("lock-screen")
	text = read(root, "etc/pam.d/gdm-face")
	# Includes could pull in the password: only the checks and our line
	assert auth_lines(text) == [
		"-auth\trequired\tpam_shells.so",
		"-auth\trequisite\tpam_nologin.so",
		"-auth\trequired\tpam_faillock.so preauth",
		pam.OWN_STACK_LINE,
	]


def test_gdm_face_leaves_the_password_stacks_blocks_behind(tmp_path):
	# Built from gdm-password while that carries the login and keyring
	# blocks: those belong to their targets, not to the copy
	root = gnome_only("fedora", tmp_path)
	os.unlink(os.path.join(root, "usr/lib/pam.d/gdm-fingerprint"))
	integration = PamIntegration(root)
	integration.enable("login")
	integration.enable("auto-login", user="alice")
	integration.enable("lock-screen")
	text = read(root, "etc/pam.d/gdm-face")
	assert not any(pam.has_block(text, begin) for begin in pam.BEGINS)
	assert "remember_password" not in text
	assert [line for line in text.splitlines() if pam.MODULE in line] == [pam.OWN_STACK_LINE]
	integration.disable("login")
	integration.disable("auto-login")
	assert read(root, "etc/pam.d/gdm-face") == text


def test_lock_screen_round_trip_with_both_desktops(tmp_path):
	root = tree("arch", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)
	integration.enable("lock-screen")
	assert os.path.exists(os.path.join(root, "etc/pam.d/kde-face"))
	assert os.path.exists(os.path.join(root, "etc/pam.d/gdm-face"))
	integration.disable("lock-screen")
	assert snapshot(root) == before


# -- the login screen ------------------------------------------------------------
def test_login_block_in_gdm_password_keeps_the_checks(tmp_path):
	root = tree("arch", tmp_path)
	PamIntegration(root).enable("login")
	lines = read(root, "etc/pam.d/gdm-password").splitlines()
	start = lines.index(pam.BEGIN)
	assert lines[start + 1:start + 5] == [
		"-auth\trequired\tpam_shells.so",
		"-auth\trequisite\tpam_nologin.so",
		"-auth\trequired\tpam_faillock.so preauth",
		pam.INSERT_LINE,
	]
	assert lines[start + 5] == pam.END
	assert lines[start + 6].split() == ["auth", "include", "system-local-login"]
	# GDM takes no password before PAM: not the login manager's line
	assert pam.LOGIN_INSERT_LINE not in lines


def test_login_block_in_debian_gdm_password(tmp_path):
	root = gnome_only("debian", tmp_path)
	integration = PamIntegration(root)
	integration.enable("login")
	lines = read(root, "etc/pam.d/gdm-password").splitlines()
	assert lines[1:5] == [pam.BEGIN, "-auth\trequisite\tpam_nologin.so", pam.INSERT_LINE, pam.END]
	assert lines[5].split() == ["auth", "requisite", "pam_nologin.so"]
	assert {t["id"]: t["services"] for t in integration.targets()}["login"] == ["gdm-password"]


# -- logging in automatically ------------------------------------------------------
GDM_CONF = "etc/gdm/custom.conf"


def test_gdm_auto_login_round_trip(tmp_path):
	root = gnome_only("fedora", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)

	integration.enable("auto-login", user="alice")
	text = read(root, GDM_CONF)
	# Last in [daemon], so the values win, right before the next group
	assert "#WaylandEnable=false\n" + pam.BEGIN + "\nAutomaticLoginEnable=true\nAutomaticLogin=alice\n" + pam.END + "\n\n[security]" in text
	assert integration.autologin_user() == "alice"
	assert {t["id"]: t["enabled"] for t in integration.targets()}["auto-login"]

	# pam_gdm skips the keyring line when it finds no disk password: our
	# block brings the password and its own copies of the keyring lines
	autologin = read(root, "etc/pam.d/gdm-autologin")
	block = pam.block_lines(autologin, pam.KEYRING_BEGIN)
	assert block == [pam.KEYRING_RELEASE_LINE, "-auth\toptional\tpam_gnome_keyring.so", "-auth\toptional\tpam_oo7.so"]
	assert autologin.index(pam.END) < autologin.index("pam_gdm.so")
	# and is taken at a login with the password
	login = read(root, "etc/pam.d/gdm-password")
	assert auth_lines(login)[-1] == pam.KEYRING_HOLD_LINE
	assert login.rstrip().endswith(pam.KEYRING_REMEMBER_LINE + "\n" + pam.END)

	integration.disable("auto-login")
	assert snapshot(root) == before
	assert integration.autologin_user() is None


def test_debian_auto_login_uses_daemon_conf(tmp_path):
	root = gnome_only("debian", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	text = read(root, "etc/gdm3/daemon.conf")
	assert "#  TimedLoginDelay = 10\n" + pam.BEGIN + "\nAutomaticLoginEnable=true\nAutomaticLogin=alice\n" + pam.END + "\n\n[security]" in text
	# KWallet's session line, from common-session, has no auth line in this
	# stack to take the password from: one is added for it
	assert pam.block_lines(read(root, "etc/pam.d/gdm-autologin"), pam.KEYRING_BEGIN) == [
		pam.KEYRING_RELEASE_LINE, "-auth\toptional\tpam_gnome_keyring.so", "-auth\toptional\tpam_kwallet5.so"]
	integration.disable("auto-login")
	assert snapshot(root) == before


def test_auto_login_refused_when_gdm_logs_in_someone_else(tmp_path):
	root = gnome_only("fedora", tmp_path)
	write(root, GDM_CONF, "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=bob\n")
	before = snapshot(root)
	with pytest.raises(PamError):
		PamIntegration(root).enable("auto-login", user="alice")
	assert snapshot(root) == before


@pytest.mark.parametrize("existing", [
	"[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=alice\n",
	"[daemon]\nAutomaticLoginEnable=false\nAutomaticLogin=bob\n",
])
def test_auto_login_next_to_a_harmless_setting(tmp_path, existing):
	root = gnome_only("fedora", tmp_path)
	write(root, GDM_CONF, existing)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	assert read(root, GDM_CONF) == existing + pam.BEGIN + "\nAutomaticLoginEnable=true\nAutomaticLogin=alice\n" + pam.END + "\n"
	integration.disable("auto-login")
	assert read(root, GDM_CONF) == existing


def test_missing_gdm_config_is_created_and_removed_again(tmp_path):
	root = gnome_only("fedora", tmp_path)
	os.unlink(os.path.join(root, GDM_CONF))
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	assert read(root, GDM_CONF) == "[daemon]\n" + pam.BEGIN + "\nAutomaticLoginEnable=true\nAutomaticLogin=alice\n" + pam.END + "\n"
	integration.disable("auto-login")
	assert not os.path.exists(os.path.join(root, GDM_CONF))


def test_unknown_gdm_config_location_is_not_offered(tmp_path):
	# Under /etc/gdm3 Debian reads daemon.conf and Ubuntu custom.conf
	root = gnome_only("debian", tmp_path)
	os.unlink(os.path.join(root, "etc/gdm3/daemon.conf"))
	assert not {t["id"]: t["available"] for t in PamIntegration(root).targets()}["auto-login"]


def test_auto_login_turned_off_in_the_users_settings_shows_as_off(tmp_path):
	# Settings > Users rewrites the values in place, markers and all
	root = gnome_only("fedora", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, GDM_CONF, read(root, GDM_CONF).replace("AutomaticLoginEnable=true", "AutomaticLoginEnable=false"))
	assert integration.autologin_user() is None
	assert not {t["id"]: t["enabled"] for t in integration.targets()}["auto-login"]
	integration.disable("auto-login")
	assert snapshot(root) == before


def test_reset_removes_the_gdm_block_without_a_manifest(tmp_path):
	root = gnome_only("fedora", tmp_path)
	original = read(root, GDM_CONF)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, pam.STATE_FILE, "{")
	changed = integration.reset()
	assert read(root, GDM_CONF) == original
	assert os.path.join(root, GDM_CONF) in changed
	assert pam.KEYRING_BEGIN not in read(root, "etc/pam.d/gdm-password")


def test_reset_removes_a_gdm_config_we_created(tmp_path):
	root = gnome_only("fedora", tmp_path)
	os.unlink(os.path.join(root, GDM_CONF))
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	changed = integration.reset()
	assert not os.path.exists(os.path.join(root, GDM_CONF))
	assert os.path.join(root, GDM_CONF) in changed


def test_display_manager_decides_the_auto_login(tmp_path):
	root = tree("arch", tmp_path)
	integration = PamIntegration(root)
	assert integration.display_manager() == "plasmalogin"
	link = os.path.join(root, DM_LINK)
	os.unlink(link)
	os.symlink("/usr/lib/systemd/system/gdm.service", link)
	assert integration.display_manager() == "gdm"
	assert {t["id"]: t["services"] for t in integration.targets()}["auto-login"] == ["gdm-autologin", "gdm-password"]
	# Without the alias, what is installed
	os.unlink(link)
	assert integration.display_manager() == "plasmalogin"
	os.unlink(os.path.join(root, "usr/bin/plasmalogin"))
	assert integration.display_manager() == "gdm"


def test_disable_auto_login_reaches_both_display_managers(tmp_path):
	root = tree("arch", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)
	link = os.path.join(root, DM_LINK)
	os.unlink(link)
	os.symlink("/usr/lib/systemd/system/gdm.service", link)
	integration.enable("auto-login", user="alice")
	assert pam.BEGIN in read(root, GDM_CONF)
	# Switched back to Plasma's login manager since: still turned off
	os.unlink(link)
	os.symlink("/usr/lib/systemd/system/plasmalogin.service", link)
	integration.disable("auto-login")
	after = snapshot(root)
	assert {k: v for k, v in after.items() if k != DM_LINK} == {k: v for k, v in before.items() if k != DM_LINK}


# -- descriptions ------------------------------------------------------------------
def test_descriptions_follow_the_desktops(tmp_path):
	both = {t["id"]: t["description"] for t in PamIntegration(tree("arch", tmp_path)).targets()}
	assert "Plasma:" in both["login"] and "GNOME:" in both["login"]
	assert "GNOME Shell" in both["lock-screen"]
	assert "KWallet" in both["auto-login"]

	gnome = {t["id"]: t["description"] for t in PamIntegration(gnome_only("fedora", tmp_path)).targets()}
	assert "GNOME Shell" in gnome["lock-screen"] and "Plasma" not in gnome["lock-screen"]
	assert "name is selected" in gnome["login"]
	assert "keyring" in gnome["auto-login"] and "KWallet" not in gnome["auto-login"]
	assert gnome["admin"] == both["admin"]


# -- helpers -----------------------------------------------------------------------
def test_insert_in_group():
	assert pam.insert_in_group("", "daemon", ["a=1"]) == "[daemon]\na=1\n"
	assert pam.insert_in_group("[other]\nx=1", "daemon", ["a=1"]) == "[other]\nx=1\n\n[daemon]\na=1\n"
	assert pam.insert_in_group("[daemon]\n", "daemon", ["a=1"]) == "[daemon]\na=1\n"
	assert pam.insert_in_group("[daemon]\nx=1\n\n\n[other]\n", "daemon", ["a=1"]) == "[daemon]\nx=1\na=1\n\n\n[other]\n"
	assert pam.insert_in_group("[other]\n[daemon]\nx=1", "daemon", ["a=1"]) == "[other]\n[daemon]\nx=1\na=1\n"


def test_keyring_companions_only_behind_pam_gdm():
	gdm = "auth [success=ok default=1] pam_gdm.so\n-auth optional pam_gnome_keyring.so\n-auth optional pam_oo7.so\nsession optional pam_gnome_keyring.so auto_start\n"
	assert pam.keyring_companions(gdm) == ["-auth\toptional\tpam_gnome_keyring.so", "-auth\toptional\tpam_oo7.so"]
	plasma = "auth required pam_permit.so\n-auth optional pam_gnome_keyring.so\n-auth optional pam_kwallet5.so\n"
	assert pam.keyring_companions(plasma) == []


def test_block_lines():
	text = "x\n" + pam.BEGIN + "\na=1\nb=2\n" + pam.END + "\ny\n"
	assert pam.block_lines(text) == ["a=1", "b=2"]
	assert pam.block_lines("nothing\n") == []
	assert pam.block_lines(pam.KEYRING_BEGIN + "\nz\n", pam.KEYRING_BEGIN) == ["z"]
