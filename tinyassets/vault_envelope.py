"""AES-GCM primitive shared by the broker root wrapper and custody envelopes."""
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class EnvelopeError(ValueError):
    """Authentication failed; no plaintext or key material in the error."""


def gcm_seal(
    key: bytes, plaintext: bytes, aad: bytes, *, nonce: bytes | None = None
) -> bytes:
    """One AES-GCM layout: 12-byte nonce, then ciphertext. No other copy."""
    import secrets

    if len(key) != KEY_BYTES:
        raise EnvelopeError("data key is not 32 bytes")
    nonce = secrets.token_bytes(NONCE_BYTES) if nonce is None else nonce
    if len(nonce) != NONCE_BYTES:
        raise EnvelopeError("nonce is not 96 bits")
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def gcm_open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(key) != KEY_BYTES or len(blob) <= NONCE_BYTES:
        raise EnvelopeError("credential vault authentication failed")
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except (InvalidTag, ValueError):
        raise EnvelopeError("credential vault authentication failed") from None
