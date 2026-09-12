# Linux Hello

**Windows Hello-style face authentication for Linux, built for KDE Plasma.**

> ⚠️ **Work in progress.** Linux Hello is under active development and not yet
> ready for daily use. Expect breaking changes, missing features and rough
> edges.

Linux Hello unlocks your screen, authenticates sudo and polkit prompts with
your face — no terminal configuration required. It is a fork of
[Howdy](https://github.com/boltgolt/howdy) by boltgolt, rebuilt as a
zero-config alternative: install it, open the GUI, enroll your face, done.

## Features

- **Zero configuration** — cameras are auto-detected (IR first), the darkness
  threshold adapts to your lighting automatically, and sane defaults are used
  everywhere.
- **"Linux Hello" GUI app** — a Qt desktop app that looks and feels native on
  KDE Plasma. Setup wizard, model management, settings and a live camera test,
  all without touching a terminal.
- **PAM integration** — face authentication for KDE lock screen, login, sudo,
  su and polkit. Toggle per-service from the GUI.
- **Pluggable recognition backends** — dlib on CPU, optional Intel OpenVINO
  acceleration on iGPU, behind a common interface.
- **WebAuthn (experimental)** — a virtual FIDO2 platform authenticator that
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

Runtime dependencies (dlib, OpenCV, PySide6) are not yet bundled or packaged;
there are no release tarballs yet. For now this repository is for development,
testing and review.

## Building from source

Requirements: `meson`, `ninja`, a C++ compiler, `python3` with
`python3-opencv`, `dlib` (`python3-dlib`), and `python3-pyside6` for the GUI.

```sh
meson setup build
ninja -C build
sudo ninja -C build install
```

Useful build options (see `meson.options` for the full list):

```sh
meson configure build -Dwith_webauthn=false -Dinstall_config=false
```

After installing, run the GUI app **Linux Hello** (or `linux-hello-cli` from a
terminal) to enroll your face, then enable the PAM services you want from the
settings page.

## Credits

Linux Hello is a fork of [Howdy](https://github.com/boltgolt/howdy) by
boltgolt (MIT-licensed; see [NOTICE](NOTICE)).

- [PR #1088](https://github.com/boltgolt/howdy/pull/1088) by **haleelrah** —
  recog/ backend abstraction and PAM hardening
- [PR #1125](https://github.com/boltgolt/howdy/pull/1125) by **qilsklo** —
  WebAuthn virtual FIDO2 authenticator

## License

GPL-3.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
