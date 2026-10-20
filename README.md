# Linux Hello

**Windows Hello-style face authentication for Linux, built for KDE Plasma.**

> ⚠️ **Work in progress.** Linux Hello is under active development and not yet
> ready for daily use. Expect breaking changes, missing features and rough
> edges.

Linux Hello unlocks your screen, authenticates sudo and polkit prompts with
your face, no terminal configuration required. It is a fork of
[Howdy](https://github.com/boltgolt/howdy) by boltgolt, rebuilt as a
zero-config alternative: install it, open Face Unlock in System Settings,
add your face, done.

## Features

- **Zero configuration**: cameras are auto-detected (IR first), the darkness
  threshold adapts to your lighting automatically, and sane defaults are used
  everywhere.
- **System Settings page**: add your face, test it and choose where face
  unlock is used, without touching a terminal.
- **PAM integration**: face authentication for the Plasma lock screen, login,
  sudo and polkit, each turned on separately.
- **Pluggable recognition backends**: dlib on CPU, optional Intel OpenVINO
  acceleration on iGPU, behind a common interface.
- **WebAuthn (experimental)**: a virtual FIDO2 platform authenticator that
  lets your face unlock passkey logins in the browser.
- **GPL-3.0 licensed**, forever free and open source.

## Status

| Area | State |
|---|---|
| PAM face authentication | Working (requires dlib + models) |
| Camera auto-detection | Working |
| System Settings page | Working |
| Plasma lock screen | Working on Plasma 6.8+ |
| WebAuthn authenticator | Experimental, off by default |
| Packaging | Debian + Arch (unofficial), untested on real systems |

Python dependencies (OpenCV, dlib) can be shipped inside a
self-contained runtime, see [Building from source](#building-from-source).
There are no release tarballs yet, so for now this repository is for
development, testing and review.

## Building from source

There are two ways to build. The **bundled runtime** ships its own CPython plus
every Python dependency; the **system Python** build uses your distro's
interpreter and packages. Both produce the same PAM module, service and CLI.
The System Settings page is a separate CMake build in `kcm/`.

### 1. Build dependencies

All builds need: a C++20 compiler, `meson` (>= 0.60), `ninja`, `pkg-config`,
`gettext` (translations) and the headers for `libpam`, `libevdev`,
`libsystemd` (sd-bus) and `inih`/`INIReader`.

The bundled runtime additionally needs `curl` (or `wget`), `tar`, `python3`,
`cmake` and network access: dlib is compiled from source against the bundled
interpreter (no CUDA, no GUI layer), and every download is SHA256-pinned in
`tools/runtime-manifest.json`.

The System Settings page needs `cmake`, `extra-cmake-modules`, Qt 6 and
KDE Frameworks 6 (KCMUtils, Kirigami, I18n, Config, CoreAddons).

Arch / CachyOS:

```sh
sudo pacman -S --needed base-devel meson ninja gettext pkgconf \
	libevdev libinih systemd cmake curl ccache \
	extra-cmake-modules kcmutils kirigami ki18n kconfig kcoreaddons
```

Debian / Ubuntu:

```sh
sudo apt install build-essential meson ninja-build gettext pkg-config \
	libpam0g-dev libevdev-dev libinih-dev libsystemd-dev cmake curl python3 \
	extra-cmake-modules qt6-declarative-dev libkf6kcmutils-dev \
	libkf6i18n-dev libkf6config-dev libkf6coreaddons-dev
```

Fedora:

```sh
sudo dnf install gcc-c++ meson ninja-build gettext pkgconf-pkg-config \
	pam-devel libevdev-devel inih-devel systemd-devel cmake curl python3 \
	extra-cmake-modules qt6-qtdeclarative-devel kf6-kcmutils-devel \
	kf6-ki18n-devel kf6-kconfig-devel kf6-kcoreaddons-devel
```

### 2. Runtime dependencies

**Bundled runtime: none from your repositories.** No `python3-opencv`, no
`python3-dlib`, no pip. The cost is ~300 MB of installed size and immunity to
interpreter upgrades on the target machine. The System Settings page needs
Kirigami and KCMUtils, which Plasma already ships.

**System Python build** needs distro packages instead:

```sh
# Debian / Ubuntu
sudo apt install python3-opencv python3-dlib python3-numpy \
	python3-dbus-fast python3-fido2 python3-cryptography
# Arch / CachyOS
sudo pacman -S --needed python-opencv python-dlib python-numpy \
	python-dbus-fast python-fido2 python-cryptography
```

`python3-fido2` and `python3-cryptography` are only needed for WebAuthn.

### 3. Configure, compile, install

Bundled runtime (recommended):

```sh
tools/build_runtime.sh --outdir build-runtime
meson setup build -Dbundled_python_dir="$(pwd)/build-runtime/linux-hello-runtime"
ninja -C build
sudo ninja -C build install
```

System Python:

```sh
meson setup build
ninja -C build
sudo ninja -C build install
```

Then the System Settings page:

```sh
cmake -B build-kcm -S kcm -DCMAKE_INSTALL_PREFIX=/usr
cmake --build build-kcm
sudo cmake --install build-kcm
```

Other build options (see `meson.options` for the full list):

```sh
meson configure build -Dwith_webauthn=false
```

Handy follow-ups:

```sh
ninja -C build && sudo ninja -C build install   # rebuild after editing
sudo tools/uninstall.sh                         # undo PAM changes, remove
```

After installing, open **System Settings > Security & Privacy > Face Unlock**
(or `linux-hello-cli` in a terminal), add your face and choose where to use
it.

## Hardware notes

- **ASUS laptops with Sonix IR cameras (USB ID `3277:0018`)**: the IR emitter
  is controlled by the laptop's proximity sensor, not a UVC control. The feed
  looks black until a person approaches. Do **not** run
  `linux-enable-ir-emitter configure` on these; it can wedge the USB bus and
  require a hard reboot (boltgolt/howdy#1109). The IR LED sits in the RGB
  camera cutout, so a privacy shutter can dim the IR feed.
- **ThinkPad X1 Nano**: IR camera reports Y800 grayscale at 640x360 on
  `/dev/video2`; auto dark-threshold detection handles the high baseline
  (boltgolt/howdy#1113).
- **ThinkPad X1 Yoga / Carbon (Chicony)**: if the emitter never lights up,
  the `chicony-ir-toggle` tool or `linux-enable-ir-emitter` can enable it
  persistently.
- **SELinux-enforcing systems (Fedora)**: if face unlock fails, check the
  audit log for denials of `linux-hello-daemon` on `/dev/video*`.
- **Hardware camera kill switches** are detected as a missing camera and fail
  gracefully back to password auth.

## Credits

Linux Hello is a fork of [Howdy](https://github.com/boltgolt/howdy) by
boltgolt (MIT-licensed; see [NOTICE](NOTICE)).

- [PR #1088](https://github.com/boltgolt/howdy/pull/1088) by **haleelrah**:
  recog/ backend abstraction and PAM hardening
- [PR #1125](https://github.com/boltgolt/howdy/pull/1125) by **qilsklo**:
  WebAuthn virtual FIDO2 authenticator

## License

GPL-3.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
