"""Create a local owner-only evidence key without overwriting; stdout is ONLY public PEM."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # CreateNew + a protected DACL are atomic: no inherited read access,
        # no interval with a world-readable key, and no overwrite of any key.
        command = r"""
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [Security.AccessControl.FileSecurity]::new()
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
$stream = [IO.FileStream]::new($env:TA_REVIEW_KEY_PATH, [IO.FileMode]::CreateNew,
    [Security.AccessControl.FileSystemRights]::Write, [IO.FileShare]::None,
    4096, [IO.FileOptions]::None, $acl)
try {
    $data = [Text.Encoding]::ASCII.GetBytes([Console]::In.ReadToEnd())
    $stream.Write($data, 0, $data.Length)
    $stream.Flush($true)
} finally { $stream.Dispose() }
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            input=data,
            capture_output=True,
            env={**os.environ, "TA_REVIEW_KEY_PATH": str(path.resolve())},
            check=False,
        )
        if result.returncode:
            # Do not forward subprocess output, even on failure.
            raise OSError("Secure key creation failed; existing keys are never overwritten")
    else:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)


def generate(path: Path) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private = X25519PrivateKey.generate()
    write_private(path, private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def main() -> int:
    path = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".tinyassets/review-evidence.key"
    try:
        public = generate(path)
    except Exception as exc:
        print(f"Key generation failed ({type(exc).__name__}); no key overwritten", file=sys.stderr)
        return 1
    sys.stdout.write(public.decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
