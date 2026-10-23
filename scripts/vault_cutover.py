"""Offline shared broker custody conversion; stop writers and verify a backup first."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.broker.vault_backup import validate_backup
from tinyassets.broker.vault_cutover import convert  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--legacy-browser-keys", type=Path, required=True)
    parser.add_argument("--offline-backup-verified", action="store_true", required=True)
    args = parser.parse_args()
    count = convert(args.root, legacy_browser_keys=args.legacy_browser_keys)
    validate_backup(args.root.resolve())
    print(f"Verified sealed custody documents: {count}")
