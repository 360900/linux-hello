# CLI subcommand for the optional Linux Hello WebAuthn authenticator
# Based on PR #1125 by qilsklo (https://github.com/boltgolt/howdy/pull/1125)
# Called with: linux-hello-cli webauthn <action> [arguments...]

import builtins
import configparser
import datetime
import hashlib
import json
import os
import sys

import paths_factory

from i18n import _

user = builtins.linux_hello_user
args = builtins.linux_hello_args
action = args.arguments[0] if args.arguments else "status"

# Give a friendly error if the optional dependency is missing, before any
# webauthn module (which imports fido2) is touched
try:
	import fido2  # noqa: F401
except ImportError:
	print(_("The Linux Hello WebAuthn feature requires the python-fido2 package"))
	print(_("On Arch Linux install it with:"))
	print("\n\tsudo pacman -S python-fido2\n")
	sys.exit(1)

from verification import BoundVerifier
from webauthn import create_keystore
from webauthn.authenticator import Authenticator
from webauthn.store import CredentialStore


def load_state():
	"""Read authenticator state, returns None if init has not been run"""
	try:
		with open(paths_factory.webauthn_state_path()) as file:
			return json.load(file)
	except FileNotFoundError:
		return None


def require_state():
	state = load_state()
	if state is None:
		print(_("Linux Hello WebAuthn has not been set up yet, please run:"))
		print("\n\tsudo linux-hello-cli webauthn init\n")
		sys.exit(1)
	return state


def make_authenticator(state):
	config = configparser.ConfigParser()
	config.read(paths_factory.config_file_path())
	verify_timeout = config.getfloat("webauthn", "verify_timeout", fallback=10.0)

	store = CredentialStore(paths_factory.webauthn_credentials_path(user))
	keystore = create_keystore(state["keystore"], paths_factory.webauthn_keystore_dir_path())
	return Authenticator(store, keystore, BoundVerifier(user), verify_timeout=verify_timeout)


def cmd_init():
	if load_state() is not None:
		print(_("Linux Hello WebAuthn is already initialised, see \"linux-hello-cli webauthn status\""))
		sys.exit(1)

	from webauthn.keystore_tpm import tpm_is_usable

	requested = args.arguments[1] if len(args.arguments) > 1 else None
	if requested not in (None, "tpm", "software"):
		print(_("Unknown keystore \"{}\", use \"tpm\" or \"software\"").format(requested))
		sys.exit(1)

	if requested == "tpm" and not tpm_is_usable():
		print(_("No usable TPM 2.0 found (is python-tpm2-pytss installed?)"))
		sys.exit(1)

	kind = requested or ("tpm" if tpm_is_usable() else "software")

	directory = paths_factory.webauthn_dir_path()
	os.makedirs(directory, mode=0o700, exist_ok=True)
	fd = os.open(paths_factory.webauthn_state_path(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
	with os.fdopen(fd, "w") as file:
		json.dump({"version": 1, "keystore": kind}, file)

	print(_("Linux Hello WebAuthn initialised"))
	if kind == "tpm":
		print(_("Keystore: tpm (credential keys live inside the TPM)"))
	else:
		print(_("Keystore: software (DEVELOPMENT GRADE, keys are only protected"))
		print(_("by filesystem permissions, consider the TPM keystore instead)"))
	print(_("\nNext steps:"))
	print(_("  1. Make sure a face model exists: sudo linux-hello-cli -U {} add").format(user))
	print(_("  2. Enable the service: sudo systemctl enable --now linux-hello-webauthn"))


def cmd_status():
	from webauthn.keystore_tpm import TPM_AVAILABLE, tpm_is_usable

	state = load_state()
	if state is None:
		print(_("Initialised: no (run \"sudo linux-hello-cli webauthn init\")"))
		return

	print(_("Initialised: yes"))
	print(_("Keystore: ") + state["keystore"])
	if state["keystore"] == "software":
		print(_("  WARNING: development keystore, not hardware backed"))
	print(_("TPM 2.0 usable: ") + (_("yes") if tpm_is_usable() else _("no") + ("" if TPM_AVAILABLE else _(" (python-tpm2-pytss not installed)"))))

	store = CredentialStore(paths_factory.webauthn_credentials_path(user))
	print(_("Credentials for {}: {}").format(user, len(store.list_all())))

	model_path = paths_factory.user_model_path(user)
	print(_("Face model for {}: {}").format(user, _("yes") if os.path.isfile(model_path) else _("NO, add one with \"sudo linux-hello-cli add\"")))


def cmd_list():
	require_state()
	store = CredentialStore(paths_factory.webauthn_credentials_path(user))
	credentials = store.list_all()
	if not credentials:
		print(_("No WebAuthn credentials stored for ") + user)
		return

	print(_("Known WebAuthn credentials for ") + user + ":\n")
	print("\t" + _("ID  Website              Account              Uses  Created"))
	for index, credential in enumerate(credentials):
		created = datetime.datetime.fromtimestamp(credential.created_at).strftime("%Y-%m-%d %H:%M")
		print("\t{:<3} {:<20} {:<20} {:<5} {}".format(
			index, credential.rp_id[:20], (credential.user_name or "-")[:20],
			credential.sign_count, created))


def cmd_remove():
	require_state()
	if len(args.arguments) < 2:
		print(_("Please add the ID of the credential to remove, shown by \"linux-hello-cli webauthn list\""))
		sys.exit(1)

	store = CredentialStore(paths_factory.webauthn_credentials_path(user))
	credentials = store.list_all()
	try:
		credential = credentials[int(args.arguments[1])]
	except (ValueError, IndexError):
		print(_("No credential with ID ") + args.arguments[1])
		sys.exit(1)

	store.remove(credential.credential_id)
	print(_("Removed the credential for {} ({})").format(credential.rp_id, credential.user_name or "-"))


def cmd_register():
	"""Local end to end test: register a credential for a built-in fake RP"""
	state = require_state()
	authenticator = make_authenticator(state)

	print(_("Testing credential creation against a local fake website"))
	print(_("Look at the camera to confirm with your face"))

	from fido2.ctap import CtapError
	try:
		response = authenticator.make_credential({
			1: hashlib.sha256(b"linux-hello webauthn register test").digest(),
			2: {"id": "linuxhello.test", "name": "Linux Hello local test"},
			3: {"id": user.encode(), "name": user, "displayName": user},
			4: [{"type": "public-key", "alg": -7}],
			7: {"rk": True},
		})
	except CtapError as err:
		print(_("Registration failed: ") + str(err))
		sys.exit(1)

	print(_("\nFace verified and test credential created (format: {})").format(response[1]))
	print(_("Run \"linux-hello-cli webauthn authenticate\" to test signing with it"))


def cmd_authenticate():
	"""Local end to end test: assert over the fake RP credential and verify it"""
	state = require_state()
	authenticator = make_authenticator(state)

	print(_("Testing authentication against a local fake website"))
	print(_("Look at the camera to confirm with your face"))

	from fido2 import cbor
	from fido2.cose import CoseKey
	from fido2.ctap import CtapError

	challenge_hash = hashlib.sha256(os.urandom(32)).digest()
	try:
		response = authenticator.get_assertion({
			1: "linuxhello.test",
			2: challenge_hash,
		})
	except CtapError as err:
		if err.code == CtapError.ERR.NO_CREDENTIALS:
			print(_("No test credential found, run \"linux-hello-cli webauthn register\" first"))
		else:
			print(_("Authentication failed: ") + str(err))
		sys.exit(1)

	# Do what the website would do: check the signature against the
	# public key stored at registration
	store = CredentialStore(paths_factory.webauthn_credentials_path(user))
	credential = store.find_by_id(response[1]["id"])
	public_key = CoseKey.parse(cbor.decode(credential.cose_public_key))
	public_key.verify(response[2] + challenge_hash, response[3])

	print(_("\nFace verified and assertion signature checked (sign counter: {})").format(credential.sign_count))


def cmd_run():
	state = require_state()
	from webauthn.service import run_service
	run_service(state)


actions = {
	"init": cmd_init,
	"status": cmd_status,
	"list": cmd_list,
	"remove": cmd_remove,
	"register": cmd_register,
	"authenticate": cmd_authenticate,
	"run": cmd_run,
}

if action not in actions:
	print(_("Unknown action \"{}\", use one of: ").format(action) + ", ".join(actions))
	sys.exit(1)

actions[action]()
