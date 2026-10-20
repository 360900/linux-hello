# Security

## Reporting

Report vulnerabilities privately through
[GitHub security advisories](https://github.com/360900/linux-hello/security/advisories/new),
not in public issues. Only the latest version is supported.

## Threat model

Linux Hello keeps these true:

- Your password always works. A missing, stopped or broken Linux Hello falls
  through to it.
- Programs running as you cannot use your face to get root or approve admin
  prompts or passkey sign-ins. These scan only after Enter on a physical
  keyboard, one request at a time, asked from your own local session.
- Other users and remote sessions cannot use your face, read your face models
  or see your camera.
- A face match does not skip lockout checks (faillock, nologin, access).
- Face models and snapshots stay on this computer, readable by root only.

Not covered:

- X11. Any program there can read your keys.
- A program running as you that starts a request right before you press Enter
  alone somewhere else. Repeated tries pause face for up to an hour.
- Programs running as you that get your password another way, such as a
  `sudo` alias or a fake password dialog.
- Root, or anyone who can write to `/etc` or `/var/lib/linux-hello`.
- Photos or masks in front of a regular (RGB) camera. Use an infrared camera.
