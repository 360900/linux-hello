import os
from types import SimpleNamespace

from integration import encryption

MOUNTS = """\
22 1 0:23 /@ / rw - btrfs /dev/plain rw
23 22 0:23 /@home /home rw - btrfs /dev/plain rw
24 22 0:24 / /home/alice rw - ext4 /dev/mapper/home rw
25 22 0:25 / /mnt/with\\040space rw - ext4 /dev/vg-data rw
26 22 0:26 / /run tmpfs - tmpfs tmpfs rw
"""

# /dev node -> sysfs device number
NUMBERS = {"/dev/plain": (259, 2), "/dev/mapper/home": (254, 0), "/dev/vg-data": (254, 1)}


def fake_system(tmp_path, data_on_crypt):
	tmp_path.mkdir(parents=True, exist_ok=True)
	mountinfo = tmp_path / "mountinfo"
	mountinfo.write_text(MOUNTS)
	block = tmp_path / "block"

	def device(name, uuid=None, slaves=()):
		path = block / name
		path.mkdir(parents=True)
		if uuid is not None:
			(path / "dm").mkdir()
			(path / "dm" / "uuid").write_text(uuid + "\n")
		if slaves:
			(path / "slaves").mkdir()
			for slave in slaves:
				(path / "slaves" / slave).symlink_to(block / slave)
		return path

	device("259:2")
	device("259:3")
	device("254:2", uuid="CRYPT-LUKS2-0123-root")
	device("254:0", uuid="CRYPT-LUKS2-4567-home")
	# LVM volume on LUKS, or on a plain partition
	device("254:1", uuid="LVM-abc", slaves=["254:2" if data_on_crypt else "259:3"])

	def stat(path):
		major, minor = NUMBERS[path]
		return SimpleNamespace(st_rdev=os.makedev(major, minor))

	return lambda path: encryption.is_encrypted(path, str(mountinfo), str(block), stat)


def test_plain_partition(tmp_path):
	encrypted = fake_system(tmp_path, data_on_crypt=True)
	assert not encrypted("/home/bob")


def test_luks_mount_inside_a_plain_one(tmp_path):
	# The longest mount point wins
	encrypted = fake_system(tmp_path, data_on_crypt=True)
	assert encrypted("/home/alice/Documents")
	assert not encrypted("/home/alicex")


def test_lvm_counts_only_on_top_of_luks(tmp_path):
	assert fake_system(tmp_path / "a", data_on_crypt=True)("/mnt/with space/x")
	assert not fake_system(tmp_path / "b", data_on_crypt=False)("/mnt/with space/x")


def test_not_a_device(tmp_path):
	encrypted = fake_system(tmp_path, data_on_crypt=True)
	assert not encrypted("/run/user")
