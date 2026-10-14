"""Build a private native workflow snapshot; never installs or activates it."""

import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent


def program(room_source):
    return (
        room_source
        + "\n"
        + "\n".join(
            (BASE / name)
            .read_text(encoding="utf-8")
            .replace("from dispatch_contract import prepare, reply_payload\n", "")
            .replace("from room_store import Room, atomic, canonical, locked, safe\n", "")
            for name in ("dispatch_contract.py", "durable_dispatch.py")
        )
    )


def source(stage, *, command_center_id, storage_workflow_id, room_id, room_source):
    # Literal source/payload serialization, never interpolation into shell syntax.
    template = """import json

def object_result(value):
    if isinstance(value, str): value = json.loads(value)
    if not isinstance(value, dict) or value.get("error") or value.get("is_error"):
        raise RuntimeError("Owner-scoped workflow read refused")
    return value

def run(state):
    stage = STAGE
    if stage == "claim":
        event = state.get("event", {})
        if (not isinstance(event, dict) or event.get("type") != "run_completed"
                or event.get("branch_def_id") != STORAGE or event.get("outcome") != "completed"):
            return {"route": "stop", "dispatch": "", "prompt": "", "result": "ignored"}
        rid = event.get("run_id")
        if not isinstance(rid, str) or not rid:
            raise ValueError("Missing source run")
        actual = object_result(invoke_mcp_action(
            "read_graph", target="run", graph_id=CENTER, run_id=rid))
        if (actual.get("run_id") != rid or actual.get("branch_def_id") != STORAGE
                or actual.get("status") != "completed" or actual.get("cancel_requested")):
            raise PermissionError("Source run does not match room event")
        output = object_result(invoke_mcp_action("read_graph", target="run_output", graph_id=CENTER,
            run_id=rid, field_name="result", output_max_chars=8192))
        if (output.get("status") != "completed" or output.get("run_id") != rid
                or output.get("truncated") is not False or "value" not in output):
            raise ValueError("Incomplete source receipt")
        payload = object_result(output["value"])
        if payload.get("room_id") != ROOM or payload.get("ok") is not True:
            raise PermissionError("Foreign room receipt")
        payload["message_id"] = payload.get("id")
    else:
        payload = json.loads(state["dispatch"])
        payload["native_reply"] = state.get("native_reply")
    import base64
    wire = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    code = (PROGRAM + "\\nimport base64\\nprint(json.dumps(execute_stage('/u'," + repr(stage)
            + ",json.loads(base64.b64decode(" + repr(wire) + "))),ensure_ascii=False))\\n")
    answer = invoke_mcp_action("bash", command=(
        "python3 - <<'NATIVE_DISPATCH_PY'\\n" + code + "\\nNATIVE_DISPATCH_PY"), timeout=40)
    if isinstance(answer, dict):
        if answer.get("is_error"): raise RuntimeError("Native dispatch storage refused")
        answer = answer.get("text", "")
    if "[exit code 0]" not in str(answer):
        raise RuntimeError("Native dispatch storage did not complete")
    documents = []
    for line in str(answer).splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict): documents.append(value)
        except ValueError: pass
    if len(documents) != 1: raise ValueError("Ambiguous dispatch receipt")
    value = documents[0]
    if stage == "claim":
        return {"route": value["route"], "dispatch": json.dumps(value),
                "prompt": value.get("prompt", ""), "result": json.dumps(value)}
    return {"result": json.dumps(value)}
"""
    values = {
        "STAGE": stage,
        "STORAGE": storage_workflow_id,
        "CENTER": command_center_id,
        "ROOM": room_id,
        "PROGRAM": program(room_source),
    }
    return re.sub(
        r"\b(?:STAGE|STORAGE|CENTER|ROOM|PROGRAM)\b", lambda match: repr(values[match[0]]), template
    )


def build(*, author, command_center_id, storage_workflow_id, room_id, room_source):
    from tinyassets.branches import (
        BranchDefinition,
        ConditionalEdge,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )

    if any(
        not isinstance(value, str) or not value.strip()
        for value in (
            author,
            command_center_id,
            storage_workflow_id,
            room_id,
            room_source,
        )
    ):
        raise ValueError("Verified owner, center, storage workflow, room and adapter required")
    config = dict(
        command_center_id=command_center_id,
        storage_workflow_id=storage_workflow_id,
        room_id=room_id,
        room_source=room_source,
    )
    compile(program(room_source), "<owner-room-adapter>", "exec")
    nodes = [
        NodeDefinition(
            node_id="claim",
            display_name="Verify addressed room post and claim",
            source_code=source("claim", **config),
            input_keys=["event"],
            output_keys=["route", "dispatch", "prompt", "result"],
            tools_allowed=["read_graph", "bash"],
            timeout_seconds=60,
        ),
        NodeDefinition(
            node_id="native",
            display_name="Village Elder native reply",
            prompt_template="{prompt}",
            input_keys=["prompt"],
            output_keys=["native_reply"],
            model_hint="writer",
            tools_allowed=["agent", "read"],
            timeout_seconds=120,
            retry_policy={"max_retries": 0, "backoff_seconds": 1},
        ),
        NodeDefinition(
            node_id="commit",
            display_name="Persist and commit actual reply",
            source_code=source("finish", **config),
            input_keys=["dispatch", "native_reply"],
            output_keys=["result"],
            tools_allowed=["bash"],
            timeout_seconds=60,
        ),
    ]
    return BranchDefinition(
        name="Roundtable native addressed replies",
        author=author,
        visibility="private",
        description="Bounded addressed native main replies; disabled until separately activated.",
        graph_nodes=[GraphNodeRef(n.node_id, n.node_id, i) for i, n in enumerate(nodes)],
        node_defs=nodes,
        entry_point="claim",
        concurrency_budget=1,
        edges=[
            EdgeDefinition("START", "claim"),
            EdgeDefinition("native", "commit"),
            EdgeDefinition("commit", "END"),
        ],
        conditional_edges=[
            ConditionalEdge("claim", {"stop": "END", "commit": "commit", "generate": "native"})
        ],
        state_schema=[{"name": "event", "type": "dict", "default": {}}]
        + [
            {"name": name, "type": "str", "default": ""}
            for name in ("route", "dispatch", "prompt", "native_reply", "result")
        ],
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--room-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    branch = build(**config, room_source=args.room_source.read_text(encoding="utf-8"))
    errors = branch.validate()
    if errors:
        raise SystemExit(errors)
    args.output.write_text(branch.to_json(), encoding="utf-8")
