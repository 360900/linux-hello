# SDDM, Plasma's login manager before plasmalogin (Fedora KDE, Kubuntu,
# Debian): the login screen gets a block in "sddm"; logging in automatically
# is a drop-in in /etc/sddm.conf.d next to the one the Login Screen settings
# write, with the keyring lines in sddm-autologin, which has none of its own.

import json
import os

import pytest

from integration import pam
from integration.pam import PamError, PamIntegration
from test_pam_integration import read, snapshot, tree, write

DM_LINK = "etc/systemd/system/display-manager.service"
AUTOLOGIN = "etc/sddm.conf.d/linux-hello.conf"
KDE_SETTINGS = "etc/sddm.conf.d/kde_settings.conf"
MAIN = "etc/sddm.conf"


def sddm_boots(distro, tmp_path):
	"""A tree where SDDM is the display manager"""
	root = tree(distro, tmp_path)
	link = os.path.join(root, DM_LINK)
	os.unlink(link)
	os.symlink("/usr/lib/systemd/system/sddm.service", link)
	return root


def services(root):
	return {t["id"]: t["services"] for t in PamIntegration(root).targets()}


# -- target resolution ------------------------------------------------------
def test_auto_login_follows_sddm(tmp_path):
	for distro in ("fedora", "debian"):
		root = sddm_boots(distro, tmp_path)
		assert PamIntegration(root).display_manager() == "sddm"
		assert services(root)["auto-login"] == ["sddm-autologin", "sddm"], distro
		# The login screen's block is the same whichever manager boots
		assert services(root)["login"] == ["sddm", "gdm-password"], distro


@pytest.mark.parametrize("other", ["lightdm", "greetd", "ly"])
def test_no_auto_login_while_another_display_manager_boots(tmp_path, other):
	# SDDM and GDM installed, but neither boots
	root = sddm_boots("fedora", tmp_path)
	link = os.path.join(root, DM_LINK)
	os.unlink(link)
	os.symlink("/usr/lib/systemd/system/{}.service".format(other), link)
	integration = PamIntegration(root)
	assert integration.display_manager() is None
	assert not {t["id"]: t["available"] for t in integration.targets()}["auto-login"]
	with pytest.raises(PamError):
		integration.enable("auto-login", user="alice")


def test_installed_sddm_is_found_without_the_alias(tmp_path):
	root = tree("fedora", tmp_path)
	os.unlink(os.path.join(root, DM_LINK))
	# GDM and SDDM both installed and neither enabled: Plasma's manager first
	assert PamIntegration(root).display_manager() == "sddm"
	os.unlink(os.path.join(root, "usr/bin/sddm"))
	assert PamIntegration(root).display_manager() == "gdm"


def test_login_covers_every_login_screen_installed(tmp_path):
	# Arch with plasmalogin and SDDM side by side: switching between them
	# must not need face unlock to be set up again
	root = tree("arch", tmp_path)
	write(root, "etc/pam.d/sddm", read(root, "usr/lib/pam.d/plasmalogin"))
	assert services(root)["login"] == ["plasmalogin", "sddm", "gdm-password"]
	integration = PamIntegration(root)
	integration.enable("login")
	assert pam.LOGIN_INSERT_LINE in read(root, "etc/pam.d/sddm")
	assert pam.LOGIN_INSERT_LINE in read(root, "etc/pam.d/plasmalogin")


# -- auto-login ---------------------------------------------------------------
def test_fedora_auto_login_round_trip(tmp_path):
	root = sddm_boots("fedora", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)

	integration.enable("auto-login", user="alice")
	# No Session: SDDM then takes the last one used
	assert read(root, AUTOLOGIN).endswith("[Autologin]\nUser=alice\n")
	assert integration.autologin_user() == "alice"
	assert {t["id"]: t["enabled"] for t in integration.targets()}["auto-login"]

	# Fedora's sddm-autologin starts the keyrings in its session section but
	# has no auth lines to give them the password: ours brings them
	autologin = read(root, "etc/pam.d/sddm-autologin")
	assert pam.block_lines(autologin, pam.KEYRING_BEGIN) == [
		pam.KEYRING_RELEASE_LINE,
		"-auth\toptional\tpam_gnome_keyring.so",
		"-auth\toptional\tpam_kwallet5.so",
		"-auth\toptional\tpam_oo7.so",
	]
	assert autologin.index(pam.END) < autologin.index("pam_permit.so")
	# and the password is taken at a login with it
	login = read(root, "etc/pam.d/sddm")
	auth_lines = [line for line in login.splitlines() if line.lstrip("-").startswith("auth")]
	assert auth_lines[-1] == pam.KEYRING_HOLD_LINE
	assert login.rstrip().endswith(pam.KEYRING_REMEMBER_LINE + "\n" + pam.END)

	integration.disable("auto-login")
	assert snapshot(root) == before
	assert integration.autologin_user() is None


def test_debian_auto_login_round_trip(tmp_path):
	root = sddm_boots("debian", tmp_path)
	before = snapshot(root)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	# KWallet's session line comes from common-session
	assert pam.block_lines(read(root, "etc/pam.d/sddm-autologin"), pam.KEYRING_BEGIN) == [
		pam.KEYRING_RELEASE_LINE, "-auth\toptional\tpam_kwallet5.so"]
	integration.disable("auto-login")
	assert snapshot(root) == before


def test_session_named_when_plasma_is_installed(tmp_path):
	root = sddm_boots("fedora", tmp_path)
	write(root, "usr/share/wayland-sessions/plasma.desktop", "[Desktop Entry]\n")
	PamIntegration(root).enable("auto-login", user="alice")
	assert read(root, AUTOLOGIN).endswith("[Autologin]\nUser=alice\nSession=plasma\n")


@pytest.mark.parametrize("path", [MAIN, KDE_SETTINGS, "etc/sddm.conf.d/zz-autologin.conf"])
def test_auto_login_refused_when_sddm_logs_in_someone_else(tmp_path, path):
	root = sddm_boots("fedora", tmp_path)
	write(root, path, "[Autologin]\nUser=bob\nSession=plasma\n")
	before = snapshot(root)
	with pytest.raises(PamError):
		PamIntegration(root).enable("auto-login", user="alice")
	assert snapshot(root) == before


def test_empty_user_saved_before_ours_is_no_decision(tmp_path):
	# What the Login Screen settings save by default on Fedora KDE and
	# Kubuntu; kde_settings.conf is read before our drop-in
	root = sddm_boots("fedora", tmp_path)
	write(root, KDE_SETTINGS, "[Autologin]\nRelogin=false\nSession=\nUser=\n")
	before = snapshot(root)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	assert integration.autologin_user() == "alice"
	integration.disable("auto-login")
	assert snapshot(root) == before


@pytest.mark.parametrize("path", [MAIN, "etc/sddm.conf.d/zz-autologin.conf"])
def test_empty_user_read_after_ours_refuses(tmp_path, path):
	# It wins over ours: nobody would be logged in
	root = sddm_boots("fedora", tmp_path)
	write(root, path, "[Autologin]\nUser=\n")
	before = snapshot(root)
	with pytest.raises(PamError):
		PamIntegration(root).enable("auto-login", user="alice")
	assert snapshot(root) == before


def test_login_screen_settings_copying_our_user_is_undone(tmp_path):
	# Applying System Settings > Login Screen saves every value it sees,
	# ours too, into kde_settings.conf
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, KDE_SETTINGS, "[Autologin]\nSession=plasma\nUser=alice\n\n[Theme]\nCurrent=breeze\n")
	# Still ours
	integration.refresh()
	assert read(root, AUTOLOGIN).endswith("User=alice\n")
	integration.disable("auto-login")
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))
	assert read(root, KDE_SETTINGS) == "[Autologin]\nSession=plasma\n\n[Theme]\nCurrent=breeze\n"


def test_main_file_copy_is_undone_too(tmp_path):
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, MAIN, "[Autologin]\nUser=alice\n")
	integration.reset()
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))
	assert read(root, MAIN) == "[Autologin]\n"


def test_someone_elses_user_is_left_alone_on_disable(tmp_path):
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	# Set up later by the admin; theirs
	write(root, KDE_SETTINGS, "[Autologin]\nUser=bob\n")
	integration.disable("auto-login")
	assert read(root, KDE_SETTINGS) == "[Autologin]\nUser=bob\n"


def test_reset_without_manifest_removes_the_drop_in(tmp_path):
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, pam.STATE_FILE, "{")
	integration.reset()
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))
	assert pam.KEYRING_BEGIN not in read(root, "etc/pam.d/sddm")


def test_disable_works_once_sddm_is_gone(tmp_path):
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	os.unlink(os.path.join(root, DM_LINK))
	os.unlink(os.path.join(root, "usr/bin/sddm"))
	integration.disable("auto-login")
	assert not os.path.exists(os.path.join(root, AUTOLOGIN))
	assert pam.KEYRING_BEGIN not in read(root, "etc/pam.d/sddm")
	assert pam.KEYRING_BEGIN not in read(root, "etc/pam.d/sddm-autologin")


def test_read_only_directory_writes_the_main_file_in_place(tmp_path, monkeypatch):
	# Inside the service only /etc/sddm.conf itself is writable, not /etc
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	integration.enable("auto-login", user="alice")
	write(root, MAIN, "[Autologin]\nUser=alice\n")
	read_only_etc(monkeypatch, root)
	with pytest.raises(OSError):
		integration._write(os.path.join(root, "etc/new-file"), "x")
	integration.disable("auto-login")
	assert read(root, MAIN) == "[Autologin]\n"
	# Nothing left in the journal
	assert os.listdir(os.path.join(root, pam.JOURNAL_DIR)) == []


def read_only_etc(monkeypatch, root):
	"""As inside the service: files under /etc writable, the directory not"""
	import tempfile
	real = tempfile.mkstemp

	def mkstemp(*args, **kwargs):
		if kwargs.get("dir", "").startswith(os.path.join(root, "etc")):
			raise OSError(30, "Read-only file system")
		return real(*args, **kwargs)
	monkeypatch.setattr(tempfile, "mkstemp", mkstemp)


def test_failed_write_in_place_puts_the_old_text_back(tmp_path, monkeypatch):
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	write(root, MAIN, "[Theme]\nCurrent=breeze\n")
	read_only_etc(monkeypatch, root)
	real = integration._overwrite
	calls = []

	def disk_full(path, text):
		calls.append(text)
		if len(calls) == 1:
			# Cut short after the file was emptied
			real(path, "")
			raise OSError(28, "No space left on device")
		real(path, text)
	monkeypatch.setattr(integration, "_overwrite", disk_full)
	with pytest.raises(OSError):
		integration._write(os.path.join(root, MAIN), "[Theme]\nCurrent=other\n")
	assert read(root, MAIN) == "[Theme]\nCurrent=breeze\n"
	assert os.listdir(os.path.join(root, pam.JOURNAL_DIR)) == []


def test_write_in_place_cut_short_by_a_crash_is_put_back(tmp_path, monkeypatch):
	root = sddm_boots("fedora", tmp_path)
	integration = PamIntegration(root)
	write(root, MAIN, "[Theme]\nCurrent=breeze\n")
	read_only_etc(monkeypatch, root)

	def power_loss(path, text):
		with open(path, "w") as file:
			file.write(text[:3])
		raise KeyboardInterrupt
	monkeypatch.setattr(integration, "_overwrite", power_loss)
	with pytest.raises(KeyboardInterrupt):
		integration._write(os.path.join(root, MAIN), "[Theme]\nCurrent=other\n")
	assert read(root, MAIN) == "[Th"
	# The next start of the service, or the next change, puts it back
	fresh = PamIntegration(root)
	assert fresh.recover() == [os.path.join(root, MAIN)]
	assert read(root, MAIN) == "[Theme]\nCurrent=breeze\n"
	assert os.listdir(os.path.join(root, pam.JOURNAL_DIR)) == []
	assert fresh.recover() == []


def test_a_change_starts_by_putting_back_what_was_cut_short(tmp_path, monkeypatch):
	root = sddm_boots("fedora", tmp_path)
	write(root, MAIN, "[Autologin]\nUser=\n")
	integration = PamIntegration(root)
	journal = integration._journal(os.path.join(root, MAIN))
	write(root, os.path.relpath(journal, root), json.dumps({"path": os.path.join(root, MAIN), "text": "[Theme]\nCurrent=breeze\n"}))
	integration.enable("auto-login", user="alice")
	assert read(root, MAIN) == "[Theme]\nCurrent=breeze\n"
