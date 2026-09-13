# Linux Hello WebAuthn: Design Document

> Implemented in [boltgolt/howdy PR #1125](https://github.com/boltgolt/howdy/pull/1125) by **qilsklo**; adapted for Linux Hello.

**Status:** Draft / experimental feature design
**Scope:** An *optional* Linux Hello component that exposes a WebAuthn/FIDO2 platform-style
authenticator on Linux, gated on Linux Hello facial verification.
**Non-goals:** Replacing PAM functionality, FIDO certification, marketing parity with
Windows Hello.

---

## 1. Executive summary

Browsers on Linux have no OS-provided platform authenticator: on Windows the browser
delegates to Windows Hello, on macOS to Touch ID / iCloud Keychain, but on Linux both
Firefox and Chromium speak CTAP directly to USB HID security keys and nothing else
ships by default. This design adds an optional **Linux Hello WebAuthn** subsystem that:

1. implements a standards-compliant **CTAP2 authenticator core** in Python
   (credentials, ES256 signing, authenticator data, attestation) built on
   **python-fido2**: no invented protocols, no hand-rolled crypto;
2. performs **Linux Hello facial verification immediately before every credential
   operation**, reporting it as CTAP *user verification* (UV);
3. exposes the authenticator to browsers as a **virtual CTAP2 HID device via
   `/dev/uhid`**, which both Firefox (≥ 114) and Chromium already support with zero
   browser modification;
4. stores credentials so private keys are **never on disk in plaintext**, with a
   keystore abstraction whose backends are a clearly-marked software development
   keystore and a **TPM 2.0** backend (keys created inside the TPM, wrapped blobs on
   disk, signing inside the TPM);
5. leaves every existing Linux Hello code path (PAM, CLI, GTK) untouched; the feature is
   off unless explicitly initialised and its daemon started.

The architecture deliberately splits the **authenticator core** (transport-agnostic,
reusable) from the **transport** (UHID today; a `credentialsd` D-Bus backend later),
so Linux Hello can plug into the emerging Linux credential-portal ecosystem
([linux-credentials/credentialsd](https://github.com/linux-credentials/credentialsd))
when that stabilises, without rewriting credential or key management.

---

## 2. Background: how Linux Hello works today

Relevant facts established from the codebase (`linux-hello/src`):

- **Face verification** is one self-contained script, `compare.py`. It is executed as
  `python3 compare.py <username>`, opens the configured camera (V4L2 via OpenCV /
  ffmpeg / pyv4l2), computes dlib 128-dimensional face descriptors per frame, and
  compares them against the user's stored encodings by Euclidean distance
  (`certainty` config, default 3.5/10). It communicates *only through its exit code*:

  | Exit code | Meaning (mirrored in `pam/main.hh` `CompareError`) |
  |----------:|-----------------------------------------------------|
  | 0         | Face matched                                        |
  | 10        | `NO_FACE_MODEL`                                     |
  | 11        | `TIMEOUT_REACHED`                                   |
  | 12        | `ABORT` (bad invocation)                            |
  | 13        | `TOO_DARK`                                          |
  | 14        | `INVALID_DEVICE`                                    |
  | 15        | `RUBBERSTAMP` (extra check failed)                  |

- **PAM integration** (`pam/main.cc`) is a C++ module that `posix_spawn`s
  `compare.py` and races it against password entry. It knows nothing about the
  script's internals beyond the exit codes above. *This is the stable seam we reuse:
  anything that spawns `compare.py` and interprets exit codes gets identical
  behaviour to PAM.*

- **Face models** live in `/etc/linux-hello/models/<user>.dat` (JSON, root-owned). Config
  is INI at `/etc/linux-hello/config.ini`. The `linux-hello-cli` CLI requires root and dispatches to
  `cli/*.py` modules chosen by `argparse` in `cli.py`.

- Install layout is Meson-driven; Python sources land in `libdir/linux-hello` (or
  site-packages), and paths are generated into `paths.py`.

Consequences for this design:

- Root is the natural privilege level for the new daemon (camera, models and config
  are already root-controlled; `/dev/uhid` is root-only by default).
- The cleanest non-invasive internal API is a Python wrapper around the
  `compare.py` subprocess contract, not an in-process rewrite of `compare.py`
  (which is performance-tuned, threaded, and UI-coupled).

---

## 3. Research findings: the ecosystem

### 3.1 Browsers on Linux

| Browser | WebAuthn transport support on Linux (2026) |
|---|---|
| **Firefox** | CTAP1 + CTAP2 over **USB HID** via `authenticator-rs` (CTAP2 default since ~FF 114). Devices are discovered from hidraw nodes with the FIDO usage page (`0xF1D0`). No platform authenticator; a `credentialsd` web-extension/patched build exists experimentally (FF 140+). |
| **Chromium/Chrome** | Own FIDO stack (`device/fido`): **USB HID** (hidraw), hybrid/caBLE (phone), enterprise attestation, GPM passkeys via Google account. Discovers HID devices with FIDO usage page; needs a udev rule granting the user access to the hidraw node. No Linux platform authenticator. |

**Key fact:** both browsers *enumerate HID devices generically*. A kernel-level
virtual HID device created through **`/dev/uhid`** that reports the FIDO usage page
is indistinguishable from a plugged-in security key. This is proven in production by
several projects (below). No extension, flag, or browser patch is needed.

### 3.2 Existing projects (prior art)

| Project | Approach | Relevance |
|---|---|---|
| [psanford/tpm-fido](https://github.com/psanford/tpm-fido) | Go; U2F over uhid; TPM-wrapped keys; pinentry for presence | Validates uhid + TPM design; U2F-only, no CTAP2/passkeys |
| [BryanJacobs/fido2-hid-bridge](https://github.com/BryanJacobs/fido2-hid-bridge) | **Python + python-fido2 + uhid**, bridges CTAP2 to PC/SC | MIT-licensed reference for CTAPHID framing over uhid in Python |
| [nmdanny/softauth](https://github.com/nmdanny/softauth), [pando85/passless](https://github.com/pando85/passless) | Software CTAP2 authenticators over uhid | Validate CTAP2-over-uhid for browsers |
| [mc256/tpm-fido2-thinkpad-linux](https://github.com/mc256/tpm-fido2-thinkpad-linux) | Go; fingerprint (fprintd) + TPM as FIDO2 key over uhid; works with Firefox/Chrome unmodified | **Direct analogue** of this design with fingerprint instead of face |
| [linux-credentials/credentialsd](https://github.com/linux-credentials/credentialsd) (ex `xdg-credentials-portal`) | Rust D-Bus service + proposed XDG portal; Gateway / Flow-Control / UI-Control APIs; Firefox 140 extension + patched Flatpak; FOSDEM 2026 talk | The likely *future* of Linux platform authenticators. Today: USB + hybrid transports only; the "internal platform authenticator" flow exists as mockups. A Linux Hello backend here is the long-term goal, not a today deliverable |
| KeePassXC 2.7.7+ | Browser-extension native messaging passkeys | Shows native messaging works but only via its own extension; per-site `navigator.credentials` override, not OS-level |
| [Yubico python-fido2](https://github.com/Yubico/python-fido2) | CTAP2 client + WebAuthn data structures | Provides `AuthenticatorData.create`, `AttestedCredentialData.create`, COSE `ES256`, canonical CBOR, `CTAPHID` constants, `CtapError` codes: everything needed to *be* an authenticator except the command dispatcher |

### 3.3 Linux Hello issue tracker

- [#782](https://github.com/boltgolt/howdy/issues/782) requested exactly this
  feature (closed, unimplemented).
- [#1076](https://github.com/boltgolt/howdy/issues/1076) raises the passive-biometric
  concern (face can be captured by pointing the device at the user), incorporated
  into the threat model (§9) and mitigations (rubberstamps, §7.4).

### 3.4 TPM stack

Arch ships `tpm2-tss` 4.x and `python-tpm2-pytss` 2.3 (ESAPI bindings). The
standard pattern (used by tpm-fido and `pam_tpm2`-style tools): create a primary key
in the owner hierarchy, create per-credential ECC P-256 signing keys under it, store
only the TPM-wrapped `TPM2B_PUBLIC`/`TPM2B_PRIVATE` blobs on disk, load + sign inside
the TPM per operation. Private scalars never exist in system RAM or on disk.

---

## 4. Architecture decision: browser integration

Four options were evaluated (Phase-4 question, answered up front because it shapes
everything):

| Option | Works today? | Browser changes | Maintainability | Verdict |
|---|---|---|---|---|
| **1. Native messaging + web extension** | Only on sites the extension can script; `navigator.credentials` override is fragile, CSP-hostile, and per-browser packaging is heavy | Extension per browser | Poor: we'd own an extension, a JS shim, and a host protocol | ✗ Rejected, since credentialsd already ships exactly this shim for testing; duplicating it adds nothing |
| **2. Virtual CTAP2 HID device (`/dev/uhid`)** | **Yes**: Firefox ≥114 and all Chromium releases treat it as a security key; proven by tpm-fido, fido2-hid-bridge, softauth, passless, mc256 | **None** | Good: one kernel ABI (uhid is stable), one CTAP2 implementation, standard protocol | ✅ **Chosen transport for now** |
| **3. Own D-Bus platform-authenticator API** | No browser consumes a bespoke D-Bus API | Patches in both browsers | Terrible: we'd invent a protocol the prompt forbids and compete with credentialsd | ✗ Rejected |
| **4. Extend credentialsd** | Partially: needs their extension or patched Firefox; internal-authenticator flow not yet implemented; Rust codebase, LGPL, separate release cycle | Extension / patched builds | Long-term best: it *is* the emerging standard portal | 🔶 **Adopted as roadmap**, not as the first deliverable. Our authenticator core is transport-agnostic so a credentialsd/D-Bus backend can be added without touching credential or key management |

**Why UHID wins today:** it is the only option where an unmodified Firefox *and*
Chromium complete real registrations and assertions, using a stable kernel interface
and a protocol (CTAP2) with a public test-suite culture, multiple independent
implementations, and no invented parts. The cost (the browser believes it is
talking to a *roaming* authenticator rather than a *platform* one) is cosmetic for
the user (RPs see `transports: ["usb"]` and cannot request `platform` attachment to
reach us), and is exactly the trade-off every prior-art project accepted.

**Trajectory:** when credentialsd's internal-authenticator flow and browser
integrations mature, Linux Hello's authenticator core becomes its UV-providing backend
(option 4), and the UHID transport remains as a fallback for browsers without portal
support. This is documented as the explicit end-state so the project converges with,
rather than diverges from, the Linux desktop ecosystem.

---

## 5. Proposed architecture

```
                        ┌────────────────────────────────────────────┐
                        │                Browser                     │
                        │  Firefox ≥114 / Chromium (unmodified)      │
                        │  WebAuthn → CTAP2 client → hidraw          │
                        └───────────────────┬────────────────────────┘
                                            │ 64-byte HID reports (CTAPHID)
                              /dev/hidrawN  │  (udev rule grants user access)
                        ┌───────────────────▼────────────────────────┐
                        │            Linux kernel (uhid)             │
                        └───────────────────┬────────────────────────┘
                                            │ /dev/uhid (root)
┌──────────────────────────────────────────────────────────────────────────────┐
│                      linux-hello-webauthn daemon (root, systemd)                    │
│                                                                              │
│  transport layer                    authenticator core          verification │
│  ┌──────────────┐  CTAP2 CBOR   ┌──────────────────────┐   ┌──────────────┐  │
│  │ uhid.py      │  request/resp │ authenticator.py     │   │verification.py│ │
│  │ ctaphid.py   ├──────────────▶│  MakeCredential      ├──▶│ verify_face() │ │
│  │ (framing,    │◀──────────────┤  GetAssertion        │   │  = spawns     │ │
│  │  channels,   │  keepalives   │  GetInfo / Reset     │   │  compare.py   │ │
│  │  keepalive)  │  while UV runs│  GetNextAssertion    │   │  (same as PAM)│ │
│  └──────────────┘               └──────┬───────┬───────┘   └──────┬───────┘  │
│                                        │       │                  │ camera   │
│  (future: dbus.py - credentialsd       │       │                  ▼          │
│   backend, same core)             ┌────▼───┐ ┌─▼─────────┐   IR/RGB webcam   │
│                                   │store.py│ │keystore.py│                   │
│                                   │creds + │ │ software │                    │
│                                   │counters│ │ backend  │                    │
│                                   └────┬───┘ │keystore_ │                    │
│                                        │     │tpm.py    │──▶ TPM 2.0 (tss)   │
│                                        ▼     └──────────┘                    │
│                    /etc/linux-hello/webauthn/  (root-only, 0700/0600)              │
└──────────────────────────────────────────────────────────────────────────────┘
```

Module layout (all new, under `linux-hello/src/`):

```
linux-hello/src/verification.py            Phase 1: verify_face() internal API
linux-hello/src/webauthn/__init__.py
linux-hello/src/webauthn/store.py          credential records, sign counters, RP index
linux-hello/src/webauthn/keystore.py       KeyStore ABC + SoftwareKeyStore (dev)
linux-hello/src/webauthn/keystore_tpm.py   TpmKeyStore (optional import of tpm2_pytss)
linux-hello/src/webauthn/authenticator.py  CTAP2 command handling (transport-agnostic)
linux-hello/src/webauthn/ctaphid.py        CTAPHID framing/channels/keepalive
linux-hello/src/webauthn/uhid.py           /dev/uhid virtual device
linux-hello/src/webauthn/service.py        daemon entry point
linux-hello/src/cli/webauthn.py            linux-hello-cli webauthn init|register|authenticate|status|run|list|remove
```

### 5.1 Privilege & process model

- `linux-hello-webauthn.service` (systemd, root, `ExecStart=… service.py`): root is
  required for `/dev/uhid`, the camera during verification, and the credential
  store, matching Linux Hello's existing trust model (PAM verification also runs as root).
  Hardening directives (`ProtectHome`, `NoNewPrivileges`, `PrivateTmp`, syscall
  filter) are applied in the unit.
- A udev rule tags the virtual hidraw node so the *logged-in user's* browser can
  open it (same requirement as any physical security key on Chromium).
- **Which user's face?** The daemon serves one user per virtual device. Default:
  the configured `[webauthn] user`; if unset, the owner of the active graphical
  logind session at operation time. Multi-seat is out of scope (documented
  limitation §10).

### 5.2 Authenticator core (CTAP2)

Implemented against **CTAP 2.1** using python-fido2's data structures; the only code
we author is command dispatch and policy:

- `authenticatorGetInfo (0x04)`: versions `["FIDO_2_0","FIDO_2_1"]`; options
  `rk=true, up=true, uv=true, plat=false, credMgmt=false, clientPin=false/absent`;
  `maxMsgSize`; one AAGUID fixed for Linux Hello WebAuthn (single project-wide constant);
  algorithms: ES256 (-7) only (EdDSA later).
- `authenticatorMakeCredential (0x01)`: validates `clientDataHash`, RP entity,
  user entity, `pubKeyCredParams` (must include ES256), `excludeList` (returns
  `CTAP2_ERR_CREDENTIAL_EXCLUDED` after UP/UV), honours `rk`; **runs
  `verify_face()`** (UV) before creating the key; returns packed **self-attestation**
  (`alg: -7`, signature by the credential's own key, no attestation CA to protect,
  standard for non-certified/platform authenticators; RPs treating it as `none` is
  correct behaviour).
- `authenticatorGetAssertion (0x02)` / `GetNextAssertion (0x08)`: resolves
  credentials from `allowList` or, for empty `allowList`, from resident credentials
  for the RP ID; **runs `verify_face()` per ceremony**; increments and persists the
  sign counter *before* returning the assertion; sets flags `UP|UV`.
- `authenticatorSelection (0x0B)`: runs a short face check so browsers can
  disambiguate between multiple authenticators.
- `authenticatorReset (0x07)`: wipes the store; only permitted within the CTAP
  10-second-after-power-up window (i.e., daemon start) as the spec requires, plus a
  CLI equivalent (`linux-hello-cli webauthn clear`).
- Unsupported commands/extensions → proper CTAP error codes
  (`CTAP2_ERR_UNSUPPORTED_OPTION`, `CTAP1_ERR_INVALID_COMMAND`, …). `credProtect`
  is accepted and stored (all our credentials are effectively
  `userVerificationRequired` anyway); `hmac-secret` is not advertised initially.
- **PIN:** not implemented; UV is built-in (face). `clientPin` absent from options,
  so compliant clients never attempt `authenticatorClientPIN`. Verified against
  Firefox/Chromium behaviour during Phase 4 testing.

**User verification semantics (security requirement):** the core calls
`verify_face()` *synchronously inside* each MakeCredential/GetAssertion, after
request validation and *before* any key material is touched. There is no caching of
a previous verification, no "grace period". While verification runs, the transport
emits `CTAPHID_KEEPALIVE` with `STATUS_UPNEEDED` every ~100 ms so the browser shows
its "touch/verify your key" UI instead of timing out. Verification failure maps to
`CTAP2_ERR_OPERATION_DENIED` (and `CTAP2_ERR_USER_ACTION_TIMEOUT` on camera
timeout).

### 5.3 CTAPHID transport over uhid

- uhid device: vendor/product strings "Linux Hello Virtual FIDO2", HID report descriptor
  = the canonical FIDO usage page descriptor (`0xF1D0/0x01`, 64-byte IN/OUT
  reports), byte-for-byte the one from the CTAP spec, as used by fido2-hid-bridge.
- CTAPHID layer implements: channel allocation (`INIT`), fragmentation/reassembly
  (INIT/CONT frames), `PING`, `CBOR`, `MSG` (respond `CTAP1_ERR_INVALID_COMMAND`;
  U2F/CTAP1 not offered; CTAP2-only capability flag `CAPABILITY_CBOR`, `NMSG` set),
  `CANCEL` (aborts an in-flight face verification), `ERROR`, keepalives, busy
  handling (`ERR_CHANNEL_BUSY` for concurrent channels).
- Long-running CTAP2 operations run in a worker thread; the uhid read loop stays
  responsive; `CANCEL` terminates the compare subprocess.

### 5.4 Credential storage

`/etc/linux-hello/webauthn/` (0700 root):

```
/etc/linux-hello/webauthn/
├── state.json          # authenticator-level: AAGUID instance salt, keystore kind,
│                       # store format version
├── credentials/<user>.json   # per-user credential records (0600 root)
└── keystore/           # backend-specific material (0700)
    ├── master.key      # SoftwareKeyStore only: AES-256-GCM master key (0600)
    └── tpm/            # TpmKeyStore: primary-key context/persistent handle info
```

Credential record (JSON; binary fields base64url):

```json
{
  "credential_id": "…16-byte random id…",
  "rp_id": "example.com",
  "rp_name": "Example",
  "user_handle": "…",
  "user_name": "fred",
  "user_display_name": "Fred",
  "cose_public_key": "…CBOR…",
  "key_ref": "opaque keystore handle (blob path / wrapped blob)",
  "sign_count": 42,
  "resident": true,
  "cred_protect": 2,
  "created_at": "2026-07-06T…",
  "algorithm": -7
}
```

- `credential_id` is 16 random bytes (a pure lookup key, *not* key-wrapping
  material, so no stateless-authenticator key-derivation scheme to get wrong).
  All credentials are stored server-side in the store; resident vs non-resident
  only controls whether the credential is discoverable with an empty `allowList`.
- `sign_count` is per-credential, monotonically increasing, persisted with an
  atomic write (write-temp + `fsync` + `rename`) *before* the assertion is
  released, so a crash can skip counter values but never repeat one.
- Writes of the whole store are atomic; the daemon holds an `flock` to prevent
  CLI/daemon races.

### 5.5 Keystore abstraction (TPM-ready by construction)

```python
class KeyStore(ABC):
    kind: str                                   # "software" | "tpm"
    def generate_key(self) -> tuple[str, CoseKey]   # returns (key_ref, public)
    def sign(self, key_ref: str, message: bytes) -> bytes   # DER ECDSA sig
    def delete_key(self, key_ref: str) -> None
    def destroy_all(self) -> None               # for authenticatorReset
```

- **SoftwareKeyStore (development keystore, clearly marked):** P-256 keys via
  `cryptography`; private keys are AES-256-GCM-encrypted (unique nonce per blob,
  key_ref as AAD) under a random master key in `/etc/linux-hello/webauthn/keystore/master.key`
  (0600 root). *Never plaintext on disk*, but the master key lives beside the data,
  so at-rest protection reduces to root filesystem permissions, the same level as
  Linux Hello's face models. Every surface (CLI `status`, docs, log line at daemon start)
  labels it `software (development)`.
- **TpmKeyStore:** via `tpm2_pytss.ESAPI`:
  - one ECC P-256 primary in the owner hierarchy (deterministic template, so it is
    re-derivable; not persisted by default to avoid NVRAM pressure),
  - `Create` a P-256 signing key per credential → store the wrapped
    `TPM2B_PUBLIC/PRIVATE` blobs as `key_ref` payload,
  - `Load` + `Sign` (ECDSA/SHA-256) per assertion, then flush,
  - private scalars never leave the TPM; blobs on disk are useless on any other
    machine (and can additionally be sealed to PCR policy later).
  - `tpm2_pytss` is an *optional* import: absence degrades to SoftwareKeyStore with
    a warning at `linux-hello-cli webauthn init`.
- Backend is chosen at `init` (`--keystore tpm|software`, default: `tpm` if a TPM
  and `tpm2_pytss` are present, else software) and recorded in `state.json`.
  Migration between backends is explicitly *not* supported (keys are
  non-exportable by design); switching requires re-registering credentials.

### 5.6 Face verification API (Phase 1 refactor)

```python
# linux-hello/src/verification.py
class VerificationResult(enum.IntEnum):
    SUCCESS = 0
    NO_FACE_MODEL = 10
    TIMEOUT = 11
    ABORT = 12
    TOO_DARK = 13
    INVALID_DEVICE = 14
    RUBBERSTAMP = 15

def verify_face(user: str, timeout: float | None = None) -> VerificationResult
```

Implementation: spawn `python3 compare.py <user>` (the identical contract the PAM
module uses) and map the exit status. Rationale:

- zero risk to PAM/login/sudo (no changes to `compare.py` semantics);
- inherits every Linux Hello feature for free (recorder plugins, rubberstamps, dark-frame
  logic, GTK auth UI popup);
- trivially testable by injecting a fake executable;
- an in-process API can replace the internals later without changing callers.

The subprocess handle is exposed so the CTAP layer can kill it on `CTAPHID_CANCEL`.

### 5.7 Configuration (all optional, defaults off)

```ini
[webauthn]
# Master switch for the linux-hello-webauthn service. The PAM module ignores this section.
enabled = false
# User whose face unlocks credentials; empty = owner of the active graphical session
user =
# Keystore backend recorded at init: tpm | software
# (shown in `linux-hello-cli webauthn status`)
# Seconds a browser waits while your face is verified
verify_timeout = 10
```

### 5.8 CLI

```
linux-hello-cli webauthn init            # create store, choose+initialise keystore, print status
linux-hello-cli webauthn status          # keystore kind, TPM availability, daemon state, cred count
linux-hello-cli webauthn register        # local end-to-end test: fake RP MakeCredential
linux-hello-cli webauthn authenticate    # local end-to-end test: fake RP GetAssertion + verify sig
linux-hello-cli webauthn list            # list credentials (RP, user, created; never IDs/keys)
linux-hello-cli webauthn remove <n>      # delete a credential
linux-hello-cli webauthn run             # run the service in the foreground (what systemd calls)
```

`register`/`authenticate` exercise the full core (including real face verification
and real signing) against a built-in fake RP, because real registration happens in
the browser; they double as manual integration tests.

---

## 6. Flows

### 6.1 Registration (browser)

```
User clicks "create passkey" on https://example.com
Browser (WebAuthn client)                    linux-hello-webauthn
  │ CTAPHID_INIT ─────────────────────────────▶ allocate channel
  │ CTAPHID_CBOR authenticatorGetInfo ────────▶ rk,uv,up; no clientPin
  │ CTAPHID_CBOR authenticatorMakeCredential ─▶ validate params, excludeList
  │ ◀─ KEEPALIVE(UPNEEDED) every 100ms ──────── verify_face(user)   ←camera
  │        (browser shows "verify identity")      │ exit 0 = match
  │                                               ▼
  │                                    keystore.generate_key() (TPM/sw)
  │                                    store credential (+rk data, counter=0)
  │                                    build authData: rpIdHash|UP,UV,AT|counter
  │                                             |AAGUID|credId|COSE pubkey
  │ ◀─ CBOR {fmt:"packed", authData, attStmt(self-sig)} ─┘
  ▼
Browser → RP: attestationObject + clientDataJSON
```

### 6.2 Authentication (browser)

```
Browser sends authenticatorGetAssertion {rpId, clientDataHash, allowList?}
  → look up credential(s): allowList ∩ store, or resident creds for rpId
  → NO match: CTAP2_ERR_NO_CREDENTIALS (before any camera use)
  → match: verify_face(user)  [KEEPALIVEs meanwhile; CANCEL kills camera]
      → fail/timeout: CTAP2_ERR_OPERATION_DENIED / USER_ACTION_TIMEOUT
      → success:
          counter += 1; persist store (atomic) BEFORE responding
          authData = sha256(rpId) | flags(UP|UV) | counter
          sig = keystore.sign(key_ref, authData || clientDataHash)
          respond {credential, authData, sig, user?}   (+GetNextAssertion if >1)
```

RP-side verification then checks the signature against the registered public key,
the rpIdHash, the UV flag, and counter regression, all standard WebAuthn L3; we
implement nothing RP-side.

### 6.3 What the user experiences

1. Site prompts for a passkey → browser shows its security-key dialog.
2. Linux Hello's GTK notice appears (same UI as sudo) while the camera looks for you.
3. Face match → dialog closes, site logs you in. No PIN, no touch.

---

## 7. Security analysis

### 7.1 What the design gets from the standards

- **Phishing resistance / origin binding:** the browser computes `clientDataHash`
  and enforces RP-ID ↔ origin rules; we sign over `rpIdHash` and refuse credentials
  whose stored `rp_id` doesn't match the request. A credential for `example.com`
  can never answer for `evil.com`.
- **Replay protection:** every assertion signs a fresh RP-chosen random challenge
  (inside `clientDataHash`); captured assertions cannot be replayed. The
  monotonic, crash-safe sign counter lets RPs detect cloned credential stores.
- **No secrets in transit:** the CTAPHID link carries public data + signatures
  only; private keys never cross it.

### 7.2 Key protection

| Backend | Key at rest | Key in use | Compromise required |
|---|---|---|---|
| TPM | wrapped blob, unusable off-machine | inside TPM | root *and* runtime abuse of the live daemon; keys still not extractable |
| Software (dev) | AES-256-GCM blob; master key 0600 root on same disk | process memory | root or offline disk read (unless FDE); **explicitly a development keystore** |

Never logged (enforced convention + reviewed): private keys, wrapped blobs,
credential IDs, assertion signatures, user handles, face encodings. Logs carry RP ID
and outcome only.

### 7.3 Threat model

| Threat | Addressed? | How / residual risk |
|---|---|---|
| Phishing site requests assertion | ✅ | Browser origin checks + rpIdHash binding |
| Network attacker replays assertion | ✅ | RP challenge freshness; signature covers it |
| Malicious local *unprivileged* process talks CTAP directly | ⚠️ | hidraw node is user-accessible (required for browsers), so any process in the user's session can request an assertion, but **every operation still requires a live face match in front of the camera**, and the GTK notice is shown. Equivalent to a physical key with fingerprint UV plugged in permanently. Residual: user-session malware could time a request while the user is at the camera; the visible prompt is the mitigation |
| Root compromise | ❌ out of scope | Root owns the store, config, and camera (same as PAM Linux Hello). TPM keeps keys non-exportable but a root attacker can use them while resident |
| Photo/video presentation attack | ⚠️ | Inherited Linux Hello limitation: dlib matching has **no certified liveness detection**. IR-emitter cameras defeat printed photos; RGB-only cameras are weak. Mitigations: `rubberstamps` (nod/hotkey) apply to WebAuthn too because we reuse `compare.py`; documented prominently (§10) |
| Coerced/unconscious user, camera pointed at victim (issue #1076) | ⚠️ | Same passive-biometric weakness as every face unlock; rubberstamp active gestures are the opt-in mitigation |
| Cloned disk / stolen laptop | TPM: ✅ blobs unusable elsewhere; also detectable via sign counter. Software keystore: ❌ without FDE (documented) |
| Evil-maid replacement of face model | ⚠️ | Anyone with root can add a face model; pre-existing Linux Hello property, unchanged by this feature |
| Downgrade/UV-bypass by client | ✅ | UV is unconditional for every credential operation regardless of `uv`/`up` request options; there is no code path that signs without a fresh face match |

### 7.4 Honest classification

This is a **software authenticator with biometric user-verification of
opportunistic strength**. It is *not* FIDO-certified, does not have a secure
display, does not run in a TEE, and its biometric matcher (dlib ResNet, typically
FAR ≫ certified-biometrics requirements, no PAD/anti-spoofing certification) would
not pass FIDO Biometric Component Certification. With an RGB-only camera it should
be treated as **convenience-grade**, roughly "password autofill gated by your
face", not as Windows-Hello-equivalent. Documentation (docs/webauthn.md) must carry
this framing verbatim; nothing in CLI output or docs may claim parity with
certified authenticators.

---

## 8. TPM integration options considered

| Option | Notes | Decision |
|---|---|---|
| Per-credential keys created in TPM, wrapped blobs on disk | Standard, scales, keys never leave TPM, no NVRAM pressure | ✅ chosen (`TpmKeyStore`) |
| Seal a software master key with TPM (+PCRs) | Keys still plaintext in RAM at scale; weaker than native TPM keys; simpler | ✗ (inferior to native keys at similar complexity) |
| Persistent handles per credential | NVRAM exhaustion after a handful of credentials | ✗ |
| `tpm2-pkcs11` | Extra daemon/DB layer; python-fido2 doesn't need PKCS#11 | ✗ |
| PCR-bound policies (boot-state sealing) | Valuable hardening; brittle across kernel updates | Future work |

`fapi` vs `esys`: ESAPI (`tpm2_pytss.ESAPI`) chosen; lower-level but dependency-free
of FAPI config profiles, and the operations needed (CreatePrimary, Create, Load,
Sign, FlushContext) are few.

---

## 9. Testing strategy

Unit tests (pytest, no hardware, no root, everything injectable):

- store: create/lookup/delete, per-RP resident queries, counter monotonicity,
  atomic persistence (crash-simulation via killed writer), flock exclusion.
- keystore: software backend round-trip (generate → sign → verify with
  `cryptography`), blobs-on-disk-are-not-plaintext check, wrong-AAD failure.
- authenticator core with `FakeKeyStore` + `FakeVerifier`:
  - MakeCredential: happy path (attestation object parses via python-fido2 and
    self-attestation signature verifies), unsupported alg, excludeList hit,
    UV failure ⇒ OPERATION_DENIED and **no credential created**,
  - GetAssertion: happy path (signature verifies against registered public key),
    unknown RP ID / empty store ⇒ NO_CREDENTIALS **without invoking verifier**,
    UV failure ⇒ no signature, counter unchanged,
    counter increments across assertions and survives store reload,
    resident-credential discovery with empty allowList, GetNextAssertion ordering.
- ctaphid: INIT/fragmentation round-trip, keepalive emission during slow verifier,
  CANCEL aborts verification, oversized/garbage frames rejected per spec.
- verification: exit-code mapping via stub `compare.py`.

Integration tests (opt-in, need root/uhid): create the uhid device and drive it with
python-fido2's *client* (`fido2.hid`) as if it were the browser: full
register+assert loop against the running daemon (`tests/integration/`, skipped
unless `LINUX_HELLO_WEBAUTHN_IT=1`).

Manual browser matrix documented in docs/webauthn.md: webauthn.io on Firefox and
Chromium.

---

## 10. Known limitations

1. **Biometric strength:** no certified liveness/PAD; RGB-only cameras are
   spoofable with photos/screens; IR cameras raise but do not certify assurance.
2. **Appears as a roaming (USB) authenticator**, not `platform` attachment; RPs
   filtering on `authenticatorAttachment: "platform"` won't offer it; RPs asking
   for `"cross-platform"` will (that's most security-key flows).
3. **Self-attestation only**: RPs enforcing attestation allow-lists (rare outside
   enterprise) will reject registration.
4. **Single-user per device instance;** multi-seat unsupported.
5. **Software keystore is development-grade** (root-readable master key).
6. **No hmac-secret/PRF extension initially** (some passkey-encrypted apps use PRF);
   no CTAP1/U2F fallback; ES256 only.
7. **credentialsd/portal integration not yet implemented** (roadmap).
8. Any process in the user's session with hidraw access can *initiate* a ceremony
   (§7.3); visible prompt + mandatory face match is the control.

---

## 11. Implementation roadmap

| Phase | Deliverable | Risk to existing Linux Hello |
|---|---|---|
| 1 | `verification.py` (`verify_face`), pytest scaffolding, tests | None (new files only) |
| 2 | `webauthn/store.py`, `keystore.py`, `keystore_tpm.py` + tests | None |
| 3 | `webauthn/authenticator.py` (CTAP2 core) + CLI (`init/register/authenticate/status/list/remove`) + tests; wire into `cli.py` (one `elif`) and config.ini (`[webauthn]`, disabled) | One-line dispatch addition |
| 4 | `ctaphid.py`, `uhid.py`, `service.py`, systemd unit + udev rule (installed but disabled), meson `webauthn` install additions, docs/webauthn.md, browser verification | Packaging only |
| Future | credentialsd D-Bus backend; hmac-secret/PRF; PCR-sealed TPM policies; EdDSA; credManagement API; liveness-oriented rubberstamp defaults for WebAuthn | - |

Dependencies (Arch): `python-fido2` (required for the feature), `python-tpm2-pytss` +
`tpm2-tss` (optional, TPM backend), `python-cryptography` (already a transitive
requirement of python-fido2). All are optional for Linux Hello itself; without them the
`linux-hello-cli webauthn` subcommand explains what to install and every existing feature is
unaffected.

---

## 12. References

- WebAuthn Level 3: https://www.w3.org/TR/webauthn-3/
- CTAP 2.1: https://fidoalliance.org/specs/fido-v2.1-ps-20210615/fido-client-to-authenticator-protocol-v2.1-ps-20210615.html
- Linux uhid ABI: https://www.kernel.org/doc/Documentation/hid/uhid.txt
- python-fido2: https://github.com/Yubico/python-fido2
- tpm2-pytss: https://github.com/tpm2-software/tpm2-pytss
- credentialsd / Credentials for Linux: https://github.com/linux-credentials/credentialsd
- libwebauthn (linux-credentials): https://github.com/linux-credentials/libwebauthn
- Prior art: tpm-fido, fido2-hid-bridge, softauth, passless, tpm-fido2-thinkpad-linux (§3.2)
- Upstream issues: boltgolt/howdy#782, boltgolt/howdy#1076
