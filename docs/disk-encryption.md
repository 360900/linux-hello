# Encrypt your disk (LUKS + TPM + Secure Boot)

Without disk encryption, anyone who has your computer can boot it from a USB
stick and read or change every file, whatever the login screen asks for.
With it, the disk only opens on your computer, running your system.

This setup works like BitLocker on Windows:

- **LUKS** encrypts the disk.
- **The TPM** (a chip in your computer) unlocks it at boot, so you type no
  extra password.
- **Secure Boot** makes sure the TPM only does that for your own bootloader
  and kernel, not for a USB stick.

Linux Hello then logs you in at boot behind the lock screen (Log in
automatically), and the lock screen looks for your face.

## The easy way: reinstall

Every major installer can encrypt the disk. Back up, reinstall, and tick
**Encrypt system** (CachyOS, Arch with archinstall, Fedora, Kubuntu). Then
continue with [Secure Boot](#1-secure-boot) and [TPM unlock](#2-unlock-with-the-tpm).

## Without reinstalling

LUKS can encrypt a partition in place. It rewrites the whole partition, so
expect an hour or more.

**Before you start:**

- Back up everything. If power fails halfway, the backup is what saves you.
- Plug in the charger.
- Make a live USB of your distribution.
- Note your partitions with `lsblk -f`. Below, `/dev/nvme0n1p2` is the root
  partition and `/boot` is a separate EFI partition. Change the names to
  yours.

**Boot the live USB, then:**

1. Make room for the LUKS header by shrinking the filesystem by 32 MiB:

   ```sh
   # btrfs: shrink while mounted
   mount /dev/nvme0n1p2 /mnt
   btrfs filesystem resize -32M /mnt
   umount /mnt

   # ext4: check, then shrink to 32 MiB less than its size
   e2fsck -f /dev/nvme0n1p2
   resize2fs /dev/nvme0n1p2 <new size>
   ```

2. Encrypt in place. Pick a strong passphrase; it stays as a fallback:

   ```sh
   cryptsetup reencrypt --encrypt --reduce-device-size 32M /dev/nvme0n1p2
   ```

3. Open it and enter your system:

   ```sh
   cryptsetup open /dev/nvme0n1p2 root
   mount -o subvol=@ /dev/mapper/root /mnt   # btrfs; ext4: no -o subvol=@
   mount /dev/nvme0n1p1 /mnt/boot
   arch-chroot /mnt                          # Fedora, Kubuntu: chroot /mnt
   ```

4. Tell the boot process about it:
   - **Kernel command line**, in your bootloader's config (Limine:
     `KERNEL_CMDLINE[default]` in `/etc/default/limine` if it exists, as on
     CachyOS, else `/boot/limine.conf`; systemd-boot: `/etc/kernel/cmdline`, GRUB:
     `GRUB_CMDLINE_LINUX` in `/etc/default/grub`). Add
     `rd.luks.name=<UUID>=root` and set `root=/dev/mapper/root`.
     `<UUID>` is what `cryptsetup luksUUID /dev/nvme0n1p2` prints.
   - **Arch, CachyOS:** in `/etc/mkinitcpio.conf`, `HOOKS` must contain
     `systemd` and `sd-encrypt`, not `udev` and `encrypt`. Then run
     `limine-mkinitcpio` if you have it (CachyOS), else `mkinitcpio -P`.
   - **Fedora:** `dracut -f --regenerate-all`.
   - **Kubuntu:** add `root UUID=<UUID> none luks` to `/etc/crypttab`, then
     `update-initramfs -u -k all`.
   - Leave `/etc/fstab` as it is. The filesystem inside keeps its UUID.

5. Reboot. It asks for the passphrase. If it doesn't boot, start the live
   USB again, repeat step 3 and check step 4.

## 1. Secure Boot

Secure Boot with your own keys, using `sbctl`:

1. In the firmware settings, set Secure Boot to **Setup Mode** (often "Reset
   to Setup Mode" or "Clear keys").
2. Boot, then:

   ```sh
   sudo sbctl status          # Setup Mode: Enabled
   sudo sbctl create-keys
   sudo sbctl enroll-keys -m  # -m keeps Microsoft's keys, which some hardware needs
   sudo sbctl verify          # lists what needs signing
   sudo sbctl sign -s <each file sbctl verify lists>
   ```

3. **Limine** checks the kernel itself, not through Secure Boot. Put a
   `#<blake2b>` hash after every kernel and initramfs path, and enroll the
   config checksum (`limine enroll-config`), or anyone can boot a different
   kernel. Also set `editor_enabled: no`, so the boot menu can't change the
   kernel command line. CachyOS's `limine-entry-tool` and `limine-mkinitcpio-hook` can do
   this for you; see their docs. With systemd-boot or GRUB, the signed files
   are enough.
4. Turn Secure Boot on in the firmware and boot. `sbctl status` should say
   Secure Boot: Enabled.

`sbctl` signs new kernels by itself after updates, through a package hook.

## 2. Unlock with the TPM

1. Check that there is a TPM: `systemd-cryptenroll --tpm2-device=list`. If
   it says none, turn it on in the firmware (ThinkPads: Security > Security
   Chip).
2. Make a recovery key, and keep it somewhere other than this computer:

   ```sh
   sudo systemd-cryptenroll --recovery-key /dev/nvme0n1p2
   ```

3. Let the TPM unlock the disk while Secure Boot is on with your keys:

   ```sh
   sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/nvme0n1p2
   ```

4. Add `rd.luks.options=tpm2-device=auto` to the kernel command line (or
   `tpm2-device=auto` to the `/etc/crypttab` line), rebuild the initramfs as
   in step 4 above, and reboot. The disk opens without the passphrase.

   Kubuntu before 25.10 builds the initramfs with initramfs-tools, which
   can't use the TPM. Install `dracut` first, then rebuild with
   `sudo dracut -f --regenerate-all`.

If you turn Secure Boot off or change its keys, the TPM stops unlocking and
asks for the passphrase or the recovery key. That is how it keeps a USB
stick out.

## Then

In **System Settings > Security & Privacy > Face Unlock**, turn on **Log in
automatically**, then log out, wait a few seconds, and log in with your
password once. From then on your computer boots straight to the lock screen
with KWallet open, and the lock screen looks for your face. Fingerprint and
password work there too.

The disk now opens by itself on your computer, so the lock screen is what
protects your session, as on Windows. For more, add a PIN to the TPM unlock
(`--tpm2-with-pin=yes`), which you then type at every boot.

## More

- [Arch Wiki: dm-crypt](https://wiki.archlinux.org/title/Dm-crypt)
- [Arch Wiki: Secure Boot](https://wiki.archlinux.org/title/Unified_Extensible_Firmware_Interface/Secure_Boot)
- [systemd-cryptenroll](https://www.freedesktop.org/software/systemd/man/latest/systemd-cryptenroll.html)
- [Limine](https://github.com/limine-bootloader/limine)
