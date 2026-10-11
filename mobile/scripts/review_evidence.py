"""Encrypt private App Review evidence before publishing public Actions artifacts.

Decrypt locally with the lead's private key (never present in CI):
  python mobile/scripts/review_evidence.py decrypt evidence.enc private-output
No additional GitHub secret or AWS storage permission is required.
"""

from __future__ import annotations

import argparse
import io
import os
import zipfile
from pathlib import Path

MAGIC = b"TINYASSETS-REVIEW-2\n"
RECIPIENT = Path(__file__).resolve().parents[1] / "review-evidence-recipient.pub"


def private_key_path() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".tinyassets/review-evidence.key"


def recipient_key(path: Path):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        public = load_pem_public_key(path.read_bytes())
        if not isinstance(public, X25519PublicKey):
            raise ValueError("Expected X25519")
        X25519PrivateKey.generate().exchange(public)  # Reject low-order keys as well.
        return public
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Replace the recipient placeholder with the lead's X25519 public key"
        ) from exc


def key(shared: bytes, salt: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=MAGIC + ephemeral + recipient,
    ).derive(shared)


def seal(source: Path, target: Path, recipient: Path = RECIPIENT) -> None:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    public = recipient_key(recipient)
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise ValueError("No evidence to encrypt")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(source).as_posix())
    salt, nonce = os.urandom(16), os.urandom(12)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes_raw()
    header = MAGIC + ephemeral_public + salt + nonce
    aes_key = key(ephemeral.exchange(public), salt, ephemeral_public, public.public_bytes_raw())
    payload = AESGCM(aes_key).encrypt(nonce, archive.getvalue(), header)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + payload)


def unseal(source: Path, target: Path, private_key: Path | None = None) -> None:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private = load_pem_private_key((private_key or private_key_path()).read_bytes(), password=None)
    if not isinstance(private, X25519PrivateKey):
        raise ValueError("Expected X25519 private key")
    raw = source.read_bytes()
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 60 + 16:
        raise ValueError("Not TinyAssets review evidence")
    body = raw[len(MAGIC) :]
    ephemeral, salt, nonce = body[:32], body[32:48], body[48:60]
    aes_key = key(
        private.exchange(X25519PublicKey.from_public_bytes(ephemeral)),
        salt,
        ephemeral,
        private.public_key().public_bytes_raw(),
    )
    plaintext = AESGCM(aes_key).decrypt(nonce, body[60:], raw[: len(MAGIC) + 60])
    with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
        for item in archive.infolist():
            destination = (target / item.filename).resolve()
            if not destination.is_relative_to(target.resolve()) or item.is_dir():
                raise ValueError("Invalid evidence path")
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            raise ValueError("Choose an empty private output directory")
        archive.extractall(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["encrypt", "decrypt", "validate-recipient"])
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("target", type=Path, nargs="?")
    args = parser.parse_args()
    try:
        if args.operation == "validate-recipient":
            recipient_key(RECIPIENT)
            print("Evidence recipient validated")
            return 0
        if args.source is None or args.target is None:
            parser.error("source and target are required")
        if args.operation == "encrypt":
            seal(args.source, args.target)
        else:
            unseal(args.source, args.target)
        print(f"Evidence {args.operation}ed successfully")
        return 0
    except Exception as exc:
        print(f"::error::Evidence {args.operation} failed ({type(exc).__name__})")
        if args.operation in {"encrypt", "validate-recipient"}:
            print(
                "Ensure mobile/review-evidence-recipient.pub contains the lead's X25519 public key"
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
