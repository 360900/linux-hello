# Linux Hello WebAuthn: optional FIDO2/WebAuthn authenticator gated on face
# verification. Based on PR #1125 by qilsklo
# (https://github.com/boltgolt/howdy/pull/1125)
# See docs/webauthn-design.md for the architecture

# Freshly generated AAGUID for the Linux Hello virtual authenticator
# (self-asserted model identifier, random 16 bytes)
AAGUID = bytes.fromhex("14647b7a47b0db542750f30f0ceb251c")


def create_keystore(kind, directory):
	"""Instantiate a keystore backend by its configured kind"""
	if kind == "tpm":
		from webauthn.keystore_tpm import TpmKeyStore
		return TpmKeyStore(directory)
	from webauthn.keystore import SoftwareKeyStore
	return SoftwareKeyStore(directory)
