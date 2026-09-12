# TPM 2.0 keystore backend
# Credential keys are created inside the TPM under a primary storage key.
# Only TPM-wrapped blobs touch the disk, signing happens inside the TPM.

import base64
import json

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from webauthn.keystore import KeyStore, KeyStoreError

# The tpm2_pytss dependency is optional, probe for it once at import time
try:
	from tpm2_pytss import (
		ESAPI,
		ESYS_TR,
		TPM2B_DATA,
		TPM2B_DIGEST,
		TPM2B_PRIVATE,
		TPM2B_PUBLIC,
		TPM2B_SENSITIVE_CREATE,
		TPM2_ALG,
		TPM2_RH,
		TPM2_ST,
		TPML_PCR_SELECTION,
		TPMT_SIG_SCHEME,
		TPMT_TK_HASHCHECK,
	)
	from tpm2_pytss.constants import TPMA_OBJECT
	TPM_AVAILABLE = True
except ImportError:
	TPM_AVAILABLE = False

# Template of the storage primary the credential keys live under. The primary
# is derived deterministically from the TPM owner seed and this template, so
# it does not need to be persisted anywhere.
PRIMARY_TEMPLATE = "ecc256:null:aes128cfb"
# Signing scheme of the per credential keys
KEY_TEMPLATE = "ecc256:ecdsa-sha256:null"


def tpm_is_usable():
	"""Check whether a TPM 2.0 can actually be reached"""
	if not TPM_AVAILABLE:
		return False
	try:
		with ESAPI() as ectx:
			ectx.get_random(4)
		return True
	except Exception:
		return False


class TpmKeyStore(KeyStore):
	"""Keys created and used inside the TPM, wrapped blobs stored on disk"""

	kind = "tpm"

	def __init__(self, directory=None):
		# The directory is unused: all state lives in the credential records
		# (wrapped blobs) and inside the TPM itself
		if not TPM_AVAILABLE:
			raise KeyStoreError("tpm2_pytss is not installed")
		self._ectx = None
		self._primary = None

	def _context(self):
		if self._ectx is None:
			try:
				self._ectx = ESAPI()
			except Exception as err:
				raise KeyStoreError(f"Could not connect to the TPM: {err}") from err
		return self._ectx

	def _primary_handle(self):
		"""Create (or re-derive) the storage primary key, cached per process"""
		if self._primary is not None:
			return self._primary

		ectx = self._context()
		in_public = TPM2B_PUBLIC.parse(
			PRIMARY_TEMPLATE,
			objectAttributes=TPMA_OBJECT.RESTRICTED
			| TPMA_OBJECT.DECRYPT
			| TPMA_OBJECT.FIXEDTPM
			| TPMA_OBJECT.FIXEDPARENT
			| TPMA_OBJECT.SENSITIVEDATAORIGIN
			| TPMA_OBJECT.USERWITHAUTH
			| TPMA_OBJECT.NODA,
		)
		try:
			self._primary, _, _, _, _ = ectx.create_primary(
				TPM2B_SENSITIVE_CREATE(),
				in_public,
				ESYS_TR.OWNER,
				TPM2B_DATA(),
				TPML_PCR_SELECTION(),
			)
		except Exception as err:
			raise KeyStoreError(f"TPM CreatePrimary failed: {err}") from err
		return self._primary

	def generate_key(self):
		ectx = self._context()
		primary = self._primary_handle()

		in_public = TPM2B_PUBLIC.parse(
			KEY_TEMPLATE,
			objectAttributes=TPMA_OBJECT.SIGN_ENCRYPT
			| TPMA_OBJECT.FIXEDTPM
			| TPMA_OBJECT.FIXEDPARENT
			| TPMA_OBJECT.SENSITIVEDATAORIGIN
			| TPMA_OBJECT.USERWITHAUTH
			| TPMA_OBJECT.NODA,
		)
		try:
			private, public, _, _, _ = ectx.create(
				primary,
				TPM2B_SENSITIVE_CREATE(),
				in_public,
				TPM2B_DATA(),
				TPML_PCR_SELECTION(),
			)
		except Exception as err:
			raise KeyStoreError(f"TPM Create failed: {err}") from err

		key_ref = json.dumps({
			"backend": "tpm",
			"private": base64.b64encode(private.marshal()).decode(),
			"public": base64.b64encode(public.marshal()).decode(),
		})

		ecc_point = public.publicArea.unique.ecc
		public_numbers = ec.EllipticCurvePublicNumbers(
			int.from_bytes(bytes(ecc_point.x.buffer), "big"),
			int.from_bytes(bytes(ecc_point.y.buffer), "big"),
			ec.SECP256R1(),
		)
		return key_ref, public_numbers.public_key()

	def _load(self, key_ref):
		try:
			ref = json.loads(key_ref)
			if ref.get("backend") != "tpm":
				raise KeyStoreError("Key reference does not belong to the TPM keystore")
			private, _ = TPM2B_PRIVATE.unmarshal(base64.b64decode(ref["private"]))
			public, _ = TPM2B_PUBLIC.unmarshal(base64.b64decode(ref["public"]))
		except (ValueError, KeyError) as err:
			raise KeyStoreError("Malformed TPM key reference") from err

		ectx = self._context()
		try:
			return ectx.load(self._primary_handle(), private, public)
		except Exception as err:
			raise KeyStoreError(f"TPM Load failed: {err}") from err

	def sign(self, key_ref, message):
		import hashlib

		ectx = self._context()
		handle = self._load(key_ref)
		try:
			digest = TPM2B_DIGEST(hashlib.sha256(message).digest())
			scheme = TPMT_SIG_SCHEME(scheme=TPM2_ALG.NULL)
			validation = TPMT_TK_HASHCHECK(tag=TPM2_ST.HASHCHECK, hierarchy=TPM2_RH.NULL)
			try:
				signature = ectx.sign(handle, digest, scheme, validation)
			except Exception as err:
				raise KeyStoreError(f"TPM Sign failed: {err}") from err

			sig = signature.signature.ecdsa
			return encode_dss_signature(
				int.from_bytes(bytes(sig.signatureR.buffer), "big"),
				int.from_bytes(bytes(sig.signatureS.buffer), "big"),
			)
		finally:
			ectx.flush_context(handle)

	def delete_key(self, key_ref):
		# Wrapped blobs live inside the (deleted) credential record, nothing
		# is persisted in the TPM per key
		pass

	def destroy_all(self):
		# Without per key TPM state there is nothing to clear beyond the
		# credential records; the primary is derived, not stored
		pass

	def close(self):
		if self._ectx is not None:
			if self._primary is not None:
				try:
					self._ectx.flush_context(self._primary)
				except Exception:
					pass
				self._primary = None
			self._ectx.close()
			self._ectx = None
