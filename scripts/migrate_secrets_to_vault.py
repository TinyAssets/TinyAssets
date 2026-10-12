"""One-shot migrator: plaintext $HOME/workflow-secrets.env → vault.

Reads the existing plaintext file, matches each KEY against the canonical
list in ``scripts/secrets_keys.txt``, and writes each KEY=VALUE pair into
the chosen vault under the convention documented in
``openspec/specs/credential-vault/spec.md``:

  1Password:  item name = KEY; vault = $TINYASSETS_SECRETS_VAULT (default "tinyassets");
              field "password" holds the value.
  Bitwarden:  item name = KEY; organization/folder = "tinyassets"; login.password = value.

Idempotent: if an item already exists, issues an ``op item edit`` /
``bw edit item`` instead of creating a duplicate.

Usage
-----
    python scripts/migrate_secrets_to_vault.py --vendor 1password
    python scripts/migrate_secrets_to_vault.py --vendor bitwarden --dry-run
    python scripts/migrate_secrets_to_vault.py --plaintext ~/workflow-secrets.env \\
        --vendor 1password --vault workflow

Fails loudly on any missing key or vault-CLI error. Leaves the plaintext
file intact — HOST deletes it manually after verifying load_secrets.sh
returns each KEY correctly.

Exit codes
----------
    0  all keys migrated (or --dry-run completed)
    2  plaintext file not readable
    3  keys config not readable
    4  vendor CLI not installed
    5  vendor session not authenticated
    6  one or more migrations failed (partial state possible — report
       lists which succeeded)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_KEYS_FILE = _REPO_ROOT / "scripts" / "secrets_keys.txt"


def _default_plaintext_path() -> Path:
    override = os.environ.get("TINYASSETS_SECRETS_PLAINTEXT_PATH")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / "workflow-secrets.env"


def _read_keys(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"keys config not readable: {path}")
    keys: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys.append(line.split()[0])
    return keys


def parse_plaintext(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from a plaintext env file.

    Pure function — testable without filesystem.
    Strips optional surrounding single/double quotes on values. Skips
    blank + comment lines. Later wins if a key is repeated.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        eq = line.find("=")
        if eq < 1:
            continue
        key = line[:eq].strip()
        value = line[eq + 1:]
        # Strip optional surrounding quotes.
        if len(value) >= 2:
            first, last = value[0], value[-1]
            if (first == '"' and last == '"') or (first == "'" and last == "'"):
                value = value[1:-1]
        out[key] = value
    return out


# ---- 1Password -------------------------------------------------------


def _op_available() -> bool:
    return shutil.which("op") is not None


def _op_signed_in() -> bool:
    try:
        result = subprocess.run(
            ["op", "whoami"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _op_item_exists(key: str, vault: str) -> bool:
    try:
        result = subprocess.run(
            ["op", "item", "get", key, "--vault", vault, "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _op_write(
    key: str, value: str, vault: str, *, exists: bool,
    runner=subprocess.run,
) -> tuple[bool, str]:
    """Create or update a 1Password item. Returns (ok, message)."""
    if exists:
        args = ["op", "item", "edit", key, f"password={value}", "--vault", vault]
    else:
        args = [
            "op", "item", "create",
            "--category", "password",
            "--title", key,
            "--vault", vault,
            f"password={value}",
        ]
    try:
        result = runner(args, capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"op invocation error: {e}"
    if result.returncode != 0:
        return False, f"op exit {result.returncode}: {result.stderr.strip()[:300]}"
    return True, "ok"


# ---- Bitwarden -------------------------------------------------------


def _bw_available() -> bool:
    return shutil.which("bw") is not None


def _bw_unlocked() -> bool:
    try:
        result = subprocess.run(
            ["bw", "status"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    try:
        status = json.loads(result.stdout).get("status", "")
    except json.JSONDecodeError:
        return False
    return status == "unlocked"


def _bw_item_id(key: str) -> str | None:
    """Return the item id for `key`, or None if not found."""
    try:
        result = subprocess.run(
            ["bw", "get", "item", key],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("id")
    except json.JSONDecodeError:
        return None


def _bw_write(
    key: str, value: str, *, item_id: str | None,
    runner=subprocess.run,
) -> tuple[bool, str]:
    """Create or update a Bitwarden item. Returns (ok, message).

    Uses `bw encode` to pass the JSON payload; this avoids quoting
    hazards with special chars in the secret value.
    """
    payload = {
        "type": 1,  # login
        "name": key,
        "login": {"password": value},
        "notes": "Managed by scripts/migrate_secrets_to_vault.py",
    }
    try:
        encoded = runner(
            ["bw", "encode"],
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"bw encode error: {e}"
    if encoded.returncode != 0:
        return False, f"bw encode exit {encoded.returncode}: {encoded.stderr[:200]}"
    encoded_payload = encoded.stdout.strip()

    if item_id:
        args = ["bw", "edit", "item", item_id, encoded_payload]
    else:
        args = ["bw", "create", "item", encoded_payload]

    try:
        result = runner(args, capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"bw invocation error: {e}"
    if result.returncode != 0:
        return False, f"bw exit {result.returncode}: {result.stderr.strip()[:300]}"
    return True, "ok"


# ---- orchestration ---------------------------------------------------


def migrate(
    pairs: dict[str, str],
    keys: list[str],
    vendor: str,
    vault: str,
    *,
    dry_run: bool,
    runner=subprocess.run,
    op_item_exists_fn=_op_item_exists,
    bw_item_id_fn=_bw_item_id,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Migrate `pairs` into `vendor`. Only keys in `keys` are processed.

    Returns (successes, failures) where each element is (key, message).
    Pure-ish: vault I/O goes through injectable ``runner`` +
    ``op_item_exists_fn`` / ``bw_item_id_fn`` seams for unit testing.
    """
    successes: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []

    for key in keys:
        if key not in pairs:
            failures.append((key, "missing from plaintext file"))
            continue
        value = pairs[key]
        if not value:
            failures.append((key, "empty value in plaintext file"))
            continue

        if dry_run:
            successes.append((key, f"DRY-RUN would write {len(value)}-char value"))
            continue

        if vendor == "1password":
            exists = op_item_exists_fn(key, vault)
            ok, msg = _op_write(key, value, vault, exists=exists, runner=runner)
        elif vendor == "bitwarden":
            item_id = bw_item_id_fn(key)
            ok, msg = _bw_write(key, value, item_id=item_id, runner=runner)
        else:
            ok, msg = False, f"unsupported vendor {vendor!r}"

        (successes if ok else failures).append((key, msg))

    return successes, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Migrate plaintext secrets from $HOME/workflow-secrets.env into a vault.",
    )
    ap.add_argument("--plaintext", type=Path, default=None,
                    help="Path to plaintext env file (default: "
                         "$TINYASSETS_SECRETS_PLAINTEXT_PATH or $HOME/workflow-secrets.env).")
    ap.add_argument("--vendor", required=True, choices=["1password", "bitwarden"],
                    help="Which vault backend to write to.")
    ap.add_argument("--vault", default=os.environ.get("TINYASSETS_SECRETS_VAULT", "tinyassets"),
                    help="Vault name (1Password only; default 'workflow').")
    ap.add_argument("--keys-file", type=Path, default=_KEYS_FILE,
                    help=f"Canonical keys list (default: {_KEYS_FILE}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Decide what would happen; do not write to the vault.")
    args = ap.parse_args(argv)

    plaintext_path = args.plaintext or _default_plaintext_path()
    if not plaintext_path.is_file():
        print(f"[migrate] plaintext file not readable: {plaintext_path}", file=sys.stderr)
        return 2

    try:
        keys = _read_keys(args.keys_file)
    except FileNotFoundError as e:
        print(f"[migrate] {e}", file=sys.stderr)
        return 3
    if not keys:
        print(f"[migrate] keys config empty: {args.keys_file}", file=sys.stderr)
        return 3

    pairs = parse_plaintext(plaintext_path.read_text(encoding="utf-8"))

    # Check CLI availability before writing anything (faster failure).
    if not args.dry_run:
        if args.vendor == "1password":
            if not _op_available():
                print("[migrate] 1Password CLI 'op' not installed. "
                      "Install: https://developer.1password.com/docs/cli/get-started/",
                      file=sys.stderr)
                return 4
            if not _op_signed_in():
                print("[migrate] 1Password session not authenticated. Run: eval $(op signin)",
                      file=sys.stderr)
                return 5
        elif args.vendor == "bitwarden":
            if not _bw_available():
                print("[migrate] Bitwarden CLI 'bw' not installed. "
                      "Install: https://bitwarden.com/help/cli/",
                      file=sys.stderr)
                return 4
            if not _bw_unlocked():
                print("[migrate] Bitwarden vault not unlocked. "
                      "Run: export BW_SESSION=$(bw unlock --raw)",
                      file=sys.stderr)
                return 5

    successes, failures = migrate(
        pairs, keys, args.vendor, args.vault, dry_run=args.dry_run,
    )

    print(f"[migrate] vendor={args.vendor} vault={args.vault} "
          f"dry_run={args.dry_run}", file=sys.stderr)
    for key, msg in successes:
        print(f"  OK    {key}: {msg}")
    for key, msg in failures:
        print(f"  FAIL  {key}: {msg}", file=sys.stderr)

    if failures:
        print(f"[migrate] {len(failures)} failure(s); {len(successes)} success(es)",
              file=sys.stderr)
        return 6

    print(f"[migrate] all {len(successes)} key(s) migrated successfully.",
          file=sys.stderr)
    if not args.dry_run:
        print(f"[migrate] NEXT: verify via 'scripts/load_secrets.sh' then "
              f"delete {plaintext_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
