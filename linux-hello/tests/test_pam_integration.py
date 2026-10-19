import os
import shutil

import pytest

from integration import pam
from integration.pam import PamError, PamIntegration

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
DISTROS = ["arch", "fedora", "debian"]


def snapshot(root, skip_state=True):
	"""Map of relative path to content for every file under root"""
	files = {}
	for directory, _dirs, names in os.walk(root):
		for name in names:
			path = os.path.join(directory, name)
			relative = os.path.relpath(path, root)
			if skip_state and relative == pam.STATE_FILE:
				continue
			with open(path, "rb") as file:
				files[relative] = file.read()
	return files


def read(root, relative):
	with open(os.path.join(root, relative), "r") as file:
		return file.read()


@pytest.fixture(params=DISTROS)
def system(request, tmp_path):
	root = tmp_path / request.param
	shutil.copytree(os.path.join(FIXTURES, request.param), root)
	return request.param, str(root)


def tree(distro, tmp_path):
	root = tmp_path / distro
	shutil.copytree(os.path.join(FIXTURES, distro), root)
	return str(root)


# -- target resolution ------------------------------------------------------
def test_targets_resolve_per_distro(tmp_path):
	expected = {
		"arch": {
			"lock-screen": ["kde-face"],
			"admin": ["polkit-1"],
			"sudo": ["sudo"],
			"login": ["plasmalogin"],
		},
		"fedora": {
			"lock-screen": ["kde-face"],
			"admin": ["polkit-1"],
			# sudo-i includes sudo on Fedora
			"sudo": ["sudo"],
			"login": ["sddm"],
		},
		"debian": {
			# Greeter without kde-face support: patch the interactive stack
			"lock-screen": ["kde"],
			"admin": ["polkit-1"],
			"sudo": ["sudo", "sudo-i"],
			"login": ["sddm"],
		},
	}
	for distro, targets in expected.items():
		integration = PamIntegration(tree(distro, tmp_path))
		found = {t["id"]: t["services"] for t in integration.targets()}
		assert found == targets, distro
		assert not any(t["enabled"] for t in integration.targets())


def test_global_stacks_are_never_touched(system):
	_distro, root = system
	before = snapshot(root)
	integration = PamIntegration(root)
	for target in integration.targets():
		integration.enable(target["id"])
	after = snapshot(root)
	for name in ("system-auth", "system-login", "system-local-login", "common-auth"):
		relative = os.path.join("etc/pam.d", name)
		assert before.get(relative) == after.get(relative)


# -- round trips ------------------------------------------------------------
@pytest.mark.parametrize("target", ["lock-screen", "admin", "sudo", "login"])
def test_enable_disable_restores_tree(system, target):
	_distro, root = system
	before = snapshot(root)
	integration = PamIntegration(root)

	integration.enable(target)
	assert snapshot(root) != before
	assert {t["id"]: t["enabled"] for t in integration.targets()}[target]

	integration.disable(target)
	assert snapshot(root) == before
	assert not {t["id"]: t["enabled"] for t in integration.targets()}[target]


def test_reset_restores_tree(system):
	_distro, root = system
	before = snapshot(root)
	integration = PamIntegration(root)
	for target in integration.targets():
		integration.enable(target["id"])
	integration.reset()
	assert snapshot(root) == before


def test_enable_is_idempotent(system):
	_distro, root = system
	integration = PamIntegration(root)
	integration.enable("sudo")
	once = snapshot(root, skip_state=False)
	integration.enable("sudo")
	assert snapshot(root, skip_state=False) == once
	assert read(root, "etc/pam.d/sudo").count(pam.BEGIN) == 1


# -- insertion --------------------------------------------------------------
def test_block_goes_before_first_auth_line(tmp_path):
	root = tree("arch", tmp_path)
	PamIntegration(root).enable("sudo")
	lines = read(root, "etc/pam.d/sudo").splitlines()
	assert lines[0] == "#%PAM-1.0"
	assert lines[1:4] == [pam.BEGIN, pam.INSERT_LINE, pam.END]
	# Ahead of fprintd, so a face match wins over waiting for a finger
	assert "pam_fprintd.so" in lines[4]


def test_block_goes_before_debian_include(tmp_path):
	root = tree("debian", tmp_path)
	PamIntegration(root).enable("sudo")
	lines = read(root, "etc/pam.d/sudo").splitlines()
	index = lines.index(pam.BEGIN)
	assert lines[index + 3] == "@include common-auth"
	# The session lines above stay where they were
	assert all(not line.startswith("@include") for line in lines[:index])


def test_block_goes_before_control_group_line(tmp_path):
	root = tree("fedora", tmp_path)
	PamIntegration(root).enable("login")
	lines = read(root, "etc/pam.d/sddm").splitlines()
	assert lines[1] == pam.BEGIN
	assert "pam_selinux_permit.so" in lines[4]


def test_insert_line_falls_through_on_failure():
	# default=ignore keeps the password working when the module fails,
	# returns PAM_IGNORE or is missing entirely
	assert "default=ignore" in pam.INSERT_LINE
	assert "success=done" in pam.INSERT_LINE


# -- vendor copies ----------------------------------------------------------
def test_vendor_only_service_is_copied_then_removed(tmp_path):
	root = tree("arch", tmp_path)
	integration = PamIntegration(root)
	assert not os.path.exists(os.path.join(root, "etc/pam.d/polkit-1"))

	integration.enable("admin")
	copied = read(root, "etc/pam.d/polkit-1")
	assert pam.remove_block(copied) == read(root, "usr/lib/pam.d/polkit-1")

	integration.disable("admin")
	assert not os.path.exists(os.path.join(root, "etc/pam.d/polkit-1"))


def test_edited_copy_is_kept_on_disable(tmp_path):
	root = tree("arch", tmp_path)
	integration = PamIntegration(root)
	integration.enable("admin")
	path = os.path.join(root, "etc/pam.d/polkit-1")
	with open(path, "a") as file:
		file.write("# local admin change\n")

	integration.disable("admin")
	text = read(root, "etc/pam.d/polkit-1")
	assert pam.BEGIN not in text
	assert pam.MODULE not in text
	assert text.endswith("# local admin change\n")


def test_mode_is_preserved(tmp_path):
	root = tree("fedora", tmp_path)
	path = os.path.join(root, "etc/pam.d/sudo")
	os.chmod(path, 0o640)
	PamIntegration(root).enable("sudo")
	assert os.stat(path).st_mode & 0o777 == 0o640


# -- lock screen stack ------------------------------------------------------
def test_kde_face_from_arch_fingerprint_stack(tmp_path):
	root = tree("arch", tmp_path)
	PamIntegration(root).enable("lock-screen")
	text = read(root, "etc/pam.d/kde-face")
	auth = [line for line in text.splitlines() if line.lstrip("-").startswith("auth")]

	assert text.startswith(pam.OWN_HEADER)
	assert pam.OWN_STACK_LINE in auth
	# Nothing in the stack may grant access without a face match
	assert not any("pam_permit.so" in line or "pam_fprintd.so" in line for line in auth)
	# Protective checks stay, and run before the face scan
	assert any("pam_faillock.so" in line for line in auth)
	assert auth.index(pam.OWN_STACK_LINE) > [i for i, line in enumerate(auth) if "pam_nologin.so" in line][0]
	# The interactive stack is left alone
	assert not os.path.exists(os.path.join(root, "etc/pam.d/kde"))


def test_kde_face_from_fedora_substack(tmp_path):
	root = tree("fedora", tmp_path)
	PamIntegration(root).enable("lock-screen")
	text = read(root, "etc/pam.d/kde-face")
	auth = [line for line in text.splitlines() if line.startswith("auth")]
	# Includes could pull in password modules: only our line remains
	assert auth == [pam.OWN_STACK_LINE]
	assert "account    include      fingerprint-auth" in text
	assert "password   required     pam_deny.so" in text


def test_own_stack_denies_on_ignore():
	assert "default=die" in pam.OWN_STACK_LINE


def test_foreign_kde_face_is_not_overwritten(tmp_path):
	root = tree("arch", tmp_path)
	path = os.path.join(root, "etc/pam.d/kde-face")
	with open(path, "w") as file:
		file.write("#%PAM-1.0\nauth required pam_someone_else.so\n")
	with pytest.raises(PamError):
		PamIntegration(root).enable("lock-screen")
	assert "pam_someone_else.so" in read(root, "etc/pam.d/kde-face")


# -- reset and recovery -----------------------------------------------------
def test_reset_without_manifest_deletes_own_stack(tmp_path):
	root = tree("arch", tmp_path)
	integration = PamIntegration(root)
	integration.enable("lock-screen")
	integration.enable("sudo")
	os.unlink(os.path.join(root, pam.STATE_FILE))

	integration.reset()
	# Stripping our line out of kde-face would leave a stack that succeeds
	# without authentication, so the whole file must go
	assert not os.path.exists(os.path.join(root, "etc/pam.d/kde-face"))
	assert pam.MODULE not in read(root, "etc/pam.d/sudo")


def test_reset_removes_legacy_lines_and_backups(tmp_path):
	root = tree("arch", tmp_path)
	path = os.path.join(root, "etc/pam.d/sudo")
	original = read(root, "etc/pam.d/sudo")
	with open(path + ".linux-hello-backup", "w") as file:
		file.write(original)
	lines = original.splitlines(keepends=True)
	with open(path, "w") as file:
		file.write(lines[0] + "auth\tsufficient\tpam_linux_hello.so\n" + "".join(lines[1:]))

	changed = PamIntegration(root).reset()
	assert read(root, "etc/pam.d/sudo") == original
	assert not os.path.exists(path + ".linux-hello-backup")
	assert path in changed


def test_reset_keeps_commented_references(tmp_path):
	root = tree("arch", tmp_path)
	path = os.path.join(root, "etc/pam.d/sudo")
	with open(path, "a") as file:
		file.write("# auth sufficient pam_linux_hello.so\n")
	before = read(root, "etc/pam.d/sudo")
	PamIntegration(root).reset()
	assert read(root, "etc/pam.d/sudo") == before


def test_unavailable_target_raises(tmp_path):
	root = tree("arch", tmp_path)
	os.unlink(os.path.join(root, "usr/lib/pam.d/plasmalogin"))
	integration = PamIntegration(root)
	assert not {t["id"]: t["available"] for t in integration.targets()}["login"]
	with pytest.raises(PamError):
		integration.enable("login")


def test_state_file_is_private(tmp_path):
	root = tree("arch", tmp_path)
	PamIntegration(root).enable("sudo")
	assert os.stat(os.path.join(root, pam.STATE_FILE)).st_mode & 0o777 == 0o600


# -- kscreenlocker configuration -------------------------------------------------
def test_lock_screen_enables_face_in_kscreenlocker(tmp_path):
	root = tree("arch", tmp_path)
	integration = PamIntegration(root)
	integration.enable("lock-screen")

	text = read(root, pam.KSCREENLOCKER_CONFIG)
	assert "[Authenticators]\nFace=true\n" in text
	assert {t["id"]: t["enabled"] for t in integration.targets()}["lock-screen"]

	# Without the setting the lock screen never offers face unlock
	os.unlink(os.path.join(root, pam.KSCREENLOCKER_CONFIG))
	assert not {t["id"]: t["enabled"] for t in integration.targets()}["lock-screen"]


@pytest.mark.parametrize("existing", [
	"[Greeter]\nTheme=breeze\n",
	"[Greeter]\nTheme=breeze",
	"[Greeter]\nTheme=breeze\n\n",
])
def test_existing_kscreenlocker_config_round_trip(tmp_path, existing):
	root = tree("arch", tmp_path)
	path = os.path.join(root, pam.KSCREENLOCKER_CONFIG)
	os.makedirs(os.path.dirname(path))
	with open(path, "w") as file:
		file.write(existing)

	integration = PamIntegration(root)
	integration.enable("lock-screen")
	text = read(root, pam.KSCREENLOCKER_CONFIG)
	assert text.startswith("[Greeter]\nTheme=breeze")
	assert text.endswith("[Authenticators]\nFace=true\n" + pam.END + "\n")

	integration.disable("lock-screen")
	assert read(root, pam.KSCREENLOCKER_CONFIG) == existing


def test_created_config_keeps_admin_additions(tmp_path):
	root = tree("arch", tmp_path)
	integration = PamIntegration(root)
	integration.enable("lock-screen")
	with open(os.path.join(root, pam.KSCREENLOCKER_CONFIG), "a") as file:
		file.write("[Daemon]\nTimeout=10\n")

	integration.disable("lock-screen")
	assert read(root, pam.KSCREENLOCKER_CONFIG) == "[Daemon]\nTimeout=10\n"


def test_old_plasma_does_not_touch_kscreenlocker(tmp_path):
	root = tree("debian", tmp_path)
	PamIntegration(root).enable("lock-screen")
	assert not os.path.exists(os.path.join(root, pam.KSCREENLOCKER_CONFIG))


def test_reset_without_manifest_removes_config_block(tmp_path):
	root = tree("arch", tmp_path)
	path = os.path.join(root, pam.KSCREENLOCKER_CONFIG)
	os.makedirs(os.path.dirname(path))
	with open(path, "w") as file:
		file.write("[Greeter]\nTheme=breeze\n")
	integration = PamIntegration(root)
	integration.enable("lock-screen")
	os.unlink(os.path.join(root, pam.STATE_FILE))

	integration.reset()
	assert pam.BEGIN not in read(root, pam.KSCREENLOCKER_CONFIG)
	assert read(root, pam.KSCREENLOCKER_CONFIG).startswith("[Greeter]\nTheme=breeze\n")
