#!/usr/bin/env python3
"""Install the read-only log command and, when a key is supplied, its forced command.

Run as root from the host installer. The script is copied to a stable path.
``authorized_keys`` is rewritten only when that file already exists and already
holds a key. A missing file is not created: an empty authorized_keys would
lock out the deploy key the moment sshd starts using it.
"""

from __future__ import annotations

import argparse
import os
import pwd
import re
import sys
from pathlib import Path

SCRIPT_DEST = Path("/usr/local/libexec/tinyassets-read-logs")
COMMAND = 'command="/usr/local/libexec/tinyassets-read-logs"'
_USER = re.compile(r"^[A-Za-z0-9._-]+$")
_PUBKEY = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) "
    r"[A-Za-z0-9+/=]+(?: [A-Za-z0-9@._:/+-]{1,80})?$"
)


def valid_pubkey(line: str) -> bool:
    return bool(_PUBKEY.fullmatch(line.strip())) and COMMAND not in line


def forced_command_line(pubkey: str) -> str:
    if not valid_pubkey(pubkey):
        raise ValueError("invalid public key")
    return (
        "restrict," + COMMAND + ","
        "no-port-forwarding,no-agent-forwarding,no-X11-forwarding,"
        "no-pty,no-user-rc " + pubkey.strip()
    )


def rewrite_authorized_keys(text: str, pubkey: str) -> str | None:
    """New file contents, or None when the file must stay untouched."""
    lines = text.splitlines()
    keys = [
        line for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not keys:
        return None
    kept = [line for line in lines if COMMAND not in line]
    kept.append(forced_command_line(pubkey))
    return "\n".join(kept) + "\n"


def install_script(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = source.read_bytes()
    temporary = dest.with_name(dest.name + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o755)
    os.chown(temporary, 0, 0)
    os.replace(temporary, dest)


def install_key(path: Path, pubkey: str, uid: int, gid: int) -> str:
    """Update one authorized_keys file. Returns a short result name."""
    if path.is_symlink():
        raise ValueError("authorized_keys is a symlink")
    if not path.is_file():
        return "absent"
    current = path.read_text(encoding="utf-8", errors="replace")
    updated = rewrite_authorized_keys(current, pubkey)
    if updated is None:
        return "empty"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.chown(temporary, uid, gid)
    os.replace(temporary, path)
    path.chmod(0o600)
    return "updated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--pubkey-file", default="")
    parser.add_argument("--dest", default=str(SCRIPT_DEST))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("install-log-reader must run as root", file=sys.stderr)
        return 1
    if not _USER.fullmatch(args.user):
        print("invalid user", file=sys.stderr)
        return 1
    try:
        entry = pwd.getpwnam(args.user)
    except KeyError:
        print("unknown user", file=sys.stderr)
        return 1
    source = Path(args.script)
    if not source.is_file():
        print("reader script is missing", file=sys.stderr)
        return 1
    install_script(source, Path(args.dest))
    if not args.pubkey_file:
        print("reader script installed; dedicated read key not configured")
        print(
            "Optional later hardening: DO_LOG_READ_SSH_PUBKEY installs a forced "
            "command on a later Install host services run. Fetch production logs "
            "uses the deploy key and does not need it."
        )
        return 0
    pubkey_path = Path(args.pubkey_file)
    pubkey = pubkey_path.read_text(encoding="utf-8").strip()
    if not valid_pubkey(pubkey):
        print("public key is not an acceptable ssh key line", file=sys.stderr)
        return 1
    authorized = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
    try:
        result = install_key(authorized, pubkey, entry.pw_uid, entry.pw_gid)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if result == "updated":
        print("reader script installed and forced-command key updated")
        return 0
    print(
        "reader script installed; authorized_keys was left unchanged "
        f"({result}). The deploy key file must already exist."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
