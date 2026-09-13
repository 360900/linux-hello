# Linux Hello WebAuthn

> Based on the WebAuthn work in [boltgolt/howdy PR #1125](https://github.com/boltgolt/howdy/pull/1125) by **qilsklo**.

> **Experimental.** This is an optional, off-by-default feature that turns
Linux Hello into a **software platform authenticator** for WebAuthn / passkeys:
your face becomes the user verification gesture for passkey logins.
> It lets your browser create and use passkeys that are unlocked by your
> face instead of a PIN or a hardware security key.
>
> It is **not** a certified FIDO authenticator and is **not** equivalent to
> Windows Hello, Apple's Secure Enclave, or a YubiKey. Read the
> [Security model](#security-model) before relying on it. Enabling it does
> **not** change your PAM / login / sudo behaviour in any way.

For the design rationale and threat model in depth, see
[`webauthn-design.md`](webauthn-design.md). This document is the operator's
guide: how to install, configure, use, and reason about the feature.

---

## What it does

```
Browser  ──USB/HID──▶  Linux Hello virtual FIDO2 device  ──▶  face verification  ──▶  passkey signs the challenge
(unmodified)          (/dev/uhid, looks like a           (compare.py, the
                       plugged-in security key)           exact same check
                                                          PAM uses)
```

A small root daemon (`linux-hello-webauthn`) creates a **virtual FIDO2 HID
device** using the kernel's `uhid` interface. To Firefox and Chromium it
looks exactly like a plugged-in security key, so **no browser extension,
patch, or native-messaging host is required**. When a website asks for a
passkey, the browser talks CTAP2 to the virtual device; the daemon verifies
your face with the same `compare.py` path the PAM module uses, and only then
signs the challenge.

Because it presents as a roaming/USB authenticator rather than an internal
one, the browser UI will say something like "Insert your security key" and
"Use your security key" rather than "Use Windows Hello". That is expected;
see [Limitations](#limitations).

---

## Requirements

- A working Linux Hello face setup: you must be able to run `sudo linux-hello-cli test`
  and have it recognise you. If Linux Hello can't see your face, WebAuthn can't
  either.
- Linux kernel with the `uhid` module (standard on Arch and most distros).
- **Firefox ≥ 114** or a **Chromium**-based browser (Chrome, Chromium,
  Edge, Brave). Both support USB CTAP2 authenticators out of the box.
- The `python-fido2` Python package.
- Optional but recommended: a **TPM 2.0** and `python-tpm2-pytss` for
  hardware-backed key storage.

### Arch Linux packages

```sh
# Required for the WebAuthn feature
sudo pacman -S python-fido2

# Optional: hardware-backed keystore (TPM 2.0)
sudo pacman -S tpm2-tss python-tpm2-pytss
```

`python-fido2` (2.2.1), `tpm2-tss` (4.1.3) and `python-tpm2-pytss` (2.3.0)
are all in the Arch repositories.

---

## Installation

The feature ships with Linux Hello's source but is **not built or installed
unless you ask for it**. Configure Meson with `-Dwith_webauthn=true`:

```sh
meson setup build -Dwith_webauthn=true
meson compile -C build
sudo meson install -C build
```

This installs, in addition to Linux Hello itself:

| File | Purpose |
|------|---------|
| `/usr/bin/linux-hello-webauthn` | Daemon entry point (run by systemd) |
| `/usr/lib/systemd/system/linux-hello-webauthn.service` | Hardened systemd unit |
| `/usr/lib/udev/rules.d/70-linux-hello-webauthn.rules` | `uaccess` rule so your browser can reach the virtual device |

Without `-Dwith_webauthn=true`, none of these are installed and the Python
modules simply sit unused; the ordinary `linux-hello-cli` command and PAM module are
byte-for-byte the same.

---

## Setup

### 1. Enable it in the config

Edit `/etc/linux-hello/config.ini`:

```ini
[webauthn]
# Master switch. The PAM module ignores this whole section.
enabled = true
# Whose face unlocks credentials. Empty = owner of the active graphical
# session (resolved via logind at start-up).
user =
# Seconds a browser waits while your face is being verified.
verify_timeout = 10
```

### 2. Initialise the keystore

```sh
sudo linux-hello-cli webauthn init
```

This picks the **TPM keystore** if a usable TPM 2.0 is present, otherwise it
falls back to the **software (development-grade) keystore** and tells you so.
Force a backend with `sudo linux-hello-cli webauthn init tpm` or
`sudo linux-hello-cli webauthn init software`.

Check what you got:

```sh
sudo linux-hello-cli webauthn status
```

```
Initialised: yes
Keystore: tpm
TPM 2.0 usable: yes
Credentials for alice: 0
Face model for alice: yes
```

### 3. Start the service

```sh
sudo systemctl enable --now linux-hello-webauthn
```

The service **refuses to start** unless `[webauthn] enabled = true`, so
installing it is harmless until you opt in. Reload udev if the browser
can't see the device on the first try:

```sh
sudo udevadm control --reload && sudo udevadm trigger
```

### 4. Test it end to end without a browser

Before trusting a real website, verify signing works locally against a
built-in fake relying party:

```sh
sudo linux-hello-cli webauthn register        # creates a test passkey (look at the camera)
sudo linux-hello-cli webauthn authenticate    # signs a challenge and verifies the signature
sudo linux-hello-cli webauthn list            # shows stored credentials and sign counters
```

---

## Using it in a browser

Once the service is running, go to any passkey demo or a real site's
"add a passkey / security key" flow, for example
<https://webauthn.io>.

1. Choose to register a **passkey** / **security key**.
2. When the browser prompts for your security key, **look at your camera**.
3. Linux Hello verifies your face; the credential is created and stored.
4. To log in, repeat: the browser prompts, you look at the camera, done.

If the browser shows a PIN prompt instead of proceeding, it did not route to
the virtual device, see [Troubleshooting](#troubleshooting).

### Firefox notes

Firefox ≥ 114 supports USB CTAP2 authenticators natively. No `about:config`
changes are normally needed. Very old versions gated this behind
`security.webauthn.ctap2`: if you're on an old ESR, ensure it is `true`.

### Chromium notes

Chromium talks to USB security keys out of the box. No flags required.

---

## Configuration reference

All keys live under `[webauthn]` in `/etc/linux-hello/config.ini`. The PAM module
never reads this section.

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `false` | Master switch. The service will not start unless this is `true`. |
| `user` | *(empty)* | Whose face unlocks credentials. Empty means the owner of the active graphical session (uid ≥ 1000 via logind). |
| `verify_timeout` | `10` | Seconds the browser waits while your face is verified before giving up. |

The keystore backend is chosen once at `linux-hello-cli webauthn init` and recorded in
`/etc/linux-hello/webauthn/state.json`; `linux-hello-cli webauthn status` shows it.

### CLI reference

| Command | Action |
|---------|--------|
| `linux-hello-cli webauthn init [tpm\|software]` | One-time keystore setup |
| `linux-hello-cli webauthn status` | Show state, keystore, TPM availability, credential count |
| `linux-hello-cli webauthn list` | List stored credentials (site, account, sign count) |
| `linux-hello-cli webauthn remove <id>` | Delete a stored credential |
| `linux-hello-cli webauthn register` | Local self-test: create a test passkey |
| `linux-hello-cli webauthn authenticate` | Local self-test: sign & verify with the test passkey |
| `linux-hello-cli webauthn run` | Run the daemon in the foreground (normally systemd does this) |

All commands need root (like every other `linux-hello-cli` command).

---

## Security model

**Please read this.** This feature deliberately trades some assurance for
convenience. It is classified as **convenience-grade**, not high-assurance.

### What it inherits from the standards

- Real **CTAP2 / WebAuthn Level 3** protocol handling via `python-fido2`:
  no home-grown crypto or protocol.
- **ES256 (ECDSA P-256)** credential keys.
- **Per-credential key pairs**, scoped to the relying party (`rp_id`); a
  site only ever sees signatures from its own credential.
- A **signing counter** that increments on every assertion and is persisted
  before the assertion is released, so relying parties can detect cloning.
- Origin binding: the browser, not Linux Hello, enforces that the `rp_id` matches
  the site's origin.

### Key protection

- **Private keys are never stored in plaintext.**
  - **TPM keystore:** credential private keys are generated inside the TPM
    and never leave it. Only opaque wrapped blobs touch the disk; signing
    happens inside the TPM.
  - **Software keystore:** keys are encrypted at rest with **AES-256-GCM**
    under a master key in a `0600` file. This is **development-grade**:
    the master key is protected only by filesystem permissions, so a root
    compromise or offline disk access defeats it. `status` and `init` both
    warn about this. **Prefer the TPM keystore for anything real.**
- The credential store and keystore live under `/etc/linux-hello/webauthn/`,
  `0600`, root-owned. Writes are atomic (temp + fsync + rename) and guarded
  by `flock`.

### Face verification is mandatory per operation

Every `make_credential` and `get_assertion` calls the **same `compare.py`**
face check the PAM module uses, **immediately before** the key is used. There
is no caching or "remember me" window: no face match, no signature.

### What we never log

Private keys, credential IDs, assertion signatures, raw biometric
embeddings, and authentication secrets are **never** written to logs.

### Threat model: know the limits

- **Biometrics are not secrets.** A face is not a password; anti-spoofing
  depends entirely on your camera.
- **RGB-only (regular webcam) cameras provide weaker assurance than IR /
  depth systems.** A plain webcam can be fooled by a photo or video far more
  easily than an IR + depth sensor (as used by Windows Hello-certified
  hardware). If you care about presentation-attack resistance, use an IR
  camera and enable Linux Hello's anti-spoofing / rubberstamp features.
- **Root can impersonate the authenticator.** The daemon runs as root and,
  with the software keystore, root can also read the keys. This is a
  single-user convenience feature, not a defence against a compromised host.
- **No user-presence hardware button.** "User presence" is satisfied by the
  face check rather than a physical touch.

### Honest classification

This is a **convenience-grade software platform authenticator** for a
single trusted workstation. It is genuinely useful for reducing password
use, but you should not treat it as equivalent to a certified hardware
authenticator, and you should not present it to others as such.

---

## Limitations

- **Presents as a USB/roaming key, not an internal platform authenticator.**
  Browser prompts say "security key", and sites that *require* a platform
  authenticator (via `authenticatorAttachment: "platform"`) may not offer
  it. This is a consequence of the transport (see the design doc).
- **ES256 only.** No RS256 / EdDSA yet.
- **Self-attestation only.** No basic/attestation-CA attestation; sites that
  demand a trusted attestation chain will reject it.
- **Single user per running daemon.** One face, one session.
- **RGB cameras give weak anti-spoofing** (see above).
- **Software keystore is development-grade** (see above).
- **No browser autofill "platform passkey" integration** (e.g. the
  credentialsd / linux-credentials D-Bus portal) yet; that's on the
  roadmap.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Browser never offers the security key | Service not running (`systemctl status linux-hello-webauthn`) or udev rule not applied (`udevadm control --reload && udevadm trigger`, then re-plug/restart the service). |
| "requires the python-fido2 package" | `sudo pacman -S python-fido2`. |
| Service won't start | `[webauthn] enabled` is not `true`, or `linux-hello-cli webauthn init` was never run. Check `journalctl -u linux-hello-webauthn`. |
| "Could not determine which user's face to use" | Set `[webauthn] user` explicitly in `config.ini`. |
| Camera prompt times out | Raise `verify_timeout`, and confirm `sudo linux-hello-cli test` recognises you. |
| "No usable TPM 2.0 found" on `init tpm` | Install `python-tpm2-pytss`, confirm `/dev/tpmrm0` exists, or use the software keystore. |

---

## Developer architecture

Module layout under `linux-hello/src/`:

| File | Responsibility |
|------|----------------|
| `verification.py` | Internal face-verification API (`verify_face`, `FaceVerifier`, `BoundVerifier`) wrapping `compare.py`. Introduced by the Phase 1 refactor; PAM is untouched. |
| `webauthn/authenticator.py` | CTAP2 authenticator core: `make_credential`, `get_assertion`, `get_info`, etc. Calls the verifier before every key use. |
| `webauthn/store.py` | `CredentialStore`: credential metadata, RP scoping, sign-counter persistence, atomic + locked writes. |
| `webauthn/keystore.py` | `SoftwareKeyStore`: AES-256-GCM encrypted P-256 keys (development-grade). |
| `webauthn/keystore_tpm.py` | `TpmKeyStore`: keys generated and used inside a TPM 2.0 via `tpm2_pytss`. Optional import. |
| `webauthn/ctaphid.py` | CTAPHID transport: framing, channels, keepalives, CBOR dispatch, cancel. |
| `webauthn/uhid.py` | `/dev/uhid` virtual HID device with a FIDO usage-page report descriptor. |
| `webauthn/service.py` | Daemon loop: builds the authenticator, creates the uhid device, pumps reports. |
| `webauthn_daemon.py` | Root entry point run by systemd (separate from the CLI's non-root path). |
| `cli/webauthn.py` | `linux-hello-cli webauthn …` subcommands. |

The stack is verified end to end by `tests/integration/test_end_to_end.py`,
which drives it with **python-fido2's own CTAP2 client** over an in-memory
loopback, proving interoperability with an independent implementation
without needing root or `/dev/uhid`.

### Running the tests

```sh
# From the repo root, with python-fido2, cryptography and pytest available:
python -m pytest tests/ -q
```

Unit tests cover verification, the keystores, the credential store, the
authenticator core, and the CTAPHID transport; the integration test covers
registration, assertion, signature verification, and counter increments.

---

## Roadmap / future work

- **Platform-attachment integration** via the emerging Linux credentials
  D-Bus portal (credentialsd / linux-credentials), so the browser offers it
  as a native platform passkey with proper autofill.
- **Additional algorithms** (RS256, EdDSA).
- **Attestation options** beyond self-attestation.
- **Hardware presentation-attack** guidance and stronger defaults for IR
  cameras.
- **hmac-secret / PRF** extension support.

See [`webauthn-design.md`](webauthn-design.md) §11 for the full roadmap.
