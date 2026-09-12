# Linux Hello WebAuthn daemon
# Based on PR #1125 by qilsklo (https://github.com/boltgolt/howdy/pull/1125)
# Creates the virtual FIDO HID device and pumps host reports through the
# CTAPHID transport into the authenticator. Run as root (needs /dev/uhid,
# the camera and the root-only credential store).

import configparser
import logging
import os
import signal
import subprocess
import sys

import paths_factory

from i18n import _
from verification import BoundVerifier
from webauthn import create_keystore
from webauthn.authenticator import Authenticator
from webauthn.ctaphid import CtapHidDevice
from webauthn.store import CredentialStore
from webauthn.uhid import UHidDevice


def _resolve_user(config):
	"""The user whose face unlocks credentials"""
	configured = config.get("webauthn", "user", fallback="").strip()
	if configured:
		return configured

	# Fall back to the owner of the active graphical session via logind.
	# Querying the session list (instead of guessing from /run/user/*, which
	# is arbitrary on multi-seat machines) makes sure the face check matches
	# the user actually sitting in front of the screen.
	try:
		listing = subprocess.run(
			["loginctl", "list-sessions", "--no-legend"],
			capture_output=True, text=True, timeout=5, check=False)
		for line in listing.stdout.splitlines():
			session_id = line.split()[0] if line.split() else ""
			if not session_id:
				continue
			props = subprocess.run(
				["loginctl", "show-session", session_id,
					"--property=Active", "--property=Class",
					"--property=Name"],
				capture_output=True, text=True, timeout=5, check=False)
			fields = dict(
				part.split("=", 1) for part in props.stdout.split()
				if "=" in part)
			if fields.get("Active") == "yes" and fields.get("Class") in ("user", "lock-screen"):
				return fields.get("Name", "")
	except (OSError, subprocess.SubprocessError, ValueError):
		pass
	return ""


def run_service(state):
	# Route the authenticator's operational logging to stderr, which systemd
	# captures into the journal (journalctl -u linux-hello-webauthn)
	logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

	config = configparser.ConfigParser()
	config.read(paths_factory.config_file_path())

	if not config.getboolean("webauthn", "enabled", fallback=False):
		print(_("Linux Hello WebAuthn is disabled in the config ([webauthn] enabled = false)"))
		sys.exit(1)

	user = _resolve_user(config)
	if not user:
		print(_("Could not determine which user's face to use, set [webauthn] user"))
		sys.exit(1)

	verify_timeout = config.getfloat("webauthn", "verify_timeout", fallback=10.0)

	store = CredentialStore(paths_factory.webauthn_credentials_path(user))
	keystore = create_keystore(state["keystore"], paths_factory.webauthn_keystore_dir_path())
	verifier = BoundVerifier(user)
	authenticator = Authenticator(store, keystore, verifier, verify_timeout=verify_timeout)

	try:
		device = UHidDevice()
	except PermissionError:
		print(_("Cannot open /dev/uhid, the service must run as root"))
		sys.exit(1)
	except FileNotFoundError:
		print(_("/dev/uhid not present, load the uhid kernel module"))
		sys.exit(1)

	transport = CtapHidDevice(authenticator, device.send_input)

	stopping = {"flag": False}

	def handle_signal(signum, frame):
		stopping["flag"] = True
		try:
			device.close()
		except OSError:
			pass

	signal.signal(signal.SIGTERM, handle_signal)
	signal.signal(signal.SIGINT, handle_signal)

	print(_("Linux Hello WebAuthn authenticator running for user {} (keystore: {})").format(user, state["keystore"]))

	try:
		while not stopping["flag"]:
			try:
				event_type, report = device.read_event()
			except OSError:
				break
			if report:
				transport.feed_report(report)
	finally:
		device.close()
