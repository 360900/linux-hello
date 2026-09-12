# Credential storage for the WebAuthn authenticator
# Records are stored as root-only JSON, written atomically so a crash can
# never roll a sign counter backwards

import base64
import fcntl
import json
import os
import time
from dataclasses import dataclass, field, asdict

STORE_FORMAT_VERSION = 1


def _b64encode(data):
	return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data):
	return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


@dataclass
class Credential:
	"""One stored WebAuthn credential"""
	credential_id: bytes
	rp_id: str
	rp_name: str
	user_handle: bytes
	user_name: str
	user_display_name: str
	cose_public_key: bytes  # CBOR encoded COSE key
	key_ref: str  # opaque keystore reference, never plaintext key material
	sign_count: int = 0
	resident: bool = True
	cred_protect: int = 1
	algorithm: int = -7
	created_at: float = field(default_factory=time.time)

	def to_dict(self):
		data = asdict(self)
		for key in ("credential_id", "user_handle", "cose_public_key"):
			data[key] = _b64encode(data[key])
		return data

	@classmethod
	def from_dict(cls, data):
		data = dict(data)
		for key in ("credential_id", "user_handle", "cose_public_key"):
			data[key] = _b64decode(data[key])
		return cls(**data)


class StoreError(Exception):
	pass


class CredentialStore:
	"""Credential records for one user, persisted to a single JSON file

	All mutating operations take an exclusive lock on a sidecar lock file so
	the daemon and the CLI can not interleave read-modify-write cycles.
	"""

	def __init__(self, path):
		self.path = path
		self._credentials = []
		self.load()

	def _lock_path(self):
		return self.path + ".lock"

	def _locked(self):
		os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
		lock_file = open(self._lock_path(), "w")
		fcntl.flock(lock_file, fcntl.LOCK_EX)
		return lock_file

	def load(self):
		try:
			with open(self.path, "r") as file:
				data = json.load(file)
		except FileNotFoundError:
			self._credentials = []
			return
		except (ValueError, OSError) as err:
			raise StoreError(f"Corrupt credential store at {self.path}: {err}") from err

		if data.get("version") != STORE_FORMAT_VERSION:
			raise StoreError(f"Unsupported credential store version: {data.get('version')}")

		self._credentials = [Credential.from_dict(c) for c in data.get("credentials", [])]

	def _save(self):
		"""Write the store atomically: temp file, fsync, rename"""
		data = {
			"version": STORE_FORMAT_VERSION,
			"credentials": [c.to_dict() for c in self._credentials],
		}
		directory = os.path.dirname(self.path)
		os.makedirs(directory, mode=0o700, exist_ok=True)
		temp_path = self.path + ".tmp"
		fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
		try:
			with os.fdopen(fd, "w") as file:
				json.dump(data, file, indent="\t")
				file.flush()
				os.fsync(file.fileno())
			os.rename(temp_path, self.path)
		except OSError:
			try:
				os.remove(temp_path)
			except FileNotFoundError:
				pass
			raise

	def add(self, credential):
		with self._locked():
			self.load()
			# Replace an existing credential for the same RP and user handle,
			# like hardware authenticators do for resident credentials
			self._credentials = [
				c for c in self._credentials
				if not (c.rp_id == credential.rp_id and c.user_handle == credential.user_handle)
			]
			self._credentials.append(credential)
			self._save()

	def find_by_id(self, credential_id):
		for credential in self._credentials:
			if credential.credential_id == credential_id:
				return credential
		return None

	def find_for_rp(self, rp_id, allow_ids=None):
		"""Credentials for an RP, newest first, optionally filtered by an allow list"""
		matches = [c for c in self._credentials if c.rp_id == rp_id]
		if allow_ids is not None:
			allowed = set(allow_ids)
			matches = [c for c in matches if c.credential_id in allowed]
		else:
			# Empty allow list means discoverable credentials only
			matches = [c for c in matches if c.resident]
		return sorted(matches, key=lambda c: c.created_at, reverse=True)

	def increment_counter(self, credential_id):
		"""Increment and persist a sign counter, returns the new value

		Persisted before the assertion is released, so a crash can skip
		counter values but never repeat one.
		"""
		with self._locked():
			self.load()
			credential = self.find_by_id(credential_id)
			if credential is None:
				raise StoreError("Unknown credential")
			credential.sign_count += 1
			self._save()
			return credential.sign_count

	def remove(self, credential_id):
		with self._locked():
			self.load()
			before = len(self._credentials)
			self._credentials = [c for c in self._credentials if c.credential_id != credential_id]
			if len(self._credentials) == before:
				return False
			self._save()
			return True

	def list_all(self):
		return list(self._credentials)

	def destroy_all(self):
		with self._locked():
			self._credentials = []
			try:
				os.remove(self.path)
			except FileNotFoundError:
				pass
