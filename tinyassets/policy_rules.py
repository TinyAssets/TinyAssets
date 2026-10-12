"""Read-only rule projection; every decision comes from the broker policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tinyassets.broker.client import owner_policy_operation
from tinyassets.broker.owner_identities import admitted_owner
from tinyassets.broker.policy import BEHAVIOURS

DO, DO_IF_PREAPPROVED, ASK_FIRST, HAND_OFF = BEHAVIOURS
MAIN_AGENT = "main"
ACTION_CLASSES = {
    "workspace.files": "Reading and changing files in its own workspace",
    "workspace.shell": "Running commands in its own workspace",
    "workspace.workflows": "Creating, editing and running its own workflows",
    "shell.egress": "Using the public internet from its shell",
    "app.read": "Reading from a connected app",
    "app.write": "Changing something in a connected app",
    "people.message": "Sending a message to a person",
    "commons.publish": "Publishing to the commons",
    "share": "Sharing your command center or its work with someone",
    "spend": "Spending money over a budget you set",
    "browser.action": "Submitting a form or pressing a purchase button in its browser",
    "money.move": "Moving money or making a payment",
    "security.change": "Changing a password, credential or security setting",
    "access.grant": "Giving another person access to your accounts or data",
}


@dataclass(frozen=True, slots=True)
class Decision:
    behaviour: str
    rule_id: int | None
    reason: str

def projection(home: Path) -> dict:
    home = Path(home)
    owner = admitted_owner(home.parent, center=home.name)
    return owner_policy_operation(home.parent, owner, "read")


def entries(home: Path, table: str) -> list[dict]:
    document = projection(home)["policy"]
    return [entry for entry in document["entries"]
            if entry["table"] == table and entry["scope"] in ("account", Path(home).name)]


def standing_grants(home: Path, agent: str = MAIN_AGENT) -> list[dict]:
    grants = []
    for entry in entries(home, "rules") + entries(home, "approval_grants"):
        values = entry["values"]
        if (values.get("agent", MAIN_AGENT) != agent
                or values.get("record_kind", "behavior") != "preapproval"):
            continue
        grant = json.loads(values.get("grant_json") or "{}")
        if grant and not grant.get("revoked"):
            grants.append(grant)
    return grants


def configured_agents(home: Path) -> set[str]:
    return {entry["values"].get("agent", MAIN_AGENT)
            for entry in entries(home, "rules") + entries(home, "review_on")}


def decide(home: Path, action_class: str, *, connection: str = "",
           operation: str = "", agent: str = MAIN_AGENT,
           destination: str = "", amount: int = 0, source: str = "",
           request_id: str = "") -> Decision:
    home = Path(home)
    owner = admitted_owner(home.parent, center=home.name)
    result = owner_policy_operation(home.parent, owner, "decide", request={
        "center": home.name, "action_class": action_class, "connection": connection,
        "operation": operation, "destination": destination, "amount": amount,
        "source": source, "agent": agent, "request_id": request_id})
    behavior = {"allow": result["behaviour"], "hold": DO_IF_PREAPPROVED,
                "ask": ASK_FIRST, "deny": HAND_OFF}[result["decision"]]
    return Decision(behavior, None, result["rule"])


def classify(home: Path, connection: str, method: str, path: str) -> tuple[str, str]:
    home = Path(home)
    owner = admitted_owner(home.parent, center=home.name)
    result = owner_policy_operation(home.parent, owner, "classify", center=home.name,
                                    connection=connection, method=method, path=path)
    return result["action_class"], result["operation"]
