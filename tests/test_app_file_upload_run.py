"""An ordinary app attachment is processed through the agent's own served tools.

Live finding (docs/concerns/2026-09-20-uploaded-file-binding-discovery.md): the
served agent could not discover that an app attachment reference binds through
``run_graph inputs_json``. The runtime path existed; the advertised tool
descriptions did not describe it. This file pins both halves:

1. the REGISTERED read_graph / write_graph / run_graph descriptions, on the
   served engine and the connector, carry one coherent recipe (red on the old
   descriptions, which said only "File references are unsupported/refused");
2. a real authenticated ASGI ``POST /app/files`` -> served ``write_graph``
   create -> ``run_graph inputs_json`` -> completed exact-byte processing ->
   ``read_graph`` bounded export, with no authoring session, handle or
   storage-level bind helper standing in for admission.
"""
# ruff: noqa: F811 -- imported pytest fixtures

import asyncio
import base64
import hashlib
import json
import sqlite3

import pytest

from tests.engine_authority_helpers import seed_bound_engine
from tests.test_account_deletion import _seed_user
from tests.test_app_file_upload import (  # noqa: F401 -- app is a fixture
    HOME_A,
    HOME_B,
    A,
    B,
    app,
    call,
    headers,
    meta,
    rows,
)
from tinyassets import daemon_server, runs
from tinyassets import engine_mcp_server as engine
from tinyassets import universe_server as server
from tinyassets.auth import middleware as mw
from tinyassets.branch_versions import list_branch_versions
from tinyassets.storage import _connect as author_connection

HOME_C = "u-cccccccccccccccc"  # a later home for the SAME owner
SIX_FIELDS = "{version,file_id,size_bytes,sha256,filename,media_type}"
RPC_CALL = 'invoke_mcp_action("read_run_file", file_id=ref["file_id"], offset=0, count=524288)'

# The recipe the descriptions advertise, used verbatim as the served create
# spec below: digest every bound file and report its first sixteen bytes, a
# value that exists nowhere in the reference metadata.
SOURCE = """import base64, hashlib
def run(state, effects=None):
    digests, heads = [], []
    for ref in state['files']:
        h, offset, head = hashlib.sha256(), 0, b''
        while True:
            part = invoke_mcp_action('read_run_file',
                file_id=ref['file_id'], offset=offset,
                count=524288)
            chunk = base64.b64decode(part['bytes_base64'])
            head = (head + chunk)[:16]
            h.update(chunk)
            offset = part['next_offset']
            if part['eof']:
                break
        digests.append(h.hexdigest())
        heads.append(head.hex())
    return {'digests': digests, 'heads': heads}
"""

SPEC = {
    "name": "Attachment digest", "visibility": "private", "entry_point": "digest",
    "io_manifest": {"inputs": [{"name": "files", "io_type": "file_bundle",
                                "max_count": 4, "max_bytes": 4194304}]},
    "state_schema": [{"name": "files", "type": "list"}, {"name": "digests", "type": "list"},
                     {"name": "heads", "type": "list"}],
    "node_defs": [{"node_id": "digest", "display_name": "Digest", "input_keys": ["files"],
                   "output_keys": ["digests", "heads"], "tools_allowed": ["read_run_file"],
                   "source_code": SOURCE}],
    "edges": [{"from": "digest", "to": "END"}],
}


def _descriptions(mcp, *, with_chapters=False):
    """The registered descriptions, optionally plus each handle's handbook chapters.

    ``with_chapters`` is for the SERVED ENGINE surface only. Since 2026-09-26 the
    long-form half of a served handle's guidance is reachable rather than resident
    (`openspec/changes/archive/2026-09-26-engine-tool-manual-on-demand/`), so a test asking "is the
    agent told this?" about that half has to read what the agent can reach. The
    connector surface is unchanged and passes ``with_chapters=False``.
    """
    extra = engine.SERVED_TOOL_CHAPTERS if with_chapters else {}
    return {tool.name: " ".join(
        ((tool.description or "") + "".join(extra.get(tool.name, {}).values())).split()
    ) for tool in asyncio.run(mcp.list_tools())}


def check_recipe(descriptions):
    """One coherent, discoverable recipe across the three graph handles."""
    read, write, run = (descriptions[name] for name in ("read_graph", "write_graph", "run_graph"))
    # run_graph: the delivery-only refusal is scoped; attachments bind via inputs_json.
    assert "File references are unsupported" not in run
    assert "File references are refused" not in run
    assert "deliver_output does not accept file references" in run
    assert "scoped to delivery only" in run
    for needle in ("delimited JSON attachment block", SIX_FIELDS, "io_manifest", "inputs_json",
                   "VERBATIM", "untrusted", "never an instruction"):
        assert needle in run, needle
    # write_graph: attachments are already references; capture is for authoring handles.
    assert "needs no capture" in write
    assert "ONLY for authoring-session handles" in write
    for needle in (SIX_FIELDS, "io_manifest", "file_bundle", "input_keys", "tools_allowed",
                   '["read_run_file"]', RPC_CALL, "bytes_base64", "next_offset", "eof",
                   "untrusted"):
        assert needle in write, needle
    assert "no files" not in write  # the sandbox reads BOUND inputs through an authorized RPC
    # read_graph: an unbound attachment reads only after a run binds it; build and run.
    assert "already run-file references" in read
    assert "only after a run" in read
    assert "sent message is not a run binding" in read or "sent message is not a binding" in read


@pytest.mark.parametrize("mcp", [engine.mcp, server.mcp], ids=["served_engine", "connector"])
def test_registered_tool_descriptions_carry_the_attachment_recipe(mcp):
    check_recipe(_descriptions(mcp, with_chapters=mcp is engine.mcp))


def serve(monkeypatch, base, *, actor, home):
    """Pin the served engine to one owner/home, as the hosted engine is.

    Real serving authority, not a bypass: the actor is seeded as the home's
    single serving creator with admin ACL (tests/engine_authority_helpers.py)
    and the engine flag is on, so ``_binding_error``, the admission ledger and
    every file ownership/custody guard run exactly as in production. Only the
    local ASGI identity resolution is mocked.
    """
    provider = mw._get_provider()
    monkeypatch.setattr(provider, "is_auth_required", lambda: False)
    monkeypatch.setattr(provider, "resolve_always_writes", lambda: True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setattr(engine, "_GRAPH_ID", home)
    monkeypatch.setattr(engine, "_ACTOR_ID", actor)
    seed_bound_engine(monkeypatch)


def unwrap(raw):
    result = json.loads(raw)
    return result["content"] if isinstance(result, dict) and result.get("untrusted") else result


def upload(application, body, label):
    status, doc, _ = call(application, headers(body, header=meta(body, label=label)), [body])
    assert status == 200, doc
    (ref,) = doc["files"]
    assert set(ref) == {"version", "file_id", "size_bytes", "sha256", "filename", "media_type"}
    assert ref["sha256"] == hashlib.sha256(body).hexdigest() and ref["size_bytes"] == len(body)
    return ref


def test_real_upload_to_served_create_run_and_exact_export(app, monkeypatch):
    application, base = app
    binary = bytes(range(256)) * 200 + b"\x89PNG-tail\x00\xff"
    refs = [upload(application, binary, "attachment-label-000001"),
            upload(application, b"", "attachment-label-000002")]
    serve(monkeypatch, base, actor=A, home=HOME_A)

    # Unbound: the reference alone reads nothing, before and regardless of any run.
    refused = json.loads(engine.read_graph(target="run_file", run_id="not-a-run",
                                           file_id=refs[0]["file_id"]))
    assert refused.get("error") and "bytes_base64" not in refused

    created = json.loads(engine.write_graph(target="branch", operation="create",
                                            payload_json=json.dumps(SPEC)))
    assert created.get("branch_def_id"), created
    reply = unwrap(engine.run_graph(branch_def_id=created["branch_def_id"],
                                    inputs_json=json.dumps({"files": refs})))
    assert reply.get("run_id"), reply
    runs.wait_for(reply["run_id"], timeout=60)
    run = runs.get_run(base, reply["run_id"])
    assert run["status"] == "completed", run["error"]
    # Derived from the bytes, not copied from the reference: the exact digest
    # AND a prefix no metadata field carries (the empty file has none).
    assert run["output"]["digests"] == [hashlib.sha256(binary).hexdigest(),
                                        hashlib.sha256(b"").hexdigest()]
    assert run["output"]["heads"] == [binary[:16].hex(), ""]
    assert rows(base, "SELECT COUNT(*) FROM run_file_bindings WHERE run_id=?",
                reply["run_id"]) == [(2,)]
    # The REAL admission ledger charged this run: one row bound to its run id.
    with sqlite3.connect(base / ".engine_run_admissions.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM admissions WHERE run_id=?",
                              (reply["run_id"],)).fetchone() == (1,)

    chunk = unwrap(engine.read_graph(target="run_file", run_id=reply["run_id"],
                                     file_id=refs[0]["file_id"], file_max_bytes=16))
    assert base64.b64decode(chunk["bytes_base64"]) == binary[:16]
    assert chunk["next_offset"] == 16 and not chunk["eof"]
    tail = unwrap(engine.read_graph(target="run_file", run_id=reply["run_id"],
                                    file_id=refs[0]["file_id"], file_offset=len(binary) - 5,
                                    file_max_bytes=1024))
    assert base64.b64decode(tail["bytes_base64"]) == binary[-5:] and tail["eof"]


@pytest.mark.parametrize("who", ["foreign_owner_and_home", "forged_reference",
                                 "same_owner_changed_home"])
def test_foreign_or_forged_reference_never_admits_a_run(app, monkeypatch, who):
    application, base = app
    binary = b"owned by A" * 1000
    ref = upload(application, binary, "attachment-label-000003")
    if who == "forged_reference":
        serve(monkeypatch, base, actor=A, home=HOME_A)
        ref = {**ref, "sha256": hashlib.sha256(b"something else").hexdigest()}
    elif who == "same_owner_changed_home":
        # The upload was custody of (A, HOME_A). A's home moves on; the same
        # owner served on the new home may not bind the old home's file.
        _seed_user(base, A, HOME_C)
        serve(monkeypatch, base, actor=A, home=HOME_C)
    else:
        serve(monkeypatch, base, actor=B, home=HOME_B)
    created = json.loads(engine.write_graph(target="branch", operation="create",
                                            payload_json=json.dumps(SPEC)))
    assert created.get("branch_def_id"), created
    reply = unwrap(engine.run_graph(branch_def_id=created["branch_def_id"],
                                    inputs_json=json.dumps({"files": [ref]})))
    assert reply.get("error") and not reply.get("run_id"), reply
    # Refused by the file ownership/custody guard, not by a failed engine pin:
    # the serving authority above is real, so the pin guard admits the caller.
    assert "engine tools require" not in reply["error"], reply
    assert "not bound to a founder" not in reply["error"], reply
    assert rows(base, "SELECT COUNT(*) FROM run_file_bindings") == [(0,)]
    assert rows(base, "SELECT COUNT(*) FROM runs") == [(0,)]


# ---------------------------------------------------------------------------
# Mis-keyed manifests (live finding 2026-09-21). Four app-reported runs matched
# owner and home yet had zero bindings: the branch declared ``file_inputs`` /
# ``file_bundle_inputs``, both parsers read only ``inputs``/``outputs`` and
# ignored the rest, create accepted an apparently successful declaration with
# zero file fields, and the run completed as a scalar branch. The fix is
# explicit declaration validation, never read authorization or intent guessing.
# ---------------------------------------------------------------------------

MIS_KEYED = {"file_inputs": SPEC["io_manifest"]["inputs"]}
ECHO_SOURCE = """def run(state, effects=None):
    return {'echoed': [state['payload']]}
"""


def branch_names(base):
    with author_connection(base) as conn:
        return sorted(row[0] for row in conn.execute(
            "SELECT name FROM branch_definitions").fetchall())


def admitted_runs(base):
    """Admission ledger rows charged to a run; 0 when nothing was ever admitted."""
    ledger_path = base / ".engine_run_admissions.db"
    if not ledger_path.exists():
        return 0
    with sqlite3.connect(ledger_path) as ledger:
        try:
            return ledger.execute(
                "SELECT COUNT(*) FROM admissions WHERE run_id IS NOT NULL AND run_id != ''"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def run_count(base):
    """Run rows; 0 when the refusal happened before the runs DB was ever created."""
    try:
        return rows(base, "SELECT COUNT(*) FROM runs")[0][0]
    except sqlite3.OperationalError as exc:
        assert "no such table" in str(exc), exc
        return 0


def flat(reply):
    """Every string the caller would read, unescaped (JSON-dumping escapes quotes)."""
    return " ".join(str(value) for value in reply.values())


def test_served_write_graph_description_leads_with_the_exact_recipe():
    """The recipe used to begin ~23k characters into a 32k description."""
    descriptions = _descriptions(engine.mcp)
    head = descriptions["write_graph"][:2500]
    for needle in ('"io_type": "file_bundle"', '"type": "list"', "input_keys",
                   '"tools_allowed": ["read_run_file"]', RPC_CALL, "bytes_base64",
                   "next_offset", "eof", "inputs_json", "set_io_manifest"):
        assert needle in head, needle
    assert "ONLY top-level manifest keys" in head and "file_inputs" in head
    run_head = descriptions["run_graph"][:3500]
    assert "file_inputs" in run_head and "set_io_manifest" in run_head


def test_mis_keyed_manifest_refuses_create_and_patch_without_mutation(app, monkeypatch):
    _, base = app
    serve(monkeypatch, base, actor=A, home=HOME_A)
    rejected = json.loads(engine.write_graph(
        target="branch", operation="create",
        payload_json=json.dumps({**SPEC, "io_manifest": MIS_KEYED})))
    assert not rejected.get("branch_def_id"), rejected
    text = flat(rejected)
    # Actionable: names the offending key, the accepted top-level shape and the
    # file declaration, never a silent success.
    assert "'file_inputs'" in text and '"inputs" and "outputs"' in text, text
    assert '"io_type": "file_bundle"' in text and '"list" state' in text, text
    # No row for the rejected spec. (The first served write lazily seeds the
    # unrelated "Standalone Nodes" holder; that is not this create.)
    names = branch_names(base)
    assert SPEC["name"] not in names, names
    assert set(names) <= {"Standalone Nodes"}, names

    created = json.loads(engine.write_graph(target="branch", operation="create",
                                            payload_json=json.dumps(SPEC)))
    branch_id = created["branch_def_id"]
    stored = json.loads(engine.read_graph(target="branch", branch_id=branch_id))
    versions = list_branch_versions(base, branch_def_id=branch_id)
    patched = json.loads(engine.write_graph(
        target="branch", operation="patch", branch_id=branch_id,
        payload_json=json.dumps([
            {"op": "set_name", "name": "must not save"},
            {"op": "set_io_manifest",
             "io_manifest": {"file_bundle_inputs": SPEC["io_manifest"]["inputs"]}},
        ])))
    assert patched.get("status") == "rejected", patched
    assert "'file_bundle_inputs'" in flat(patched), patched
    # Atomic: neither op landed, no version was cut.
    assert json.loads(engine.read_graph(target="branch", branch_id=branch_id)) == stored
    assert list_branch_versions(base, branch_def_id=branch_id) == versions


def test_stored_mis_keyed_manifest_refuses_run_admission_and_stays_repairable(app,
                                                                              monkeypatch):
    application, base = app
    binary = b"stored before the fix" * 100
    ref = upload(application, binary, "attachment-label-000004")
    serve(monkeypatch, base, actor=A, home=HOME_A)
    created = json.loads(engine.write_graph(target="branch", operation="create",
                                            payload_json=json.dumps(SPEC)))
    branch_id = created["branch_def_id"]
    # A row persisted while the parser still ignored unknown keys. No migration
    # rewrites it; it stays inspectable and editable through the same handles.
    daemon_server.update_branch_definition(base, branch_def_id=branch_id,
                                           updates={"io_manifest": MIS_KEYED})
    readback = json.loads(engine.read_graph(target="branch", branch_id=branch_id))
    assert "file_inputs" in json.dumps(readback), readback

    reply = unwrap(engine.run_graph(branch_def_id=branch_id,
                                    inputs_json=json.dumps({"files": [ref]})))
    assert reply.get("error") and not reply.get("run_id"), reply
    assert "'file_inputs'" in reply["error"] and '"inputs" and "outputs"' in reply["error"]
    assert "set_io_manifest" in reply.get("suggested_action", ""), reply
    assert reply.get("actionable_by") == "chatbot", reply
    # Strict admission: no false run, no binding, nothing charged to a run.
    assert run_count(base) == 0
    assert rows(base, "SELECT COUNT(*) FROM run_file_bindings") == [(0,)]
    assert admitted_runs(base) == 0

    # The owner repairs the declaration through the existing served patch op...
    repaired = json.loads(engine.write_graph(
        target="branch", operation="patch", branch_id=branch_id,
        payload_json=json.dumps([{"op": "set_io_manifest",
                                  "io_manifest": SPEC["io_manifest"]}])))
    assert repaired.get("status") != "rejected", repaired
    # ...and the very same reference now binds and reads its original bytes.
    reply = unwrap(engine.run_graph(branch_def_id=branch_id,
                                    inputs_json=json.dumps({"files": [ref]})))
    assert reply.get("run_id"), reply
    runs.wait_for(reply["run_id"], timeout=60)
    run = runs.get_run(base, reply["run_id"])
    assert run["status"] == "completed", run["error"]
    assert run["output"]["digests"] == [hashlib.sha256(binary).hexdigest()]
    assert run["output"]["heads"] == [binary[:16].hex()]
    assert rows(base, "SELECT COUNT(*) FROM run_file_bindings WHERE run_id=?",
                reply["run_id"]) == [(1,)]


@pytest.mark.parametrize("manifest", ["absent", {}, {"inputs": [], "outputs": []}],
                         ids=["absent", "empty_object", "empty_lists"])
def test_absent_or_empty_manifest_keeps_reference_shaped_dicts_ordinary(app, monkeypatch,
                                                                        manifest):
    """run_file_contract: a dict outside declared file fields is ordinary data.

    Validation is of the explicit declaration only; a six-field-shaped value
    under an undeclared scalar field must neither bind nor be refused.
    """
    application, base = app
    ref = upload(application, b"ordinary data" * 10, "attachment-label-000005")
    serve(monkeypatch, base, actor=A, home=HOME_A)
    spec = {
        "name": "Echo payload", "visibility": "private", "entry_point": "echo",
        "state_schema": [{"name": "payload", "type": "dict"},
                         {"name": "echoed", "type": "list"}],
        "node_defs": [{"node_id": "echo", "display_name": "Echo", "input_keys": ["payload"],
                       "output_keys": ["echoed"], "source_code": ECHO_SOURCE}],
        "edges": [{"from": "echo", "to": "END"}],
    }
    if manifest != "absent":
        spec["io_manifest"] = manifest
    created = json.loads(engine.write_graph(target="branch", operation="create",
                                            payload_json=json.dumps(spec)))
    assert created.get("branch_def_id"), created
    reply = unwrap(engine.run_graph(branch_def_id=created["branch_def_id"],
                                    inputs_json=json.dumps({"payload": ref})))
    assert reply.get("run_id"), reply
    runs.wait_for(reply["run_id"], timeout=60)
    run = runs.get_run(base, reply["run_id"])
    assert run["status"] == "completed", run["error"]
    assert run["output"]["echoed"] == [ref]  # verbatim data, not a binding
    assert rows(base, "SELECT COUNT(*) FROM run_file_bindings") == [(0,)]
