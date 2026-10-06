"""Owner-editable starter workflow helper; run with bash in the owner's /u.

This file is package content, never imported by the platform. Automations of
the same workflow must not overlap. Keep one installed workflow per mode.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def moment(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Dates must include a UTC offset")
    return parsed


def keyed(rows):
    result = {}
    for row in rows:
        key = row["id"]
        if not isinstance(key, str) or not key or key in result:
            raise ValueError("Records need unique nonempty string IDs")
        result[key] = row
    return result


def notify(title, body):
    args = {"target": "pending_request", "operation": "notify",
            "payload_json": json.dumps({"title": title, "body": body})}
    result = subprocess.run(
        ["ta", "write_graph", "--json", json.dumps(args)],
        check=True, capture_output=True, text=True,
    )
    receipt = json.loads(result.stdout)
    if isinstance(receipt, str):
        receipt = json.loads(receipt)
    return receipt


def check(root, mode, *, now=None, send=notify, acknowledge=False):
    """Read fresh owner files, notify on a meaningful transition, then ack.

    A notify receipt means an owner inbox item exists, not that a device received
    push. Preserve the delivery report in the return value for the agent.
    """
    now = now or datetime.now(timezone.utc)
    folder = Path(root) / "starter"
    dial = read_json(folder / "settings.json")["proactivity"]
    if dial not in ("off", "low", "high"):
        raise ValueError("proactivity must be off, low or high")
    state_path = folder / f"{mode}-state.json"
    previous = read_json(state_path) if state_path.exists() else None
    state = dict(previous or {})
    updates = []
    if mode == "goals":
        rows = keyed(read_json(folder / "goals.json")["goals"])
        fields = ("title", "status", "next_step", "priority", "due")
        state = {}
        for key, row in rows.items():
            if row["status"] not in ("active", "blocked", "done", "cancelled"):
                raise ValueError("Unknown goal status")
            current = {field: row.get(field, "") for field in fields}
            state[key] = current
            old = (previous or {}).get(key)
            important = (current["status"] in ("blocked", "done")
                         and (old or {}).get("status") != current["status"])
            if (previous is not None and current != old
                    and (dial == "high" or (dial == "low" and important))):
                updates.append(f"{row['title']}: {row['status']}. {row.get('next_step', '')}")
    elif mode in ("monitors", "reminders"):
        rows = keyed(read_json(folder / "monitors.json")[mode])
        for key, row in rows.items():
            if row.get("active") is not True or row.get("requested") is not True:
                continue
            if mode == "reminders":
                token = row["due_at"]
                if now < moment(token):
                    continue
            else:
                if row.get("matched") is not True:
                    continue
                token = row["change_id"]
                age = (now - moment(row["observed_at"])).total_seconds()
                if not isinstance(token, str) or not token or not 0 <= age <= 3600:
                    raise ValueError("A match needs a stable change_id and fresh observation")
            if state.get(key) != token:
                summary = row["summary"]
                if not isinstance(summary, str) or not summary.strip():
                    raise ValueError("A notification needs a summary")
                updates.append(summary)
                state[key] = token
    else:
        raise ValueError("Unknown check-in mode")

    receipt = None
    suppressed = None
    if updates and not acknowledge:
        body = "\n".join(updates)
        if len(body) > 8000:
            source = "goals.json" if mode == "goals" else "monitors.json"
            body = body[:7800] + f"\n[Summary shortened; full details in starter/{source}.]"
        receipt = send(f"Starter {mode} update", body)
        if isinstance(receipt, dict) and receipt.get("settled") is True:
            suppressed = receipt.get("decision") or "declined"
        elif (not isinstance(receipt, dict) or receipt.get("error")
              or not receipt.get("request_id")):
            raise RuntimeError(f"Notification not acknowledged: {receipt}")
    # Atomic acknowledgement; a failed notification never consumes the transition.
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)
    return {"notified": receipt is not None and suppressed is None,
            "suppressed": suppressed, "changes": len(updates), "delivery": receipt}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("goals", "monitors", "reminders"))
    parser.add_argument("--acknowledge", action="store_true")
    options = parser.parse_args()
    if options.acknowledge and options.mode != "goals":
        parser.error("--acknowledge is for goals already discussed with the owner")
    print(json.dumps(check(Path.cwd(), options.mode, acknowledge=options.acknowledge)))
