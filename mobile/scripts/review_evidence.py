"""Encrypt private App Review evidence before publishing public Actions artifacts.

Decrypt locally with the existing App Review password, prompted with getpass:
  python mobile/scripts/review_evidence.py decrypt evidence.enc private-output
No additional GitHub secret or AWS storage permission is required.
"""

from __future__ import annotations

import argparse
import getpass
import io
import os
import zipfile
from pathlib import Path

MAGIC = b"TINYASSETS-REVIEW-1\n"


def key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode())


def seal(source: Path, target: Path, password: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not password:
        raise ValueError("App Review password is required for private evidence")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise ValueError("No evidence to encrypt")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(source).as_posix())
    salt, nonce = os.urandom(16), os.urandom(12)
    payload = AESGCM(key(password, salt)).encrypt(nonce, archive.getvalue(), MAGIC)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(MAGIC + salt + nonce + payload)


def unseal(source: Path, target: Path, password: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = source.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("Not TinyAssets review evidence")
    body = raw[len(MAGIC) :]
    plaintext = AESGCM(key(password, body[:16])).decrypt(body[16:28], body[28:], MAGIC)
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
    parser.add_argument("operation", choices=["encrypt", "decrypt"])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "encrypt":
            seal(args.source, args.target, os.environ.get("APP_STORE_REVIEW_PASSWORD", ""))
        else:
            unseal(args.source, args.target, getpass.getpass("App Review password: "))
        print(f"Evidence {args.operation}ed successfully")
        return 0
    except Exception as exc:
        print(f"::error::Evidence {args.operation} failed ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
