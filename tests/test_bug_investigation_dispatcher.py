"""Tests for bug_investigation dispatcher integration — Task #47."""

from __future__ import annotations

import json

from tinyassets.branch_tasks import BranchTask, append_task, read_queue
from tinyassets.bug_investigation import REQUEST_TYPE_BUG_INVESTIGATION
from tinyassets.control_plane.queue_policy import (
    get_request_type_priorities,
    load_dispatcher_config,
    prefers_request_type,
    select_next_task,
)

# ── enqueue_investigation_request ─────────────────────────────────────────────


# ── prefers_request_type / get_request_type_priorities ────────────────────────


class TestPrefersRequestType:
    def test_unset_env_accepts_all_types(self, monkeypatch):
        monkeypatch.delenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", raising=False)
        assert prefers_request_type("bug_investigation") is True
        assert prefers_request_type("branch_run") is True
        assert prefers_request_type("paid_market") is True

    def test_empty_env_accepts_all_types(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", "")
        assert prefers_request_type("bug_investigation") is True

    def test_daemon_with_bug_investigation_in_priorities_claims_it(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", "bug_investigation,paid_market")
        assert prefers_request_type("bug_investigation") is True

    def test_daemon_without_bug_investigation_does_not_claim(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", "paid_market,branch_run")
        assert prefers_request_type("bug_investigation") is False

    def test_priorities_order_preserved(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", "bug_investigation,paid_market")
        priorities = get_request_type_priorities()
        assert priorities == ["bug_investigation", "paid_market"]

    def test_whitespace_trimmed_in_priorities(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", " bug_investigation , branch_run ")
        assert prefers_request_type("bug_investigation") is True
        assert prefers_request_type("branch_run") is True


# ── select_next_task filters by request_type ─────────────────────────────────


class TestSelectNextTaskRequestTypeFilter:
    def _make_task(self, tmp_path, request_type="branch_run", task_id=None):
        import uuid
        from datetime import datetime, timezone
        task = BranchTask(
            branch_task_id=task_id or str(uuid.uuid4()),
            branch_def_id="branch-abc",
            universe_id="test",
            trigger_source="owner_queued",
            queued_at=datetime.now(timezone.utc).isoformat(),
            request_type=request_type,
        )
        append_task(tmp_path, task)
        return task

    def test_daemon_with_priorities_only_claims_matching_type(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", "bug_investigation")
        self._make_task(tmp_path, request_type="branch_run", task_id="branch-task")
        self._make_task(tmp_path, request_type="bug_investigation", task_id="bug-task")
        config = load_dispatcher_config(tmp_path)
        selected = select_next_task(tmp_path, config=config)
        assert selected is not None
        assert selected.request_type == "bug_investigation"

    def test_daemon_without_priorities_claims_any_type(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", raising=False)
        self._make_task(tmp_path, request_type="bug_investigation")
        config = load_dispatcher_config(tmp_path)
        selected = select_next_task(tmp_path, config=config)
        assert selected is not None
        assert selected.request_type == "bug_investigation"

    def test_no_matching_tasks_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", "paid_market")
        self._make_task(tmp_path, request_type="bug_investigation")
        config = load_dispatcher_config(tmp_path)
        selected = select_next_task(tmp_path, config=config)
        assert selected is None


# ── format_investigation_comment with request_id ──────────────────────────────


# ── BranchTask request_type field ─────────────────────────────────────────────


class TestBranchTaskRequestTypeField:
    def test_default_request_type_is_branch_run(self):
        import uuid
        task = BranchTask(
            branch_task_id=str(uuid.uuid4()),
            branch_def_id="b1",
            universe_id="u1",
        )
        assert task.request_type == "branch_run"

    def test_request_type_roundtrips_through_json(self, tmp_path):
        import uuid
        from datetime import datetime, timezone
        task = BranchTask(
            branch_task_id=str(uuid.uuid4()),
            branch_def_id="b1",
            universe_id="u1",
            queued_at=datetime.now(timezone.utc).isoformat(),
            request_type="bug_investigation",
        )
        append_task(tmp_path, task)
        queue = read_queue(tmp_path)
        assert queue[0].request_type == "bug_investigation"

    def test_legacy_tasks_without_request_type_default_to_branch_run(self, tmp_path):
        """Tasks written before the field existed default gracefully."""
        import uuid

        from tinyassets.branch_tasks import queue_path
        task_dict = {
            "branch_task_id": str(uuid.uuid4()),
            "branch_def_id": "b1",
            "universe_id": "u1",
            "status": "pending",
            "trigger_source": "owner_queued",
        }
        queue_path(tmp_path).write_text(json.dumps([task_dict]), encoding="utf-8")
        queue = read_queue(tmp_path)
        assert queue[0].request_type == "branch_run"


class TestBugInvestigationDirectRunRouting:
    def test_bug_investigation_tasks_execute_through_direct_run_path(self):
        from fantasy_daemon.__main__ import _should_execute_claimed_branch_directly

        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        assert _should_execute_claimed_branch_directly(task) is True

    def test_universe_cycle_wrapper_still_uses_wrapper_path(self):
        from fantasy_daemon.__main__ import _should_execute_claimed_branch_directly

        task = BranchTask(
            branch_task_id="bt-wrapper",
            branch_def_id="fantasy_author:universe_cycle_wrapper",
            universe_id="u",
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        assert _should_execute_claimed_branch_directly(task) is False

    def test_bug_investigation_direct_run_synthesizes_request_text_for_legacy_tasks(self):
        from fantasy_daemon.__main__ import _branch_task_inputs_for_execution

        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-009", "title": "dispatcher pickup stalls"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        inputs = _branch_task_inputs_for_execution(task)

        assert inputs["request_text"].startswith("bug BUG-009: dispatcher pickup stalls")

    def test_bug_investigation_direct_run_preserves_existing_request_text(self):
        from fantasy_daemon.__main__ import _branch_task_inputs_for_execution

        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-009", "request_text": "already normalized"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        inputs = _branch_task_inputs_for_execution(task)

        assert inputs["request_text"] == "already normalized"

    def test_branch_run_inputs_are_not_rewritten(self):
        from fantasy_daemon.__main__ import _branch_task_inputs_for_execution

        task = BranchTask(
            branch_task_id="bt-branch",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-009"},
            request_type="branch_run",
        )

        inputs = _branch_task_inputs_for_execution(task)

        assert inputs == {"bug_id": "BUG-009"}

    def test_direct_run_rejects_task_universe_mismatching_physical_queue(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import _try_execute_claimed_branch_task

        universe_path = tmp_path / "physical-universe"
        universe_path.mkdir()
        queue_file = universe_path / "branch_tasks.json"
        queue_file.write_text("[]", encoding="utf-8")
        queue_before = queue_file.read_bytes()
        execute_calls = []

        monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "tinyassets.api.branches._resolve_branch_id",
            lambda requested, base_path: "branch-1",
        )
        monkeypatch.setattr(
            "tinyassets.daemon_server.get_branch_definition",
            lambda base_path, branch_def_id: {"branch_def_id": branch_def_id},
        )

        class _Branch:
            def validate(self):
                return []

        from tinyassets.branches import BranchDefinition
        monkeypatch.setattr(
            BranchDefinition,
            "from_dict",
            classmethod(lambda cls, data: _Branch()),
        )
        monkeypatch.setattr(
            "tinyassets.runs.latest_run_by_name",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "tinyassets.runs.execute_branch",
            lambda *args, **kwargs: execute_calls.append((args, kwargs)),
        )
        task = BranchTask(
            branch_task_id="bt-wrong-universe",
            branch_def_id="branch-1",
            universe_id="claimed-other-universe",
            request_type="branch_run",
        )

        success, error, metadata = _try_execute_claimed_branch_task(
            universe_path, task, "daemon-a",
        )

        assert success is False
        assert "universe" in error
        assert "mismatch" in error
        assert "physical-universe" in (error + repr(metadata))
        assert "claimed-other-universe" in (error + repr(metadata))
        assert execute_calls == []
        assert queue_file.read_bytes() == queue_before

    def test_direct_run_passes_physical_universe_to_matched_execution(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import _try_execute_claimed_branch_task
        from tinyassets.runs import RUN_STATUS_COMPLETED

        universe_path = tmp_path / "physical-universe"
        universe_path.mkdir()
        execute_calls = []

        monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "tinyassets.api.branches._resolve_branch_id",
            lambda requested, base_path: "branch-1",
        )
        monkeypatch.setattr(
            "tinyassets.daemon_server.get_branch_definition",
            lambda base_path, branch_def_id: {"branch_def_id": branch_def_id},
        )

        class _Branch:
            def validate(self):
                return []

        class _Outcome:
            run_id = "run-physical"
            status = RUN_STATUS_COMPLETED
            output = {}
            error = ""

        from tinyassets.branches import BranchDefinition
        monkeypatch.setattr(
            BranchDefinition,
            "from_dict",
            classmethod(lambda cls, data: _Branch()),
        )
        monkeypatch.setattr(
            "tinyassets.runs.latest_run_by_name",
            lambda *args, **kwargs: None,
        )

        def _capture_execute(*args, **kwargs):
            execute_calls.append((args, kwargs))
            return _Outcome()

        monkeypatch.setattr("tinyassets.runs.execute_branch", _capture_execute)
        task = BranchTask(
            branch_task_id="bt-physical",
            branch_def_id="branch-1",
            universe_id="physical-universe",
            request_type="branch_run",
        )

        success, error, metadata = _try_execute_claimed_branch_task(
            universe_path, task, "daemon-a",
        )

        assert success is True
        assert error == ""
        assert metadata["run_id"] == "run-physical"
        assert len(execute_calls) == 1
        assert (
            execute_calls[0][1]["_enqueue_universe_id"]
            == "physical-universe"
        )

    def test_direct_run_reuses_completed_branch_task_run_after_restart(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import _try_execute_claimed_branch_task
        from tinyassets.runs import (
            RUN_STATUS_COMPLETED,
            create_run,
            update_run_status,
        )

        monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "tinyassets.api.branches._resolve_branch_id",
            lambda requested, base_path: "branch-1",
        )
        monkeypatch.setattr(
            "tinyassets.daemon_server.get_branch_definition",
            lambda base_path, branch_def_id: {"branch_def_id": branch_def_id},
        )

        class _Branch:
            def validate(self):
                return []

        from tinyassets.branches import BranchDefinition
        monkeypatch.setattr(
            BranchDefinition,
            "from_dict",
            classmethod(lambda cls, data: _Branch()),
        )

        def _should_not_execute(*args, **kwargs):
            raise AssertionError("completed durable run should be reused")

        monkeypatch.setattr("tinyassets.runs.execute_branch", _should_not_execute)

        run_id = create_run(
            tmp_path,
            branch_def_id="branch-1",
            thread_id="",
            inputs={"bug_id": "BUG-011"},
            run_name="branch-task-bt-restart",
            actor="daemon-a",
        )
        update_run_status(
            tmp_path,
            run_id,
            status=RUN_STATUS_COMPLETED,
            output={"result": "already completed"},
        )
        task = BranchTask(
            branch_task_id="bt-restart",
            branch_def_id="branch-1",
            universe_id=tmp_path.name,
            inputs={"bug_id": "BUG-011"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        success, error, metadata = _try_execute_claimed_branch_task(
            tmp_path, task, "daemon-a",
        )

        assert success is True
        assert error == ""
        assert metadata["run_id"] == run_id
        assert metadata["run_status"] == RUN_STATUS_COMPLETED
        assert metadata["reused_existing_run"] is True


class TestBugInvestigationPatchPacketWriteBack:
    def _make_bug_page(self, tmp_path):
        bugs_dir = tmp_path / "pages" / "bugs"
        bugs_dir.mkdir(parents=True)
        page = bugs_dir / "bug-057-write-completed-loop-investigation.md"
        page.write_text(
            "---\nbug_id: BUG-057\ntitle: Write back investigation\n---\n\n"
            "## Description\n\nInvestigation output should be visible on the request page.\n",
            encoding="utf-8",
        )
        return page

    def test_completed_bug_investigation_attaches_patch_packet_to_wiki(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import (
            _maybe_attach_bug_investigation_patch_packet,
        )

        page = self._make_bug_page(tmp_path)
        monkeypatch.setattr("tinyassets.storage.wiki_path", lambda: tmp_path)
        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-057"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        result = _maybe_attach_bug_investigation_patch_packet(
            task,
            "completed",
            {
                "patch_packet": {
                    "root_cause": "completed run output was never attached",
                    "test_plan": "assert wiki page contains patch packet",
                }
            },
        )

        assert result["status"] == "attached"
        written = page.read_text(encoding="utf-8")
        assert "## Patch Packet" in written
        assert "completed run output was never attached" in written

    def test_non_completed_bug_investigation_does_not_attach_false_packet(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import (
            _maybe_attach_bug_investigation_patch_packet,
        )

        page = self._make_bug_page(tmp_path)
        monkeypatch.setattr("tinyassets.storage.wiki_path", lambda: tmp_path)
        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-057"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        result = _maybe_attach_bug_investigation_patch_packet(
            task,
            "failed",
            {"patch_packet": {"root_cause": "not proven"}},
        )

        assert result == {"status": "skipped", "reason": "run_status:failed"}
        assert "## Patch Packet" not in page.read_text(encoding="utf-8")

    def test_attaches_candidate_packet_from_attached_child_output(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import (
            _maybe_attach_bug_investigation_patch_packet,
        )

        page = self._make_bug_page(tmp_path)
        monkeypatch.setattr("tinyassets.storage.wiki_path", lambda: tmp_path)
        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-057"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        result = _maybe_attach_bug_investigation_patch_packet(
            task,
            "completed",
            {
                "attached_child_output": {
                    "candidate_patch_packet": {
                        "implementation_sketch": "attach child packet output",
                    }
                }
            },
        )

        assert result["status"] == "attached"
        assert "attach child packet output" in page.read_text(encoding="utf-8")

    def test_attaches_live_parent_child_candidate_packet_string(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import (
            _maybe_attach_bug_investigation_patch_packet,
        )

        page = self._make_bug_page(tmp_path)
        monkeypatch.setattr("tinyassets.storage.wiki_path", lambda: tmp_path)
        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-057"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        result = _maybe_attach_bug_investigation_patch_packet(
            task,
            "completed",
            {
                "child_candidate_patch_packet": (
                    "The patch packet is in "
                    "[candidate_patch_packet.md](/tmp/tinyassets/candidate_patch_packet.md:1)."
                ),
                "child_run_id": "e12caa6629ff48d6",
                "coding_packet": {
                    "status": "CHILD_REVIEW_READY",
                    "candidate_packet_summary": "freshness_scope_check before PR creation",
                },
            },
        )

        assert result["status"] == "attached"
        written = page.read_text(encoding="utf-8")
        assert "## Patch Packet" in written
        assert "candidate_patch_packet.md" in written
        assert "### Implementation Sketch" in written

    def test_attaches_live_child_candidate_packet_string(
        self, tmp_path, monkeypatch
    ):
        from fantasy_daemon.__main__ import (
            _maybe_attach_bug_investigation_patch_packet,
        )

        page = self._make_bug_page(tmp_path)
        monkeypatch.setattr("tinyassets.storage.wiki_path", lambda: tmp_path)
        task = BranchTask(
            branch_task_id="bt-bug",
            branch_def_id="change-loop",
            universe_id="u",
            inputs={"bug_id": "BUG-057"},
            request_type=REQUEST_TYPE_BUG_INVESTIGATION,
        )

        result = _maybe_attach_bug_investigation_patch_packet(
            task,
            "completed",
            {
                "candidate_patch_packet": (
                    "**candidate_patch_packet**\n\n"
                    "`repo_availability_gate` blocks coding before source is present."
                ),
                "keep_reject_decision": "REVIEW_READY",
            },
        )

        assert result["status"] == "attached"
        written = page.read_text(encoding="utf-8")
        assert "repo_availability_gate" in written
        assert "### Implementation Sketch" in written
