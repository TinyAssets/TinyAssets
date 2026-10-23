"""Founder-approved (2026-10-23) archive of live hotfix records superseded by PR #5227.

usage: sudo python3 archive_hotfixes.py <merge sha> [--apply]
Each active record keeps its files and .orig/ originals; it gains status "archived", the
merge sha, and where its behavior lives. Without --apply it only prints what it would do.
"""
import datetime as dt
import json
import pathlib
import sys

STATE = pathlib.Path("/var/lib/tinyassets-deploy/hotfixes")
WHERE = {
    "hf-5206-first-admission": "commit 85fbbfe56d3 in #5227 (via #5210)",
    "hf-5216-desktop-open": "commit d7b058c4867 in #5227 (via #5210)",
    "hf-public-host": "commit d112295cc83 in #5227 (via #5210)",
    "hf-resume-restart": "commit d2eea2ee5fa in #5227 (via #5210)",
    "hf-seal-broker": "commit b09d554a4d8 in #5227",
    "hf-5220-subs-broker2": "commit f5d139296e8 in #5227, reshaped to broker custody (d89677c3b13)",
    "hf-5220-subs-daemon2": "commit f5d139296e8 in #5227, reshaped to broker custody (d89677c3b13)",
    "hf-address-flow-broker": "commit c48a490ad34 in #5227, discovery moved into the box (e3fbe113595)",
    "hf-address-flow-daemon": "commit c48a490ad34 in #5227, discovery moved into the box (e3fbe113595)",
    "hf-5218-box-url": "boxhostd.env UNIVERSE_SERVER_URL rendered by #5227 deploy config",
    "hf-broker-http-enabled": "broker and daemon share the outbound HTTP flag in #5227 deploy config",
    "hf-accept-live-only-daemon": "replaced by explicit disconnect departure (dcbadec7295) and one-shot repair",
    "hf-answer-line-daemon": "folded in 9ab39b4b886",
    "hf-select-copy-daemon": "folded in 9ab39b4b886",
    "hf-connected-notice-daemon": "folded in d542bb8a929",
    "hf-free-storage-5-broker": "folded in d542bb8a929",
    "hf-free-storage-5-daemon": "folded in d542bb8a929",
    "hf-disconnect-daemon": "3a21c2dfa3f, 45569df8279 and broker custody in #5227",
    "hf-review-menu-broker": "554912790a1 and 6c141d70b93 in #5227",
    "hf-review-menu-daemon": "554912790a1 and 6c141d70b93 in #5227",
    "hf-sub-progress-daemon": "superseded: CLIs ship in the box image (7d55a06f589), no install wait",
    "hf-5219b-one-tap": "superseded by the address field (d90a5e23b32); PR #5219 closed",
    "hf-broker-diag": "live diagnostic, dropped deliberately",
    "hf-broker-run-diag": "live diagnostic, dropped deliberately",
    "hf-diag-detail": "live diagnostic, dropped deliberately",
}

merge, apply = sys.argv[1], "--apply" in sys.argv
now = dt.datetime.now(dt.timezone.utc).isoformat()
missing = []
for path in sorted(STATE.glob("*.json")):
    record = json.loads(path.read_text())
    status = record.get("status") or ("active" if record.get("active") else "reverted")
    if status not in {"active", "pending"}:
        continue
    rid = record.get("id")
    if rid not in WHERE:
        missing.append(rid)
        continue
    print(f"{rid}: archive -> {WHERE[rid]}")
    if apply:
        record.update(status="archived", active=False, archived_at=now,
                      superseded_by={"pr": 5227, "merge": merge, "where": WHERE[rid],
                                     "approved": "founder 2026-10-23"})
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=1))
        tmp.replace(path)
if missing:
    print("NOT IN THE APPROVED TABLE, left active:", ", ".join(map(str, missing)))
    sys.exit(2)
