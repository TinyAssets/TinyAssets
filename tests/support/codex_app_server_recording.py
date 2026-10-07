"""A real codex-cli 0.160.0 app-server turn, as a ScriptedAppServer script.

``tests/fixtures/codex_app_server_0160_turn.jsonl`` holds, verbatim and in
order, every message the real CLI wrote after answering ``turn/start``: the
CLI was launched with the served contract (``codex_launch_contract``) against a
loopback Responses endpoint that asked for one call to the dynamic tool
``read`` and then replied. No credential or network was used (2026-10-06; the
recording procedure is in the K2 implementation evidence).
"""

from __future__ import annotations

import json
from pathlib import Path

RECORDING = Path(__file__).resolve().parents[1] / "fixtures" / "codex_app_server_0160_turn.jsonl"


def recorded_turn() -> tuple[list[tuple[float, dict]], dict]:
    """The script (zero delays) and the recorded ``item/tool/call`` params."""
    messages = [json.loads(line) for line in RECORDING.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    (call,) = [m for m in messages if m.get("method") == "item/tool/call"]
    return [(0, message) for message in messages], call["params"]
