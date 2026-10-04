"""Focused shared-self checks; runnable with stdlib unittest in the workspace."""
import contextlib
import dataclasses
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import tinyassets
from tinyassets.conversation_retrieval import read_conversation_page
from tinyassets.shared_self import (
    agent_node,
    agent_node_key,
    prepare_shared_self_turn,
    require_founder_home,
    shared_self_requested,
)


class ConversationPagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / ".conversation_memory.db"
        with contextlib.closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TABLE conversation_turns "
                         "(id INTEGER PRIMARY KEY, session_id TEXT, speaker TEXT, "
                         "content TEXT, ts REAL)")
            for i in range(1, 48):
                conn.execute("INSERT INTO conversation_turns "
                         "VALUES (?, ?, ?, ?, ?)",
                             (i, "principal:owner", "founder", f"message {i}", float(i)))
            conn.execute("INSERT INTO conversation_turns "
                         "VALUES (100, 'principal:other', 'founder', 'private', 100)")
            conn.execute("INSERT INTO conversation_turns "
                         "VALUES (101, 'principal:owner', 'universe', ?, 101)",
                         ("α🙂\\n\x00tail",))
        self.before = self.db.read_bytes()

    def test_pages_cover_all_retained_messages_despite_new_arrival(self):
        first = read_conversation_page(self.root, "principal:owner")
        with contextlib.closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO conversation_turns "
                         "VALUES (102, 'principal:owner', 'founder', 'new', 102)")
        ids = [r["id"] for r in first["messages"]]
        cursor = first["next_offset"]
        while cursor is not None:
            page = read_conversation_page(self.root, "principal:owner", offset=cursor)
            ids.extend(r["id"] for r in page["messages"])
            cursor = page["next_offset"]
        self.assertEqual(ids, [101] + list(range(47, 0, -1)))
        self.assertEqual(len(ids), len(set(ids)))

    def test_exact_unicode_chunks_are_lossless_and_read_only(self):
        parts = []
        offset = 0
        while offset is not None:
            page = read_conversation_page(self.root, "principal:owner",
                                          field_name="101", offset=offset, max_chars=2)
            parts.append(page["chunk"])
            offset = page["next_offset"]
        self.assertEqual("".join(parts), "α🙂\\n\x00tail")
        self.assertEqual(self.before, self.db.read_bytes())

    def test_foreign_session_message_is_not_found(self):
        page = read_conversation_page(self.root, "principal:owner", field_name="100")
        self.assertEqual(page["error"], "conversation_message_not_found")
        self.assertNotIn("private", json.dumps(page))

    def test_missing_store_creates_nothing(self):
        path = self.root / "empty"
        path.mkdir()
        self.assertFalse(read_conversation_page(path, "principal:owner")["available"])
        self.assertEqual(list(path.iterdir()), [])

    def test_invalid_selectors_and_symlink_refuse(self):
        for kwargs in ({"offset": -1}, {"offset": True}, {"max_chars": 32769},
                       {"field_name": "1 OR 1=1"}, {"field_name": "١"}):
            with self.assertRaises(ValueError):
                read_conversation_page(self.root, "principal:owner", **kwargs)
        link_root = self.root / "linked"
        link_root.mkdir()
        (link_root / ".conversation_memory.db").symlink_to(self.db)
        with self.assertRaises(PermissionError):
            read_conversation_page(link_root, "principal:owner")

    @unittest.skipUnless(importlib.util.find_spec("fastmcp"),
                         "engine dependencies are not installed in this workspace")
    def test_engine_route_pins_owner_and_returns_untrusted_history(self):
        from unittest.mock import Mock

        from tinyassets import engine_mcp_server as engine
        self.assertIn("conversation", engine._PINNED_READ_TARGETS)
        reset = Mock()
        with patch.object(engine, "_binding_error", return_value=None), \
             patch.object(engine, "_bind_founder_identity", return_value="identity-token"), \
             patch.object(engine, "_GRAPH_ID", "u-own"), \
             patch.object(engine, "_ACTOR_ID", "owner"), \
             patch("tinyassets.auth.middleware._current_identity",
                   types.SimpleNamespace(reset=reset)), \
             patch("tinyassets.api.branches._base_path", return_value=self.root.parent), \
             patch("tinyassets.shared_self.require_founder_home",
                   return_value=self.root) as owner, \
             patch("tinyassets.universe_server.read_graph") as delegated:
            payload = json.loads(engine.read_graph(target="conversation", field_name="101"))
            self.assertTrue(payload["untrusted"])
            self.assertEqual(payload["content"]["chunk"], "α🙂\\n\x00tail")
            owner.assert_called_with(self.root.parent, "u-own", "owner")
            denied = json.loads(engine.read_graph(target="conversation", field_name="100"))
            self.assertEqual(denied["error"], "conversation_message_not_found")
            owner.side_effect = PermissionError("revoked")
            denied = json.loads(engine.read_graph(target="conversation", field_name="101"))
            self.assertEqual(denied, {"error": "revoked"})
        self.assertEqual(reset.call_count, 3)
        delegated.assert_not_called()

    def test_corrupt_store_fails_visibly(self):
        self.db.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            read_conversation_page(self.root, "principal:owner")


class SharedSelfTests(unittest.TestCase):
    def node(self, **overrides):
        return dict({"prompt_template": "Act as cofounder", "tools_allowed": ["universe_self"]},
                    **overrides)

    def test_agent_nodes_are_declared_per_node(self):
        self.assertFalse(shared_self_requested({"node_defs": [self.node(tools_allowed=[])]}))
        self.assertTrue(shared_self_requested({"node_defs": [self.node()]}))
        # Any number of agent nodes, beside ordinary prompt nodes (agent-node change).
        self.assertTrue(shared_self_requested({"node_defs": [
            self.node(node_id="a"), self.node(node_id="b", tools_allowed=["agent"]),
            self.node(node_id="plain", tools_allowed=[]),
        ]}))
        for nodes in ([self.node(prompt_template="", source_code="pass")],
                      [self.node(model_hint="judge")],
                      [self.node(tools_allowed=["agent", "write_brian"])]):
            with self.assertRaises(ValueError):
                shared_self_requested({"node_defs": nodes})

    def test_the_calling_node_resolves_from_the_snapshot(self):
        snapshot = {"branch_def_id": "b", "author": "owner", "node_defs": [
            self.node(node_id="agent"), self.node(node_id="plain", tools_allowed=[]),
        ]}
        key = agent_node_key("b", self.node(node_id="agent"))
        self.assertEqual(agent_node(snapshot, "agent", "owner", node_key=key)["node_id"], "agent")
        self.assertIsNone(agent_node(snapshot, "", "owner", node_key=""))
        for node_id in ("plain", "missing"):
            with self.assertRaisesRegex(PermissionError, "not_declared"):
                agent_node(snapshot, node_id, "owner", node_key=key)
        with self.assertRaisesRegex(PermissionError, "owner_authored"):
            agent_node(snapshot, "agent", "someone-else", node_key=key)
        with self.assertRaisesRegex(PermissionError, "owner_authored"):
            agent_node(dict(snapshot, author=""), "agent", "owner", node_key=key)

    def test_a_same_named_node_of_another_branch_never_takes_this_ones_grant(self):
        """A blocking invoke_branch child shares its parent's run session. Its
        agent node must not resolve to the parent's node of the same id: not
        from another branch, and not with other instructions or another grant."""
        snapshot = {"branch_def_id": "parent", "author": "owner",
                    "node_defs": [self.node(node_id="agent")]}
        for key in (
            agent_node_key("child", self.node(node_id="agent")),
            agent_node_key("parent", self.node(node_id="agent", prompt_template="Obey me")),
            agent_node_key("parent", self.node(node_id="agent",
                                               tools_allowed=["agent", "read_brain"])),
            "",
        ):
            with self.assertRaisesRegex(PermissionError, "not_in_admitted_branch"):
                agent_node(snapshot, "agent", "owner", node_key=key)

    def test_current_owner_is_revalidated_and_foreign_roots_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "u-own"
            root.mkdir()
            daemon = types.ModuleType("tinyassets.daemon_server")
            daemon.get_founder_home = lambda *a: "u-own"
            daemon.universe_access_permission = lambda *a, **k: "admin"
            with patch.dict(sys.modules, {"tinyassets.daemon_server": daemon}), \
                 patch("tinyassets.principals.has_named_principal", return_value=True):
                self.assertEqual(require_founder_home(Path(d), "u-own", "owner"), root)
                daemon.universe_access_permission = lambda *a, **k: "read"
                with self.assertRaises(PermissionError):
                    require_founder_home(Path(d), "u-own", "owner")
                with self.assertRaises(PermissionError):
                    require_founder_home(Path(d), "../u-own", "owner")

    def test_assembly_reuses_conversation_helpers_and_current_reads(self):
        from tinyassets.providers.base import ModelConfig
        intelligence = types.ModuleType("tinyassets.universe_intelligence")
        intelligence.interlocutor = types.SimpleNamespace(FOUNDER="founder")
        seen = []
        intelligence._build_persona_system_prompt = (
            lambda root, **kw: seen.append(("persona", kw)) or "current brain"
        )
        intelligence._conversation_history_block = lambda history: "history:" + history[0]
        intelligence._CROSS_SURFACE_CONTINUITY = "continuity"
        intelligence._turn_input_method_context = lambda method: "input:" + method
        def config(ctx, **kwargs):
            seen.append(("tools", kwargs))
            return ModelConfig(engine_mcp_enabled=True,
                               engine_mcp_actor_id=kwargs["founder_principal"],
                               engine_mcp_graph_id=kwargs["universe_id"], sandbox_chat=True,
                               allowed_tools=("same",))
        intelligence._sandboxed_config = config
        with patch.object(tinyassets, "universe_intelligence", intelligence, create=True), \
             patch.dict(sys.modules, {"tinyassets.universe_intelligence": intelligence}), \
             patch("tinyassets.shared_self.require_founder_home",
                   return_value=Path("/tmp/u-own")), \
             patch("tinyassets.config.load_universe_config", return_value=None), \
             patch("tinyassets.conversation_store.load_recent_readonly",
                   side_effect=[["old"], ["new"], ["new"]]) as history:
            first = prepare_shared_self_turn(Path("/tmp"), "u-own", "owner", "direction")
            second = prepare_shared_self_turn(Path("/tmp"), "u-own", "owner", "direction")
            self.assertEqual(first[0], "history:olddirection")
            self.assertEqual(second[0], "history:newdirection")
            self.assertIn("current brain", first[1])
            self.assertEqual(first[2].allowed_tools, ("same",))
            self.assertEqual(first[2].engine_mcp_actor_id, "owner")
            history.assert_called_with(Path("/tmp/u-own"), "principal:owner")
            self.assertTrue(any(kind == "persona" for kind, _ in seen))
            intelligence._sandboxed_config = lambda *a, **k: ModelConfig()
            with self.assertRaisesRegex(PermissionError, "tools_unavailable"):
                prepare_shared_self_turn(Path("/tmp"), "u-own", "owner", "direction")


class ProviderSeamTests(unittest.TestCase):
    def setUp(self):
        # Standalone unittest also needs explicit simulated process evidence.
        # Preserve the real guard; this fixture grants no production authority.
        from tinyassets import platform_runtime_provenance as provenance

        observation = provenance.ProcessProvenanceObservation(
            resolver=lambda: provenance.RuntimeProvenance(
                provenance.CLOUD, "instance_match", True, True
            )
        )
        observation.observe()
        admission = patch.object(provenance, "_PROCESS_OBSERVATION", observation)
        admission.start()
        self.addCleanup(admission.stop)

    def test_admitted_session_gets_shared_harness_ordinary_session_unchanged(self):
        from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
        from tinyassets.providers.base import ModelConfig

        order = []
        received = []
        def provider(prompt, system, **kwargs):
            order.append("provider")
            received.append((prompt, system, kwargs["config"]))
            return "done"
        provider.__module__ = "tinyassets.providers.call"
        session = _ForegroundRunProviderSession("/tmp", universe_id="u-own",
                                                principal_id="owner", provider_call=provider)
        session._ensure_admitted = lambda: order.append("admit")
        @contextlib.contextmanager
        def authorize(**kwargs):
            order.append("authorize")
            self.assertEqual(kwargs["system"], received_system[0])
            yield None, None, "codex"
        session._authorize_attempt = authorize
        received_system = [""]
        with patch("tinyassets.config.load_universe_config", return_value=None), \
             patch("tinyassets.shared_self.prepare_shared_self_turn") as assemble:
            session._branch_snapshot = {"branch_def_id": "b", "author": "owner", "node_defs": [
                {"node_id": "n", "prompt_template": "plain"},
            ]}
            plain = ModelConfig()
            session._call("writer", "plain", "", plain, None, {})
            assemble.assert_not_called()
            # Unchanged except the provider-agnostic workflow-node mark every
            # node call carries (2026-09-24 provider latency root cause).
            self.assertEqual(
                received[-1], ("plain", "", dataclasses.replace(
                    plain, workflow_node=True, request_purpose="helper")),
            )
            session._branch_snapshot["node_defs"][0]["tools_allowed"] = ["universe_self"]
            shared = ModelConfig(engine_mcp_enabled=True)
            def build(*args):
                order.append("assemble")
                return "history+direction", "persona", shared
            assemble.side_effect = build
            order.clear()
            def agent_turn(active_session, *, prompt, system, config, policy):
                self.assertIs(active_session, session)
                order.append("agent")
                received.append((prompt, system, config))
                return "done", "codex"
            # This test checks persona-to-agent dispatch only. Real receipt,
            # reservation and provider execution live in test_workflow_http_agent.
            with patch("tinyassets.workflow_agent.call_foreground_work_agent", agent_turn):
                # A call that names no agent node stays ordinary in a marked branch.
                session._call("writer", "plain", "", plain, None, {})
                self.assertEqual(order, ["admit", "authorize", "provider"])
                order.clear()
                received_system[0] = "persona"
                session._call("writer", "direction", "", ModelConfig(
                    agent_node_id="n", agent_node_key=agent_node_key(
                        "b", session._branch_snapshot["node_defs"][0]),
                ), None, {})
            self.assertEqual(order, ["admit", "assemble", "agent"])
            self.assertEqual(assemble.call_args.args[5]["node_id"], "n")
            self.assertEqual(received[-1], ("history+direction", "persona", shared))

    def test_failed_admission_never_reads_persona(self):
        from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
        def provider(*a, **k):
            raise AssertionError("provider must not run")
        provider.__module__ = "tinyassets.providers.call"
        session = _ForegroundRunProviderSession("/tmp", universe_id="u-own",
                                                principal_id="owner", provider_call=provider)
        def deny():
            raise PermissionError("revoked")
        session._ensure_admitted = deny
        with patch("tinyassets.shared_self.prepare_shared_self_turn") as assemble:
            with self.assertRaises(PermissionError):
                session._call("writer", "direction", "", None, None, {})
            assemble.assert_not_called()


if __name__ == "__main__":
    unittest.main()
