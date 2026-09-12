# Keystore backends for WebAuthn credential private keys
# Private keys are never written to disk in plaintext by any backend

import abc
import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KeyStoreError(Exception):
	pass


class KeyStore(abc.ABC):
	"""Creates and uses ES256 (ECDSA P-256/SHA-256) signing keys

	key_ref is an opaque string stored with the credential record; it must
	never contain plaintext private key material.
	"""

	# Human readable backend name shown by `linux-hello-cli webauthn status`
	kind = "abstract"

	@abc.abstractmethod
	def generate_key(self):
		"""Create a new P-256 key, returns (key_ref, public_key)

		public_key is a cryptography EllipticCurvePublicKey.
		"""

	@abc.abstractmethod
	def sign(self, key_ref, message):
		"""Sign message with the key behind key_ref, returns a DER ECDSA signature"""

	@abc.abstractmethod
	def delete_key(self, key_ref):
		"""Forget the key behind key_ref, missing keys are ignored"""

	@abc.abstractmethod
	def destroy_all(self):
		"""Remove all key material, used by authenticatorReset"""


class SoftwareKeyStore(KeyStore):
	"""DEVELOPMENT keystore: keys encrypted at rest with a local master key

	Private keys are AES-256-GCM encrypted, but the master key lives on the
	same disk (root-only file). At-rest protection is therefore no stronger
	than filesystem permissions. Use the TPM keystore where possible.
	"""

	kind = "software (development)"

	MASTER_KEY_FILE = "master.key"

	def __init__(self, directory):
		self.directory = directory
		self._master_key = None

	def _master_key_path(self):
		return os.path.join(self.directory, self.MASTER_KEY_FILE)

	def _load_master_key(self):
		if self._master_key is not None:
			return self._master_key

		path = self._master_key_path()
		try:
			with open(path, "rb") as file:
				key = file.read()
		except FileNotFoundError:
			os.makedirs(self.directory, mode=0o700, exist_ok=True)
			key = AESGCM.generate_key(bit_length=256)
			fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
			with os.fdopen(fd, "wb") as file:
				file.write(key)

		if len(key) != 32:
			raise KeyStoreError("Corrupt software keystore master key")

		self._master_key = key
		return key

	def generate_key(self):
		private_key = ec.generate_private_key(ec.SECP256R1())
		plaintext = private_key.private_bytes(
			serialization.Encoding.DER,
			serialization.PrivateFormat.PKCS8,
			serialization.NoEncryption())

		nonce = os.urandom(12)
		key_id = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
		ciphertext = AESGCM(self._load_master_key()).encrypt(nonce, plaintext, key_id.encode())

		key_ref = json.dumps({
			"backend": "software",
			"id": key_id,
			"nonce": base64.b64encode(nonce).decode(),
			"blob": base64.b64encode(ciphertext).decode(),
		})
		return key_ref, private_key.public_key()

	def _decrypt(self, key_ref):
		try:
			ref = json.loads(key_ref)
			nonce = base64.b64decode(ref["nonce"])
			blob = base64.b64decode(ref["blob"])
			aad = ref["id"].encode()
		except (ValueError, KeyError) as err:
			raise KeyStoreError("Malformed software key reference") from err

		try:
			plaintext = AESGCM(self._load_master_key()).decrypt(nonce, blob, aad)
		except InvalidTag as err:
			raise KeyStoreError("Failed to unwrap software key") from err

		return serialization.load_der_private_key(plaintext, password=None)

	def sign(self, key_ref, message):
		private_key = self._decrypt(key_ref)
		return private_key.sign(message, ec.ECDSA(hashes.SHA256()))

	def delete_key(self, key_ref):
		# Key material lives inside the (deleted) credential record itself
		pass

	def destroy_all(self):
		try:
			os.remove(self._master_key_path())
		except FileNotFoundError:
			pass
		self._master_key = None
