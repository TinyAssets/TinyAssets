"""Ordinary app byte upload through the real identity middleware and ASGI frames.

No authoring session, file handle or hidden tool seeds custody: the only path
is POST /app/files driven with scripted ASGI body frames.
"""

import base64
import hashlib
import inspect
import json
import time

import anyio
import pytest
from starlette.applications import Starlette

from tests.test_account_deletion import _seed_user
from tinyassets import account_deletion, daemon_server, onboarding, runs
from tinyassets import run_file_upload as upload
from tinyassets.api import run_files as run_files_api
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import Identity
from tinyassets.authoring.store import AuthoringStore
from tinyassets.execution_authority.blob_proof import BlobProofStore
from tinyassets.onboarding import file_upload as route
from tinyassets.run_file_release import release_owned_file
from tinyassets.storage import _connect as author_connection
from tinyassets.storage import run_files
from tinyassets.storage.run_file_lock import _active as active_guards

URL = "/app/files"
A, B = "user_upload_a", "user_upload_b"
HOME_A, HOME_B = "u-aaaaaaaaaaaaaaaa", "u-bbbbbbbbbbbbbbbb"
MIB = 1024 * 1024
LABEL = "label-0123456789abcdef"


class Auth:
    def resolve_token(self, token):
        return Identity(user_id=token, username=token) if token in {A, B, "no_home"} else None

    def is_auth_required(self):
        return True

    def resolve_always_writes(self):
        return False

    def writes_require_identity(self):
        return True

    def challenge_unauthenticated(self):
        return True


def meta(body, *, label=LABEL, home=HOME_A, filename="photo.bin", **overrides):
    doc = {"version": 1, "label": label, "expected_universe_id": home, "filename": filename,
           "media_type": "application/octet-stream", "size_bytes": len(body),
           "sha256": hashlib.sha256(body).hexdigest()}
    doc.update(overrides)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def headers(body=b"", *, owner=A, header=None, length=True, **updates):
    result = {"Authorization": f"Bearer {owner}", "Origin": "https://tinyassets.io",
              "Host": "tinyassets.io", "Content-Type": "application/octet-stream",
              "X-TinyAssets-Upload": meta(body) if header is None else header}
    if length:
        result["Content-Length"] = str(len(body))
    for key, value in updates.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES", str(64 * MIB))
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    monkeypatch.setattr(mw, "_provider", Auth())
    daemon_server.initialize_author_server(tmp_path)
    for owner, home in ((A, HOME_A), (B, HOME_B)):
        _seed_user(tmp_path, owner, home)
    with runs._connect(tmp_path) as conn:  # additive schema so zero-row asserts can read
        conn.execute("BEGIN IMMEDIATE")
        run_files.ensure_schema(conn)

    def no_handles(*args, **kwargs):
        raise AssertionError("app upload must not create authoring handles or sessions")

    monkeypatch.setattr(AuthoringStore, "put_file_handle", no_handles)
    monkeypatch.setattr("tinyassets.providers.call.call_provider", no_handles)
    return mw.AuthContextMiddleware(Starlette(routes=onboarding.onboarding_routes())), tmp_path


def call(app, hdrs, frames=(), *, path=URL, method="POST"):
    """Drive the ASGI app; frames are bytes, ASGI messages or callables."""
    script = list(frames)
    reads = {"body": 0}
    sent = []

    async def run():
        async def receive():
            if not script:
                await anyio.sleep(3600)
            item = script.pop(0)
            if callable(item):
                item = item()
                if inspect.isawaitable(item):
                    item = await item
            reads["body"] += 1
            if isinstance(item, dict):
                return item
            return {"type": "http.request", "body": item, "more_body": bool(script)}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "client": ("127.0.0.1", 4321),
                 "server": ("tinyassets.io", 443),
                 "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()]}
        await app(scope, receive, send)

    anyio.run(run)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body) if body[:1] == b"{" else body, reads["body"]


def rows(base, sql, *params):
    with runs._connect(base) as conn:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]


def test_identity_origin_and_header_refusals_read_zero_bytes(app):
    application, base = app
    body = b"never read"
    cases = [
        (headers(body, Authorization=None), 401),
        (headers(body, owner="invalid"), 401),
        (headers(body, Origin="https://evil.example"), 403),
        (headers(body, Origin="null"), 403),
        (headers(body, Origin="http://tinyassets.io"), 403),
        (headers(body, Origin="https://tinyassets.io/mcp"), 403),
        (headers(body, Origin=None), 403),
        (headers(body, **{"Content-Type": "application/json"}), 403),
        (headers(body, **{"X-TinyAssets-Upload": None}), 403),
        (headers(body, header="!!!not-base64"), 400),
        (headers(body, header=base64.urlsafe_b64encode(b"\xff\xfe").decode()), 400),
        (headers(body, header=meta(body, size_bytes=True)), 400),
        (headers(body, header=meta(body, size_bytes=-1)), 400),
        (headers(body, header=meta(body, label="short")), 400),
        (headers(body, header=meta(body, extra="field")), 400),
        (headers(body, header=meta(body, sha256="A" * 64)), 400),
        (headers(body, header=meta(body, size_bytes=upload.MAX_UPLOAD_BYTES + 1)), 413),
        (headers(body, header="A" * (upload.MAX_HEADER_BYTES + 1)), 413),
        (headers(body, **{"Content-Length": "12x"}), 400),
        (headers(body, owner="no_home"), 409),
        (headers(body, header=meta(body, home=HOME_B)), 409),
        (headers(body, owner=B, header=meta(body, home=HOME_A)), 409),
    ]
    for hdrs, expected in cases:
        status, doc, reads = call(application, hdrs, [body])
        assert status == expected, (hdrs, status, doc)
        assert reads == 0, hdrs
    assert rows(base, "SELECT COUNT(*) FROM run_file_operations") == [(0,)]
    assert not active_guards


def test_fresh_home_multi_frame_binary_replay_and_metadata_only_observation(app):
    application, base = app
    body = bytes(range(256)) * (6 * MIB // 256) + b"\xff\xfe\x00tail"
    frames = [body[: 3 * MIB + 17], body[3 * MIB + 17: 5 * MIB], body[5 * MIB:]]
    status, doc, reads = call(application, headers(body), frames)
    assert status == 200, doc
    assert set(doc) == {"universe_id", "files", "unbound_retention_seconds", "unbound_expires_at"}
    assert doc["universe_id"] == HOME_A and doc["unbound_retention_seconds"] == 3600
    (ref,) = doc["files"]
    assert set(ref) == {"version", "file_id", "size_bytes", "sha256", "filename", "media_type"}
    assert ref["version"] == 1 and ref["size_bytes"] == len(body)
    assert ref["sha256"] == hashlib.sha256(body).hexdigest()
    assert (ref["filename"], ref["media_type"]) == ("photo.bin", "application/octet-stream")
    assert (base / ".run-file-custody" / (ref["file_id"] + ".body")).read_bytes() == body
    assert reads == len(frames)
    (row,) = rows(base, "SELECT state,unbound_expires_at,owner_id,universe_id "
                        "FROM run_file_operations")
    assert (row[0], row[2], row[3]) == ("committed", A, HOME_A)
    assert doc["unbound_expires_at"] == row[1] and row[1] > time.time() + 3000
    assert rows(base, "SELECT kind,run_id,amount,reserved FROM workspace_ledger") == [
        ("bytes", "", len(body), 0)]
    # Same label + metadata: original references, no body byte read, no new copy.
    again = call(application, headers(body), frames)
    assert again[0] == 200 and again[1] == doc and again[2] == 0
    # Explicit metadata-only observation after reload: committed result, no burn.
    observe = call(application, headers(body, **{"Content-Length": "0"}), [b""])
    assert observe[0] == 200 and observe[1] == doc and observe[2] == 0
    assert rows(base, "SELECT COUNT(*) FROM run_file_operations") == [(1,)]
    # Changed metadata under the same label conflicts before any byte.
    changed = call(application, headers(body, header=meta(body, filename="other.bin")), frames)
    assert changed[0] == 409 and changed[1] == {"error": "file_operation_conflict"}
    assert changed[2] == 0
    assert not active_guards


def test_empty_file_and_sibling_labels_are_independent(app):
    application, base = app
    empty = call(application, headers(b"", header=meta(b"", label="empty-file-label-01")), [b""])
    assert empty[0] == 200 and empty[1]["files"][0]["size_bytes"] == 0
    assert empty[1]["files"][0]["sha256"] == hashlib.sha256(b"").hexdigest()
    other = b"second independent file"
    second = call(application, headers(other, header=meta(other, label="second-file-label-01")),
                  [other[:5], other[5:]])
    assert second[0] == 200 and second[1]["files"][0]["size_bytes"] == len(other)
    assert rows(base, "SELECT COUNT(*) FROM run_file_objects WHERE state='ready'") == [(2,)]
    peer = call(application, headers(other, owner=B,
                                     header=meta(other, home=HOME_B, label="second-file-label-01")),
                [other])
    assert peer[0] == 200 and peer[1]["universe_id"] == HOME_B
    assert peer[1]["files"][0]["file_id"] != second[1]["files"][0]["file_id"]


def test_length_lie_refuses_new_copy_without_burning_the_label(app):
    application, base = app
    body = b"declared length disagrees"
    lie = call(application, headers(body, **{"Content-Length": str(len(body) + 1)}), [body])
    assert lie == (400, {"error": "upload_length_mismatch"}, 0)
    assert rows(base, "SELECT COUNT(*) FROM run_file_operations") == [(0,)]
    ok = call(application, headers(body, length=False), [body[:3], body[3:]])
    assert ok[0] == 200


def test_content_mismatch_and_overflow_consume_label_as_cleanup_debt(app):
    application, base = app
    body = b"x" * (2 * MIB + 5)
    wrong = call(application, headers(body, header=meta(body, sha256="0" * 64)), [body])
    assert wrong == (400, {"error": "upload_content_mismatch"}, 1)
    (row,) = rows(base, "SELECT state FROM run_file_operations")
    assert row[0] == "cleanup"
    assert rows(base, "SELECT COUNT(*) FROM run_file_cleanup") == [(1,)]
    assert rows(base, "SELECT COUNT(*) FROM run_file_objects") == [(0,)]
    held = call(application, headers(body, header=meta(body, sha256="0" * 64)), [body])
    assert held == (409, {"error": "file_operation_recovery_required"}, 0)
    # More bytes than declared, undeclared length: refused as too large.
    short = body[:100]
    over = call(application,
                headers(short, length=False, header=meta(short, label="overflow-label-0001")),
                [short, b"extra"])
    assert over[0] == 413 and over[1] == {"error": "upload_too_large"}
    assert rows(base, "SELECT COUNT(*) FROM run_file_objects WHERE state='ready'") == [(0,)]
    assert not active_guards


def test_disconnect_mid_stream_wakes_worker_and_keeps_exact_debt(app):
    application, base = app
    body = b"y" * (3 * MIB)
    status, doc, reads = call(application, headers(body),
                              [body[:MIB], {"type": "http.disconnect"}])
    assert status == 400 and doc == {"error": "upload_disconnected"} and reads == 2
    (row,) = rows(base, "SELECT state,inventory_json FROM run_file_operations")
    assert row[0] == "cleanup"
    assert json.loads(rows(base, "SELECT inventory_json FROM run_file_cleanup")[0][0]) == \
        json.loads(row[1])
    assert not active_guards
    # Uncertain physical cleanup keeps the conservative allocation as debt.
    assert rows(base, "SELECT amount,state FROM run_file_allocations") == [(len(body), "releasing")]


def test_idle_and_total_deadlines_stop_a_stalled_source(app, monkeypatch):
    application, base = app
    monkeypatch.setattr(upload, "IDLE_SECONDS", 0.3)
    body = b"z" * (2 * MIB)
    stalled = call(application, headers(body),  # promises more, never sends it
                   [{"type": "http.request", "body": body[:MIB], "more_body": True}])
    assert stalled[0] == 408 and stalled[1] == {"error": "upload_timeout"}, stalled
    assert rows(base, "SELECT state FROM run_file_operations") == [("cleanup",)]
    assert not active_guards
    monkeypatch.setattr(upload, "IDLE_SECONDS", 10.0)
    monkeypatch.setattr(upload, "TOTAL_SECONDS", 0.5)
    other = b"w" * (2 * MIB)

    async def late():
        await anyio.sleep(0.8)
        return other[MIB:]

    slow = call(application, headers(other, header=meta(other, label="total-deadline-label")),
                [other[:MIB], late])
    assert slow[0] == 408 and slow[1] == {"error": "upload_timeout"}
    assert rows(base, "SELECT COUNT(*) FROM run_file_objects") == [(0,)]
    assert not active_guards


def test_bridge_buffers_at_most_two_chunks_with_a_slow_worker(app, monkeypatch):
    application, base = app
    bridges = []
    original = upload.StreamBridge

    def recording(*args, **kwargs):
        bridge = original(*args, **kwargs)
        bridges.append(bridge)
        return bridge

    monkeypatch.setattr(upload, "StreamBridge", recording)
    real_stage = BlobProofStore.stage_stream

    def slow_stage(self, relative_path, chunks, **kwargs):
        def paced():
            for chunk in chunks:
                time.sleep(0.05)
                yield chunk
        return real_stage(self, relative_path, paced(), **kwargs)

    monkeypatch.setattr(BlobProofStore, "stage_stream", slow_stage)
    body = bytes(range(256)) * (6 * MIB // 256)
    status, doc, _ = call(application, headers(body), [body[: 4 * MIB + 1], body[4 * MIB + 1:]])
    assert status == 200, doc
    (bridge,) = bridges
    assert 1 <= bridge.high_water <= upload.BRIDGE_SLOTS
    assert bridge.consumed == len(body)


def test_home_change_during_copy_refuses_inside_the_fence(app):
    application, base = app
    body = b"h" * (2 * MIB)

    def rebind():
        daemon_server.set_founder_home(base, founder_sub=A, universe_id=HOME_B,
                                       platform_generated=True)
        return body[MIB:]

    status, doc, _ = call(application, headers(body), [body[:MIB], rebind])
    assert status == 409 and doc == {"error": "upload_home_changed"}
    assert rows(base, "SELECT COUNT(*) FROM run_file_objects") == [(0,)]
    assert rows(base, "SELECT state FROM run_file_operations") == [("cleanup",)]
    assert not active_guards


def test_capacity_missing_and_full_slots_refuse_before_bytes(app, monkeypatch):
    application, base = app
    body = b"capacity"
    monkeypatch.delenv("TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES")
    assert call(application, headers(body), [body]) == (
        503, {"error": "file_custody_not_configured"}, 0)
    monkeypatch.setenv("TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES", str(64 * MIB))
    taken = [route._SLOTS.acquire(blocking=False) for _ in range(route.UPLOAD_SLOTS)]
    assert all(taken)
    try:
        assert call(application, headers(body), [body]) == (503, {"error": "upload_busy"}, 0)
    finally:
        for _ in taken:
            route._SLOTS.release()
    assert rows(base, "SELECT COUNT(*) FROM run_file_operations") == [(0,)]
    assert call(application, headers(body), [body])[0] == 200


def test_expiry_binding_release_and_erasure_use_existing_custody(app):
    application, base = app
    body = b"bound later"
    doc = call(application, headers(body), [body])[1]
    (ref,) = doc["files"]
    with runs._connect(base) as conn:
        conn.execute("UPDATE run_file_operations SET unbound_expires_at=?", (time.time() - 1,))
    expired = call(application, headers(body, **{"Content-Length": "0"}), [b""])
    assert expired == (409, {"error": "upload_expired"}, 0)
    run_id = runs.create_run(base, branch_def_id="branch", thread_id="", inputs={}, actor=A,
                             owner_user_id=A, queue_universe_id=HOME_A)
    with runs._connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE run_file_operations SET unbound_expires_at=?", (time.time() + 3600,))
        run_files.bind_in_transaction(conn, run_id=run_id, owner_id=A, universe_id=HOME_A,
                                      field_name="file", file_ids=[ref["file_id"]])
    with runs._connect(base) as conn:
        conn.execute("UPDATE run_file_operations SET unbound_expires_at=?", (time.time() - 1,))
    bound = call(application, headers(body, **{"Content-Length": "0"}), [b""])
    assert bound[0] == 200 and bound[1]["files"] == [ref]
    assert bound[1]["unbound_expires_at"] is None
    runs.update_run_status(base, run_id, status="completed")
    other = b"released after upload"
    released_doc = call(application,
                        headers(other, header=meta(other, label="release-me-label-01")),
                        [other])[1]
    file_id = released_doc["files"][0]["file_id"]
    assert release_owned_file(base, owner_id=A, universe_id=HOME_A, file_id=file_id)
    gone = call(application, headers(other, header=meta(other, label="release-me-label-01")),
                [other])
    assert gone[0] == 409 and gone[2] == 0
    # Owner erasure removes the app-uploaded custody through the existing path.
    account_deletion.delete_account(base, founder_sub=A, cancel_billing=lambda _: "none",
                                    delete_identity=lambda _: "deleted")
    assert rows(base, "SELECT COUNT(*) FROM run_file_objects WHERE owner_id=? AND state='ready'",
                A) == [(0,)]
    with author_connection(base) as conn:
        assert conn.execute("SELECT COUNT(*) FROM universe_acl WHERE actor_id=?", (A,)
                            ).fetchone()[0] == 0
    assert call(application, headers(body), [body])[0] in (401, 403, 409)


def test_discovery_advertises_app_upload_only_when_route_is_enabled(app, monkeypatch):
    application, base = app
    # The discovery module binds its helpers at import; patch ITS bindings so no
    # stale base path or principal leaks into suites that run after this one.
    monkeypatch.setattr(run_files_api, "_base_path", lambda: base)
    monkeypatch.setattr(run_files_api, "_principal", lambda write=False: A)
    monkeypatch.setattr(run_files_api, "_request_universe", lambda u: u)
    limits = json.loads(run_files_api.file_limits(universe_id=HOME_A))
    assert limits.get("app_upload_available") is True, limits
    assert limits["app_upload_max_bytes"] == upload.MAX_UPLOAD_BYTES == 8 * MIB
    assert limits["supported_intake"] == [
        "authoring_handle", "app_upload", "cross_owner_delivery",
    ]
    assert limits["file_delivery_available"] is True
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "0")
    dark = json.loads(run_files_api.file_limits(universe_id=HOME_A))
    assert dark["app_upload_available"] is False
    # Disabling browser upload does not disable the separately authorized
    # receiver-owned delivery path.
    assert dark["supported_intake"] == ["authoring_handle", "cross_owner_delivery"]
    assert dark["file_delivery_available"] is True
    assert call(application, headers(b"x"), [b"x"])[0] == 404
