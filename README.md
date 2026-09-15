# Linux Hello

**Windows Hello-style face authentication for Linux, built for KDE Plasma.**

> ⚠️ **Work in progress.** Linux Hello is under active development and not yet
> ready for daily use. Expect breaking changes, missing features and rough
> edges.

Linux Hello unlocks your screen, authenticates sudo and polkit prompts with
your face, no terminal configuration required. It is a fork of
[Howdy](https://github.com/boltgolt/howdy) by boltgolt, rebuilt as a
zero-config alternative: install it, open the GUI, enroll your face, done.

## Features

- **Zero configuration**: cameras are auto-detected (IR first), the darkness
  threshold adapts to your lighting automatically, and sane defaults are used
  everywhere.
- **"Linux Hello" GUI app**: a Qt desktop app that looks and feels native on
  KDE Plasma. Setup wizard, model management, settings and a live camera test,
  all without touching a terminal.
- **PAM integration**: face authentication for KDE lock screen, login, sudo,
  su and polkit. Toggle per-service from the GUI.
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
| Qt GUI (setup, settings, models, test) | Working, UI polish ongoing |
| KDE integration | PAM services wired; lock-screen UI integration planned |
| WebAuthn authenticator | Experimental, off by default |
| Packaging | Debian + Arch (unofficial), untested on real systems |

Python dependencies (OpenCV, dlib, PySide6) can be shipped inside a
self-contained runtime, see [Building from source](#building-from-source).
There are no release tarballs yet, so for now this repository is for
development, testing and review.

## Building from source

There are two ways to build. The **bundled runtime** ships its own CPython plus
every Python dependency; the **system Python** build uses your distro's
interpreter and packages. Both produce the same PAM module, CLI and GUI.

### 1. Build dependencies

All builds need: a C++20 compiler, `meson` (>= 0.60), `ninja`, `pkg-config`,
`gettext` (translations) and the headers for `libpam`, `libevdev` and
`inih`/`INIReader`.

The bundled runtime additionally needs `curl` (or `wget`), `tar`, `python3`,
`cmake` and network access: dlib is compiled from source against the bundled
interpreter (no CUDA, no GUI layer), and every download is SHA256-pinned in
`tools/runtime-manifest.json`.

Arch / CachyOS:

```sh
sudo pacman -S --needed base-devel meson ninja gettext pkgconf \
	libevdev libinih cmake curl ccache
```

Debian / Ubuntu:

```sh
sudo apt install build-essential meson ninja-build gettext pkg-config \
	libpam0g-dev libevdev-dev libinih-dev cmake curl python3
```

Fedora:

```sh
sudo dnf install gcc-c++ meson ninja-build gettext pkgconf-pkg-config \
	pam-devel libevdev-devel inih-devel cmake curl python3
```

### 2. Runtime dependencies

**Bundled runtime: none from your repositories.** No `python3-opencv`, no
`python3-dlib`, no `python3-pyside6`, no pip. The cost is ~400 MB of installed
size and immunity to interpreter upgrades on the target machine. The GUI still
uses the system X11/Wayland client libraries, which any desktop install already
has (`libxcb`, `libxcb-cursor`, `libxkbcommon`, `libwayland-client`, `mesa`).

**System Python build** needs distro packages instead:

```sh
# Debian / Ubuntu
sudo apt install python3-opencv python3-dlib python3-numpy \
	python3-pyside6 python3-fido2 python3-cryptography
# Arch / CachyOS
sudo pacman -S --needed python-opencv python-dlib python-numpy \
	python-pyside6 python-fido2 python-cryptography
```

`python3-pyside6` is only needed for the GUI, `python3-fido2` +
`python3-cryptography` only for WebAuthn.

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

Other build options (see `meson.options` for the full list):

```sh
meson configure build -Dwith_webauthn=false -Dinstall_config=false
```

Handy follow-ups:

```sh
ninja -C build && sudo ninja -C build install   # rebuild after editing
sudo ninja -C build uninstall                   # remove again
sudo systemctl daemon-reload                    # see the Polkit note below
```

After installing, run the GUI app **Linux Hello** (or `linux-hello-cli` from a
terminal) to enroll your face, then enable the PAM services you want from the
settings page.

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
- **SELinux-enforcing systems (Fedora)**: the display manager domain
  (`xdm_t`) needs `map` permission on `/dev/video*` or camera open fails only
  in GDM/lock-screen contexts (boltgolt/howdy#1117).
- **Polkit >= 127**: the agent helper is sandboxed without device access; we
  ship a systemd drop-in that re-enables camera access. If prompts still
  fail, make sure the drop-in
  `/usr/lib/systemd/system/polkit-agent-helper@.service.d/10-linux-hello.conf`
  is installed and run `systemctl daemon-reload`.
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
