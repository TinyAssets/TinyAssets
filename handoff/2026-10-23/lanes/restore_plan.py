"""Rebuild the last live hotfix bytes per container from the ledger and the staging copies. Read-only.

usage: sudo python3 restore_plan.py   -> prints a plan and writes /root/restore-plan.json
"""
import glob
import hashlib
import json
import os
import pathlib

LEDGER = pathlib.Path("/var/lib/tinyassets-deploy/hotfixes")
ROOTS = sorted(glob.glob("/root/hf*") + glob.glob("/root/hotfix-*") + glob.glob("/root/hf-*"))


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


# Index every staged file by its sha256.
by_sha = {}
for root in ROOTS:
    for dirpath, _, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            try:
                by_sha.setdefault(sha(p), p)
            except OSError:
                pass

latest = {}
for path in sorted(LEDGER.glob("*.json")):
    r = json.loads(path.read_text())
    files = r.get("files") or {}
    if isinstance(files, list):
        files = {e["path"]: e.get("after_sha256") for e in files}
    if r.get("result") == "reverted":
        continue
    for container in r.get("containers") or []:
        for rel, digest in files.items():
            key = (container, rel)
            when = r.get("applied_at") or ""
            if key not in latest or when >= latest[key]["when"]:
                latest[key] = {"when": when, "sha": digest, "id": r.get("id")}

plan, missing = [], []
for (container, rel), item in sorted(latest.items()):
    src = by_sha.get(item["sha"])
    entry = {"container": container, "path": rel, "sha": item["sha"], "id": item["id"], "source": src}
    (plan if src else missing).append(entry)
json.dump(plan, open("/root/restore-plan.json", "w"), indent=1)
for e in plan:
    print("OK  ", e["container"], e["path"], e["id"])
for e in missing:
    print("MISS", e["container"], e["path"], e["id"], e["sha"][:12])
print(len(plan), "restorable,", len(missing), "missing")
