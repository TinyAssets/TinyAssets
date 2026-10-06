"""Tests for teammate_message primitive.

Spec: docs/vetted-specs.md §teammate_message.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.runs import (
    ack_teammate_message,
    create_run,
    initialize_runs_db,
    post_teammate_message,
    read_teammate_messages,
)


def _seed_run(base_path: Path) -> str:
    """Create a minimal run record and return its run_id."""
    initialize_runs_db(base_path)
    run_id = create_run(
        base_path,
        branch_def_id="test-branch",
        run_name="test",
        thread_id="thread-msg",
        inputs={},
        actor="universe:u-test",
    )
    return run_id


# ─── post_teammate_message ────────────────────────────────────────────────────

class TestPostTeammateMessage:
    def test_send_inserts_record(self, tmp_path):
        run_id = _seed_run(tmp_path)
        record = post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-B",
            message_type="request",
            body={"task": "summarize"},
        )
        assert record["message_id"]
        assert record["from_run_id"] == run_id
        assert record["to_node_id"] == "node-B"
        assert record["message_type"] == "request"
        assert record["body"] == {"task": "summarize"}
        assert record["acked"] is False

    def test_send_returns_unique_message_ids(self, tmp_path):
        run_id = _seed_run(tmp_path)
        r1 = post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-B",
            message_type="request",
            body={},
        )
        r2 = post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-B",
            message_type="request",
            body={},
        )
        assert r1["message_id"] != r2["message_id"]

    def test_send_with_reply_to_id(self, tmp_path):
        run_id = _seed_run(tmp_path)
        orig = post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-B",
            message_type="plan_approval_request",
            body={"plan": "do stuff"},
        )
        reply = post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-A",
            message_type="plan_approval_response",
            body={"approve": True},
            reply_to_id=orig["message_id"],
        )
        assert reply["reply_to_id"] == orig["message_id"]

    def test_send_requires_from_run_id(self, tmp_path):
        _seed_run(tmp_path)
        with pytest.raises(ValueError, match="from_run_id"):
            post_teammate_message(
                tmp_path,
                from_run_id="",
                to_node_id="node-B",
                message_type="request",
                body={},
            )

    def test_send_requires_to_node_id(self, tmp_path):
        run_id = _seed_run(tmp_path)
        with pytest.raises(ValueError, match="to_node_id"):
            post_teammate_message(
                tmp_path,
                from_run_id=run_id,
                to_node_id="",
                message_type="request",
                body={},
            )

    def test_send_rejects_unknown_message_type(self, tmp_path):
        run_id = _seed_run(tmp_path)
        with pytest.raises(ValueError, match="message_type"):
            post_teammate_message(
                tmp_path,
                from_run_id=run_id,
                to_node_id="node-B",
                message_type="invalid_type",
                body={},
            )

    def test_send_rejects_phantom_from_run_id(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(KeyError, match="not found"):
            post_teammate_message(
                tmp_path,
                from_run_id="nonexistent-run-id",
                to_node_id="node-B",
                message_type="request",
                body={},
            )

    def test_send_all_valid_message_types(self, tmp_path):
        run_id = _seed_run(tmp_path)
        valid_types = [
            "request", "response", "broadcast",
            "plan_approval_request", "plan_approval_response",
            "shutdown_request", "shutdown_response",
        ]
        for mt in valid_types:
            record = post_teammate_message(
                tmp_path,
                from_run_id=run_id,
                to_node_id="node-B",
                message_type=mt,
                body={},
            )
            assert record["message_type"] == mt


# ─── read_teammate_messages ───────────────────────────────────────────────────

class TestReadTeammateMessages:
    def test_receive_returns_messages_for_node(self, tmp_path):
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-B",
            message_type="request",
            body={"x": 1},
        )
        messages = read_teammate_messages(tmp_path, node_id="node-B")
        assert len(messages) == 1
        assert messages[0]["body"] == {"x": 1}

    def test_receive_does_not_return_messages_for_other_nodes(self, tmp_path):
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-C",
            message_type="request",
            body={},
        )
        messages = read_teammate_messages(tmp_path, node_id="node-B")
        assert messages == []

    def test_receive_includes_broadcast_messages(self, tmp_path):
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="*",
            message_type="broadcast",
            body={"announcement": "phase changed"},
        )
        messages = read_teammate_messages(tmp_path, node_id="node-B")
        assert len(messages) == 1
        assert messages[0]["body"]["announcement"] == "phase changed"

    def test_receive_no_node_id_returns_all(self, tmp_path):
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-A",
            message_type="request", body={},
        )
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        messages = read_teammate_messages(tmp_path)
        assert len(messages) == 2

    def test_receive_since_filter(self, tmp_path):
        import time
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={"order": 1},
        )
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc).isoformat()
        time.sleep(0.01)
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={"order": 2},
        )
        messages = read_teammate_messages(tmp_path, node_id="node-B", since=cutoff)
        assert len(messages) == 1
        assert messages[0]["body"]["order"] == 2

    def test_receive_message_types_filter(self, tmp_path):
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="response", body={},
        )
        messages = read_teammate_messages(
            tmp_path, node_id="node-B", message_types=["request"],
        )
        assert len(messages) == 1
        assert messages[0]["message_type"] == "request"

    def test_receive_is_non_destructive(self, tmp_path):
        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        first = read_teammate_messages(tmp_path, node_id="node-B")
        second = read_teammate_messages(tmp_path, node_id="node-B")
        assert len(first) == len(second) == 1

    def test_receive_limit_capped_at_1000(self, tmp_path):
        _seed_run(tmp_path)
        messages = read_teammate_messages(tmp_path, node_id="node-B", limit=99999)
        # Limit is clamped; no error
        assert isinstance(messages, list)


# ─── ack_teammate_message ────────────────────────────────────────────────────

class TestAckTeammateMessage:
    def test_ack_marks_message_as_acked(self, tmp_path):
        run_id = _seed_run(tmp_path)
        record = post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        ack_teammate_message(tmp_path, message_id=record["message_id"], node_id="node-B")
        messages = read_teammate_messages(tmp_path, node_id="node-B")
        assert messages[0]["acked"] is True

    def test_ack_returns_acked_at_timestamp(self, tmp_path):
        run_id = _seed_run(tmp_path)
        record = post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        result = ack_teammate_message(
            tmp_path, message_id=record["message_id"], node_id="node-B",
        )
        assert "acked_at" in result
        assert result["message_id"] == record["message_id"]

    def test_double_ack_is_idempotent(self, tmp_path):
        run_id = _seed_run(tmp_path)
        record = post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        ack_teammate_message(tmp_path, message_id=record["message_id"], node_id="node-B")
        ack_teammate_message(tmp_path, message_id=record["message_id"], node_id="node-B")
        messages = read_teammate_messages(tmp_path)
        assert messages[0]["acked"] is True

    def test_ack_by_wrong_node_rejected(self, tmp_path):
        run_id = _seed_run(tmp_path)
        record = post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-B",
            message_type="request", body={},
        )
        with pytest.raises(PermissionError):
            ack_teammate_message(
                tmp_path, message_id=record["message_id"], node_id="node-C",
            )

    def test_ack_broadcast_message_by_any_node(self, tmp_path):
        run_id = _seed_run(tmp_path)
        record = post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="*",
            message_type="broadcast", body={},
        )
        result = ack_teammate_message(
            tmp_path, message_id=record["message_id"], node_id="any-node",
        )
        assert "acked_at" in result

    def test_ack_nonexistent_message_raises_key_error(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(KeyError):
            ack_teammate_message(
                tmp_path, message_id="nonexistent-id", node_id="node-B",
            )


# ─── Plan-approval flow ───────────────────────────────────────────────────────

class TestPlanApprovalFlow:
    def test_request_response_roundtrip(self, tmp_path):
        run_id = _seed_run(tmp_path)
        request_id = "plan-123"

        request_msg = post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="lead-node",
            message_type="plan_approval_request",
            body={"request_id": request_id, "plan_summary": "do X then Y"},
        )

        post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="worker-node",
            message_type="plan_approval_response",
            body={"request_id": request_id, "approve": True},
            reply_to_id=request_msg["message_id"],
        )

        responses = read_teammate_messages(
            tmp_path,
            node_id="worker-node",
            message_types=["plan_approval_response"],
        )
        assert len(responses) == 1
        assert responses[0]["body"]["approve"] is True
        assert responses[0]["body"]["request_id"] == request_id


# ─── MCP action dispatch ──────────────────────────────────────────────────────


# ─── Node-spec graph-compiler primitives (TDD — implementations pending) ─────
# These tests lock the contract for the two graph_compiler dispatch arms
# (send_message_spec / receive_messages_spec) and compile-time validation.
# All tests are xfail(strict=True) until the graph_compiler dispatch is wired.

def _assert_graph_primitive_recognized(result: object, primitive: str) -> None:
    """Guard: fail if the node spec is still unrecognised."""
    if isinstance(result, dict) and "error" in result:
        error = result["error"]
        assert "Unknown" not in error and "not implemented" not in error.lower(), (
            f"{primitive} is not yet implemented — got: {error}"
        )


class TestSendMessageSpec:
    """Tests for the `send_message_spec` node-level primitive in graph_compiler.

    These lock the contract: when a NodeDefinition declares a `send_message_spec`
    field, graph_compiler injects a pre-node send step that calls post_teammate_message.
    """

    def test_send_message_spec_writes_row(self, tmp_path):
        from tinyassets.graph_compiler import compile_send_message_spec

        run_id = _seed_run(tmp_path)
        result = compile_send_message_spec(
            base_path=tmp_path,
            run_id=run_id,
            to_node_id="node-B",
            message_type="broadcast",
            body={"event": "phase_started"},
        )
        _assert_graph_primitive_recognized(result, "send_message_spec")
        messages = read_teammate_messages(tmp_path, node_id="node-B")
        assert len(messages) == 1
        assert messages[0]["message_type"] == "broadcast"

    def test_send_message_spec_generates_server_side_message_id(self, tmp_path):
        from tinyassets.graph_compiler import compile_send_message_spec

        run_id = _seed_run(tmp_path)
        result = compile_send_message_spec(
            base_path=tmp_path,
            run_id=run_id,
            to_node_id="node-C",
            message_type="request",
            body={"payload": "hello"},
        )
        _assert_graph_primitive_recognized(result, "send_message_spec")
        assert "message_id" in result
        assert len(result["message_id"]) > 0

    def test_send_message_spec_rejects_phantom_run_id(self, tmp_path):
        from tinyassets.graph_compiler import compile_send_message_spec

        with pytest.raises(Exception):
            compile_send_message_spec(
                base_path=tmp_path,
                run_id="nonexistent-run",
                to_node_id="node-B",
                message_type="request",
                body={},
            )


class TestReceiveMessagesSpec:
    """Tests for the `receive_messages_spec` node-level primitive in graph_compiler.

    These lock the contract: when a NodeDefinition declares a `receive_messages_spec`
    field, graph_compiler injects a pre-node receive step that calls read_teammate_messages.
    """

    def test_receive_messages_spec_returns_matching_rows(self, tmp_path):
        from tinyassets.graph_compiler import compile_receive_messages_spec

        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path,
            from_run_id=run_id,
            to_node_id="node-A",
            message_type="response",
            body={"result": "done"},
        )
        result = compile_receive_messages_spec(
            base_path=tmp_path,
            node_id="node-A",
        )
        _assert_graph_primitive_recognized(result, "receive_messages_spec")
        assert "messages" in result
        assert len(result["messages"]) >= 1
        assert result["messages"][0]["message_type"] == "response"

    def test_receive_messages_spec_timeout0_returns_empty_when_no_messages(
        self, tmp_path
    ):
        from tinyassets.graph_compiler import compile_receive_messages_spec

        _seed_run(tmp_path)
        result = compile_receive_messages_spec(
            base_path=tmp_path,
            node_id="node-with-no-messages",
            timeout=0,
        )
        _assert_graph_primitive_recognized(result, "receive_messages_spec")
        assert result["messages"] == []

    def test_receive_messages_spec_returns_in_step_index_order(self, tmp_path):
        from tinyassets.graph_compiler import compile_receive_messages_spec

        run_id = _seed_run(tmp_path)
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-A",
            message_type="request", body={"seq": 1},
        )
        post_teammate_message(
            tmp_path, from_run_id=run_id, to_node_id="node-A",
            message_type="request", body={"seq": 2},
        )
        result = compile_receive_messages_spec(
            base_path=tmp_path,
            node_id="node-A",
        )
        _assert_graph_primitive_recognized(result, "receive_messages_spec")
        messages = result["messages"]
        assert len(messages) == 2
        # Deterministic order: earliest sent_at first.
        assert messages[0]["body"]["seq"] == 1
        assert messages[1]["body"]["seq"] == 2

    def test_cross_run_isolation(self, tmp_path):
        from tinyassets.graph_compiler import compile_receive_messages_spec

        run_a = _seed_run(tmp_path)
        run_b = create_run(
            tmp_path,
            branch_def_id="test-branch",
            run_name="run-b",
            thread_id="thread-b",
            inputs={},
            actor="universe:u-test",
        )
        # Post a message from run_a to node-X.
        post_teammate_message(
            tmp_path, from_run_id=run_a, to_node_id="node-X",
            message_type="broadcast", body={"source": "run-a"},
        )
        # Receive from node-X scoped to run_b — should see nothing (run isolation).
        result = compile_receive_messages_spec(
            base_path=tmp_path,
            node_id="node-X",
            run_id=run_b,  # scoped to run_b only
        )
        _assert_graph_primitive_recognized(result, "receive_messages_spec")
        assert result["messages"] == [], (
            "Cross-run isolation failed: run_b received messages from run_a"
        )


class TestSendMessageSpecCompileTimeValidation:
    """Compile-time validation: recipient_node_id not in branch raises error."""

    def test_unknown_recipient_raises_at_compile_time(self, tmp_path):
        from tinyassets.branches import (
            BranchDefinition,
            EdgeDefinition,
            GraphNodeRef,
            NodeDefinition,
        )
        from tinyassets.graph_compiler import validate_message_recipients

        nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
        branch = BranchDefinition(
            branch_def_id="b1",
            name="B1",
            graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
            edges=[EdgeDefinition(from_node="n1", to_node="END")],
            entry_point="n1",
            node_defs=[nd],
            state_schema=[],
        )
        # Sending to "nonexistent-node" not in the branch should raise at compile time.
        with pytest.raises(Exception, match=r"(BranchValidation|recipient|not found)"):
            validate_message_recipients(
                branch=branch,
                send_message_specs=[{"to_node_id": "nonexistent-node"}],
            )
