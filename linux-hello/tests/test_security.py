from integration import security

GLOBAL = security.GLOBAL


def efivars(tmp_path, **flags):
	directory = tmp_path / "efivars"
	directory.mkdir(parents=True)
	for name, value in flags.items():
		# 4 attribute bytes, then the value
		(directory / "{}-{}".format(name, GLOBAL)).write_bytes(b"\x06\x00\x00\x00" + bytes([value]))
	return str(directory)


def test_secure_boot_states(tmp_path):
	assert security.secure_boot(efivars(tmp_path / "a", SecureBoot=1, SetupMode=0)) == "on"
	assert security.secure_boot(efivars(tmp_path / "b", SecureBoot=0, SetupMode=0)) == "off"
	assert security.secure_boot(efivars(tmp_path / "c", SecureBoot=0, SetupMode=1)) == "setup"
	assert security.secure_boot(efivars(tmp_path / "d")) == "unknown"
	assert security.secure_boot(str(tmp_path / "none")) == "legacy"




def test_tpm_version(tmp_path):
	assert security.tpm_version(str(tmp_path / "none")) == 0
	sys_tpm = tmp_path / "tpm"
	(sys_tpm / "tpm0").mkdir(parents=True)
	(sys_tpm / "tpm0" / "tpm_version_major").write_text("2\n")
	assert security.tpm_version(str(sys_tpm)) == 2


def test_tpm_unlock(tmp_path):
	cmdline = tmp_path / "cmdline"
	crypttab = tmp_path / "crypttab"
	cmdline.write_text("root=/dev/mapper/root rd.luks.name=abc=root\n")
	crypttab.write_text("# home UUID=x none tpm2-device=auto\n")
	assert not security.tpm_unlock(str(cmdline), [str(crypttab)])
	crypttab.write_text("home UUID=x none tpm2-device=auto\n")
	assert security.tpm_unlock(str(cmdline), [str(crypttab)])
	cmdline.write_text("rd.luks.options=tpm2-device=auto\n")
	assert security.tpm_unlock(str(cmdline), [])
