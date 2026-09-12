# CTAP 2.1 authenticator core, transport agnostic
# Based on PR #1125 by qilsklo (https://github.com/boltgolt/howdy/pull/1125)
# Command parsing/dispatch only: data structures and CBOR come from
# python-fido2, signing comes from the keystore, user verification is a
# fresh face check before every credential operation

import hashlib
import logging
import os
import time

from fido2 import cbor
from fido2.cose import ES256
from fido2.ctap import CtapError
from fido2.webauthn import AttestedCredentialData, AuthenticatorData

from verification import VerificationResult
from webauthn import AAGUID
from webauthn.store import Credential

# CTAP2 command bytes
CMD_MAKE_CREDENTIAL = 0x01
CMD_GET_ASSERTION = 0x02
CMD_GET_INFO = 0x04
CMD_CLIENT_PIN = 0x06
CMD_RESET = 0x07
CMD_GET_NEXT_ASSERTION = 0x08
CMD_SELECTION = 0x0B

CREDENTIAL_ID_LENGTH = 16
# Reset is only honoured this many seconds after service start, per spec
RESET_WINDOW = 10
# GetNextAssertion must follow its GetAssertion within this many seconds
NEXT_ASSERTION_WINDOW = 30

ERR = CtapError.ERR
FLAG = AuthenticatorData.FLAG

# Operational logging. Deliberately never logs keys, credential IDs,
# assertions, biometric data or client data hashes; only operation names,
# RP ids, counts and outcomes so a failing ceremony can be diagnosed.
log = logging.getLogger("linux-hello.webauthn")

_COMMAND_NAMES = {
	CMD_MAKE_CREDENTIAL: "makeCredential",
	CMD_GET_ASSERTION: "getAssertion",
	CMD_GET_INFO: "getInfo",
	CMD_CLIENT_PIN: "clientPin",
	CMD_RESET: "reset",
	CMD_GET_NEXT_ASSERTION: "getNextAssertion",
	CMD_SELECTION: "selection",
}


def _rp_id_hash(rp_id):
	return hashlib.sha256(rp_id.encode()).digest()


def _verification_error(result):
	"""Map a failed VerificationResult to the CTAP error to return"""
	if result == VerificationResult.TIMEOUT_REACHED:
		return CtapError(ERR.USER_ACTION_TIMEOUT)
	if result == VerificationResult.CANCELLED:
		return CtapError(ERR.KEEPALIVE_CANCEL)
	return CtapError(ERR.OPERATION_DENIED)


def _check_pin_uv_auth_param(pin_uv_auth_param):
	"""Handle the platform's pinUvAuthParam, if any.

	We do user verification with a built-in face check and support neither a
	client PIN nor pinUvAuthToken. A zero-length pinUvAuthParam is the CTAP2.1
	way for a platform to probe our PIN/UV state; answering PIN_NOT_SET tells
	it there is no PIN so it retries the ceremony using built-in UV. Any
	non-empty value would be a token we never issued, so it is ignored: the
	face check is the real UV gate regardless.
	"""
	if pin_uv_auth_param is not None and len(pin_uv_auth_param) == 0:
		raise CtapError(ERR.PIN_NOT_SET)


class Authenticator:
	"""Handles CTAP2 authenticator commands for a single user's credentials

	The verifier must expose verify() -> VerificationResult performing the
	face check for the configured user, and cancel().
	"""

	def __init__(self, store, keystore, verifier, verify_timeout=None):
		self.store = store
		self.keystore = keystore
		self.verifier = verifier
		self.verify_timeout = verify_timeout
		self._started = time.monotonic()
		self._next_assertions = []
		self._next_assertion_data = None
		self._next_assertion_time = 0

	def _verify_user(self):
		"""Run a fresh face verification, raises CtapError unless it succeeds"""
		log.info("starting face verification (timeout=%s)", self.verify_timeout)
		result = self.verifier.verify(timeout=self.verify_timeout)
		log.info("face verification result: %s", result.name)
		if result != VerificationResult.SUCCESS:
			raise _verification_error(result)

	def cancel(self):
		"""Abort a verification in progress (CTAPHID_CANCEL)"""
		self.verifier.cancel()

	def handle_cbor(self, request):
		"""Handle one CTAP2 message (command byte + CBOR request map)

		Returns the response message (status byte + CBOR response map).
		"""
		if len(request) == 0:
			return bytes([ERR.INVALID_LENGTH])

		command = request[0]
		params = {}
		if len(request) > 1:
			try:
				params = cbor.decode(request[1:])
			except Exception:
				return bytes([ERR.INVALID_CBOR])
			if not isinstance(params, dict):
				return bytes([ERR.CBOR_UNEXPECTED_TYPE])

		handlers = {
			CMD_MAKE_CREDENTIAL: self.make_credential,
			CMD_GET_ASSERTION: self.get_assertion,
			CMD_GET_INFO: self.get_info,
			CMD_RESET: self.reset,
			CMD_GET_NEXT_ASSERTION: self.get_next_assertion,
			CMD_SELECTION: self.selection,
		}
		handler = handlers.get(command)
		if handler is None:
			log.warning("unsupported command 0x%02x", command)
			if command == CMD_CLIENT_PIN:
				return bytes([ERR.PIN_AUTH_INVALID])
			return bytes([ERR.INVALID_COMMAND])

		log.info("%s requested", _COMMAND_NAMES.get(command, command))
		try:
			response = handler(params)
		except CtapError as err:
			log.info("%s -> CTAP error 0x%02x (%s)", _COMMAND_NAMES.get(command, command), err.code, err.code.name)
			return bytes([err.code])
		except Exception:
			log.exception("%s -> unexpected error", _COMMAND_NAMES.get(command, command))
			return bytes([ERR.OTHER])
		log.info("%s -> success", _COMMAND_NAMES.get(command, command))

		if response is None:
			return bytes([ERR.SUCCESS])
		return bytes([ERR.SUCCESS]) + cbor.encode(response)

	def get_info(self, params=None):
		return {
			1: ["FIDO_2_0", "FIDO_2_1"],
			2: ["credProtect"],
			3: AAGUID,
			4: {
				# Deliberately "plat": false. The authenticator presents
				# itself as a USB security key over CTAPHID, and browsers
				# route those through the roaming-authenticator flow, which
				# is the only path that works with a uhid-backed device.
				# Claiming "plat": true would make browsers expect an
				# internal transport and skip the device entirely.
				"plat": False,
				"rk": True,
				"up": True,
				"uv": True,
				# clientPin deliberately absent: user verification is built in
			},
			5: 2048,  # maxMsgSize
			9: ["usb"],  # transports as seen by the client
			10: [{"alg": ES256.ALGORITHM, "type": "public-key"}],
		}

	def make_credential(self, params):
		client_data_hash = params.get(1)
		rp = params.get(2)
		user = params.get(3)
		key_params = params.get(4)
		exclude_list = params.get(5) or []
		extensions = params.get(6) or {}
		options = params.get(7) or {}

		if not isinstance(client_data_hash, bytes) or not isinstance(rp, dict) \
				or not isinstance(user, dict) or not isinstance(key_params, list):
			raise CtapError(ERR.MISSING_PARAMETER)
		_check_pin_uv_auth_param(params.get(8))

		rp_id = rp.get("id")
		user_id = user.get("id")
		if not isinstance(rp_id, str) or not isinstance(user_id, bytes) or not 1 <= len(user_id) <= 64:
			raise CtapError(ERR.INVALID_PARAMETER)
		log.info("makeCredential rp_id=%s rk=%s", rp_id, bool(options.get("rk", False)))

		if options.get("up") is False:
			raise CtapError(ERR.INVALID_OPTION)

		if not any(p.get("type") == "public-key" and p.get("alg") == ES256.ALGORITHM for p in key_params):
			raise CtapError(ERR.UNSUPPORTED_ALGORITHM)

		cred_protect = extensions.get("credProtect", 1)
		if cred_protect not in (1, 2, 3):
			raise CtapError(ERR.INVALID_OPTION)

		excluded = any(
			isinstance(entry, dict)
			and isinstance(entry.get("id"), bytes)
			and (found := self.store.find_by_id(entry["id"])) is not None
			and found.rp_id == rp_id
			for entry in exclude_list
		)
		if excluded:
			# Prove a user is present before disclosing that the credential
			# already exists on this authenticator
			self._verify_user()
			raise CtapError(ERR.CREDENTIAL_EXCLUDED)

		# Face verification: the single gate in front of key creation
		self._verify_user()

		key_ref, public_key = self.keystore.generate_key()
		cose_key = ES256.from_cryptography_key(public_key)
		credential_id = os.urandom(CREDENTIAL_ID_LENGTH)

		credential = Credential(
			credential_id=credential_id,
			rp_id=rp_id,
			rp_name=rp.get("name") or "",
			user_handle=user_id,
			user_name=user.get("name") or "",
			user_display_name=user.get("displayName") or "",
			cose_public_key=cbor.encode(cose_key),
			key_ref=key_ref,
			resident=bool(options.get("rk", False)),
			cred_protect=cred_protect,
		)
		self.store.add(credential)

		flags = FLAG.UP | FLAG.UV | FLAG.AT
		extension_outputs = None
		if "credProtect" in extensions:
			extension_outputs = {"credProtect": cred_protect}
			flags |= FLAG.ED

		attested = AttestedCredentialData.create(AAGUID, credential_id, cose_key)
		auth_data = AuthenticatorData.create(
			_rp_id_hash(rp_id), flags, 0, bytes(attested),
			extensions=extension_outputs)

		# Packed self attestation: signed by the newly created credential key
		signature = self.keystore.sign(key_ref, bytes(auth_data) + client_data_hash)
		return {
			1: "packed",
			2: bytes(auth_data),
			3: {"alg": ES256.ALGORITHM, "sig": signature},
		}

	def get_assertion(self, params):
		rp_id = params.get(1)
		client_data_hash = params.get(2)
		allow_list = params.get(3)
		options = params.get(5) or {}

		if not isinstance(rp_id, str) or not isinstance(client_data_hash, bytes):
			raise CtapError(ERR.MISSING_PARAMETER)
		_check_pin_uv_auth_param(params.get(6))

		allow_ids = None
		if allow_list is not None:
			allow_ids = [
				entry["id"] for entry in allow_list
				if isinstance(entry, dict) and isinstance(entry.get("id"), bytes)
			]

		credentials = self.store.find_for_rp(rp_id, allow_ids=allow_ids)
		silent = options.get("up") is False
		log.info("getAssertion rp_id=%s allow_list=%s matched=%d silent=%s",
			rp_id, "none" if allow_ids is None else len(allow_ids), len(credentials), silent)
		if not credentials:
			# Note: replying without user interaction lets any client with
			# transport access probe which RPs have credentials, see the
			# threat model in docs/webauthn-design.md
			raise CtapError(ERR.NO_CREDENTIALS)

		if silent:
			# Pre-flight: the platform sets up=false only to discover which
			# credentials exist so it can filter an allow/exclude list. Return
			# an assertion with neither user presence nor verification, run no
			# face check and do not advance the counter. An RP rejects such an
			# assertion (up=0) as a login, so this is not a face-check bypass.
			return self._silent_assertion(credentials[0], client_data_hash)

		# Face verification: the single gate in front of every real signature
		self._verify_user()

		discoverable = allow_ids is None
		responses = [
			self._assert_credential(credential, client_data_hash, discoverable, len(credentials))
			for credential in credentials
		]

		self._next_assertions = responses[1:]
		self._next_assertion_time = time.monotonic()
		return responses[0]

	def _silent_assertion(self, credential, client_data_hash):
		# Pre-flight response: flags 0 (no UP, no UV), counter left untouched.
		auth_data = AuthenticatorData.create(
			_rp_id_hash(credential.rp_id), 0, credential.sign_count)
		signature = self.keystore.sign(credential.key_ref, bytes(auth_data) + client_data_hash)
		return {
			1: {"type": "public-key", "id": credential.credential_id},
			2: bytes(auth_data),
			3: signature,
		}

	def _assert_credential(self, credential, client_data_hash, discoverable, total):
		# Persist the incremented counter before the assertion leaves the
		# authenticator, so a crash cannot repeat a counter value
		sign_count = self.store.increment_counter(credential.credential_id)

		auth_data = AuthenticatorData.create(
			_rp_id_hash(credential.rp_id), FLAG.UP | FLAG.UV, sign_count)
		signature = self.keystore.sign(credential.key_ref, bytes(auth_data) + client_data_hash)

		response = {
			1: {"type": "public-key", "id": credential.credential_id},
			2: bytes(auth_data),
			3: signature,
		}
		if discoverable:
			user = {"id": credential.user_handle}
			if total > 1:
				# Only reveal identifying fields when the client must let the
				# user pick between accounts (user is verified at this point)
				user["name"] = credential.user_name
				user["displayName"] = credential.user_display_name
			response[4] = user
		if total > 1:
			response[5] = total
		return response

	def get_next_assertion(self, params=None):
		if not self._next_assertions:
			raise CtapError(ERR.NOT_ALLOWED)
		if time.monotonic() - self._next_assertion_time > NEXT_ASSERTION_WINDOW:
			self._next_assertions = []
			raise CtapError(ERR.NOT_ALLOWED)

		response = self._next_assertions.pop(0)
		self._next_assertion_time = time.monotonic()
		# numberOfCredentials is only present on the first assertion
		response.pop(5, None)
		return response

	def reset(self, params=None):
		if time.monotonic() - self._started > RESET_WINDOW:
			raise CtapError(ERR.NOT_ALLOWED)
		self._verify_user()
		self.store.destroy_all()
		self.keystore.destroy_all()
		return None

	def selection(self, params=None):
		self._verify_user()
		return None
