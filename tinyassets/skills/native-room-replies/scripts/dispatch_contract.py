"""Local preparation only: pure room-to-native-agent dispatch rules.

Not an authority boundary or live installer. The future workflow must obtain
receipt and room data through its existing owner-admitted run, never caller
assertions. No provider, credential, network, or storage calls live here.
"""

import hashlib
import json

PARTICIPANT = "village_elder"
AGENT = "main"


def prepare(room, receipt, *, include_completed=False):
    """Select one newly saved, explicitly addressed owner post from this room."""
    if (
        receipt.get("saved") is not True
        or receipt.get("operation") != "post"
        or receipt.get("duplicate") is not False
    ):
        return None
    message_id = receipt.get("message_id")
    message = next((m for m in room["messages"] if m["id"] == message_id), None)
    if (
        not message
        or message.get("kind") != "message"
        or message.get("participant") != "you"
        or message.get("depth") != 0
        or message.get("reply_to") is not None
        or PARTICIPANT not in message.get("requested_responders", [])
    ):
        return None
    room_id = room["room_id"]
    digest = hashlib.sha256(json.dumps([room_id, message_id, AGENT]).encode()).hexdigest()
    key = "native_reply_" + digest[:48]
    if not include_completed and any(
        m.get("participant") == PARTICIPANT and m.get("client_message_id") == key
        for m in room["messages"]
    ):
        return None
    # A bounded, explicit room excerpt; no native conversation/history import.
    context = [
        {k: m.get(k) for k in ("id", "participant", "text", "reply_to")}
        for m in room["messages"]
        if m["seq"] <= message["seq"]
    ][-10:]
    return {
        "agent_id": AGENT,
        "room_id": room_id,
        "source_message_id": message_id,
        "client_message_id": key,
        "context": context,
    }


def reply_payload(dispatch, native_reply):
    """Only the actual native node result may be passed by the admitted adapter."""
    if not isinstance(native_reply, str) or not native_reply.strip() or len(native_reply) > 4000:
        raise ValueError("Native response must contain 1 to 4000 characters")
    return {
        "operation": "reply",
        "participant": PARTICIPANT,
        "data": {
            "client_message_id": dispatch["client_message_id"],
            "text": native_reply.strip(),
            "reply_to": dispatch["source_message_id"],
            "requested_responders": [],
            "allow_followup": False,
        },
    }
