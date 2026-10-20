# Linux Hello

Face unlock for KDE Plasma, sudo and polkit.

- Unlocks the Plasma lock screen, approves polkit prompts and sudo with your face.
- Prefers the infrared camera and switches its IR light on by itself.
- Set up in System Settings. No terminal, no config files.
- Your password always keeps working.

## Requirements

- KDE Plasma 6. Face unlock on the lock screen needs Plasma 6.8 or later.
- A camera. An infrared (Windows Hello) camera works in the dark and is much
  harder to fool.

## Install

There are no release packages yet. Build one from this repository:

```sh
# Arch, CachyOS
cd packaging/arch && makepkg -si
```

Fedora (`packaging/fedora/linux-hello.spec`) and Debian/Kubuntu (`debian/`)
packages build the same way; see `.github/workflows/check.yml` for the steps.

Then open **System Settings > Security & Privacy > Face Unlock**.

## Uninstall

Remove the package. Linux Hello undoes its PAM changes before it goes. Face
models stay in `/var/lib/linux-hello` until you delete them.

## Command line

```sh
linux-hello-cli status        # what is set up
linux-hello-cli add           # add your face
linux-hello-cli enable sudo   # lock-screen, admin, sudo, login
linux-hello-cli doctor        # find problems
```

## Build from source

Needs a C++20 compiler, `meson`, `ninja`, `cmake`, `extra-cmake-modules`, Qt 6,
KDE Frameworks 6 (KCMUtils, Kirigami, I18n, Config, CoreAddons) and the
headers for `pam`, `libevdev`, `libsystemd` and `inih`.

```sh
tools/build_runtime.sh --outdir build-runtime
meson setup build -Dbundled_python_dir="$PWD/build-runtime/linux-hello-runtime"
ninja -C build && sudo ninja -C build install

cmake -B build-kcm -S kcm -DCMAKE_INSTALL_PREFIX=/usr
cmake --build build-kcm && sudo cmake --install build-kcm
```

`build_runtime.sh` downloads a Python runtime with OpenCV and dlib, pinned in
`tools/runtime-manifest.json`. Without `-Dbundled_python_dir`, the system Python
is used and needs OpenCV, dlib, numpy and dbus-fast.

Remove a source install with `sudo tools/uninstall.sh`.

## Hardware notes

- **IR light stays off**: run `sudo linux-hello-cli emitter search`.
- **ASUS laptops with Sonix IR cameras (`3277:0018`)**: the IR light follows
  the proximity sensor. Do not run `emitter search` or
  `linux-enable-ir-emitter` on these; it can hang the USB bus
  (boltgolt/howdy#1109).
- **SELinux (Fedora)**: if the camera fails, check the audit log for
  `linux-hello-daemon`.

## Credits

Remake of [Howdy](https://github.com/boltgolt/howdy) by boltgolt, MIT licensed;
see [NOTICE](NOTICE). Includes
[#1088](https://github.com/boltgolt/howdy/pull/1088) by haleelrah (recognition
backends) and [#1125](https://github.com/boltgolt/howdy/pull/1125) by qilsklo
(WebAuthn). IR light control follows
[linux-enable-ir-emitter](https://github.com/EmixamPP/linux-enable-ir-emitter).

## License

GPL-3.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
