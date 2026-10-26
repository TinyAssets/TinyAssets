"""MCP Events on the trusted side: subscriptions, fan-out and delivery in the broker.

Ported from the suite the C6 cutover deleted (plan P0-1). The broker holds the
store; a box reports only identifiers and the subscriptions it re-authorized.
"""

import asyncio
import base64
import hashlib
import hmac
import json

import pytest

from tinyassets.broker.mcp_events import (
    MAX_SUBSCRIPTIONS,
    VERIFICATION_LIMIT,
    EventDeliveries,
    EventStore,
    box_operation,
    error_answer,
)
from tinyassets.mcp_event_webhooks import (
    EventError,
    callback_url,
    signed_headers,
    signing_key,
    verify,
)
from tinyassets.mcp_events import NAME, arguments, authorize

SECRET = "whsec_" + base64.b64encode(b"s" * 32).decode()
SECOND = "whsec_" + base64.b64encode(b"n" * 32).decode()
EVENT = {"command_center_id": "center", "branch_def_id": "save", "run_id": "run1",
         "event_id": "run1#1", "occurred": 1001}


def params(**extra):
    return {
        "name": NAME,
        "arguments": {
            "command_center_id": "center",
            "branch_def_id": "save",
            "input_equals": {"operation": "save"},
            "output_equals": {"saved": True},
        },
        "delivery": {
            "mode": "webhook",
            "url": "https://receiver.example/callback",
            "secret": SECRET,
        },
        **extra,
    }


def answering(owner, url, body, headers):
    doc = json.loads(body)
    return 200, json.dumps({"challenge": doc.get("challenge")}).encode()


@pytest.fixture
def harness(tmp_path):
    now, sent = [1000.0], []

    def sender(owner, url, body, headers):
        doc = json.loads(body)
        sent.append((url, doc, headers, body, owner))
        if "challenge" in doc:
            return 200, json.dumps({"challenge": doc["challenge"]}).encode()
        return 202, b""

    def factory(**kwargs):
        return EventStore(tmp_path / "mcp-events.db", sender=kwargs.get("sender", sender),
                          clock=lambda: now[0])

    return factory, now, sent


def capture(store, owner="alice", *, allow=None, revoke=(), **changes):
    """What a box does: match, re-authorize (here: all), then name the allowed ones."""
    event = EVENT | changes
    matched = [item["id"] for item in store.match(owner, event)]
    return store.capture(owner, event, allow=matched if allow is None else allow,
                         revoke=list(revoke))


def rows(store, table):
    with store.db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM " + table)]


def test_idempotence_restart_and_owner_isolation(harness):
    factory, now, sent = harness
    store = factory()
    sub = store.subscribe("alice", params())
    assert factory().subscribe("alice", params())["id"] == sub["id"]
    assert len(sent) == 1  # Verified refresh is cached.
    assert sent[0][4] == "alice"  # The challenge leaves under the subscriber's egress.
    bob = store.subscribe("bob", params())["id"]
    assert bob != sub["id"]
    now[0] = 1001
    capture(store)
    capture(factory())
    assert len(rows(store, "deliveries")) == 1
    factory().unsubscribe("bob", params())
    factory().pump()
    delivered = sent[-1][1]
    assert delivered["data"] == {"command_center_id": "center", "branch_def_id": "save",
                                 "run_id": "run1"}
    assert rows(store, "deliveries")[0]["state"] == "received"
    assert sent[-1][2]["X-MCP-Subscription-Id"] == sub["id"]
    factory().pump()
    assert len(sent) == 3


def test_match_names_no_callback_or_key(harness):
    factory, _, _ = harness
    store = factory()
    sub = store.subscribe("alice", params(), authority={"client": "c", "family": "f"})
    matched = store.match("alice", EVENT)
    assert matched == [{"id": sub["id"], "arguments": params()["arguments"],
                        "authority": {"client": "c", "family": "f"}}]
    assert "receiver.example" not in json.dumps(matched) and SECRET not in json.dumps(matched)


def test_a_box_cannot_aim_a_delivery_outside_its_owner_or_scope(harness):
    factory, _, _ = harness
    store = factory()
    alice = store.subscribe("alice", params())["id"]
    other = params()
    other["arguments"]["branch_def_id"] = "other"
    elsewhere = store.subscribe("alice", other)["id"]
    # Bob's box names Alice's subscription; Alice's box names one for another workflow.
    assert capture(store, "bob", allow=[alice]) == {"queued": 0}
    assert capture(store, allow=[elsewhere], revoke=[elsewhere]) == {"queued": 0}
    assert rows(store, "deliveries") == []
    assert all(r["active"] == 1 for r in rows(store, "subscriptions"))
    assert capture(store, "bob", allow=[], revoke=[alice]) == {"queued": 0}
    assert rows(store, "subscriptions")[0]["active"] == 1


@pytest.mark.parametrize(
    "change",
    [{"command_center_id": "other"}, {"branch_def_id": "reply"}, {"occurred": 999}],
)
def test_scope_and_start_boundary(harness, change):
    factory, _, _ = harness
    store = factory()
    store.subscribe("alice", params())
    assert store.match("alice", EVENT | change) == []
    capture(store, **change)
    assert rows(store, "deliveries") == []


@pytest.mark.parametrize(
    "event",
    [
        EVENT | {"inputs": {"operation": "save"}},
        {k: v for k, v in EVENT.items() if k != "run_id"},
        EVENT | {"occurred": float("nan")},
        EVENT | {"occurred": float("inf")},
        EVENT | {"occurred": True},
        EVENT | {"run_id": ""},
        EVENT | {"run_id": "x" * 257},
    ],
)
def test_a_report_carries_identifiers_only(harness, event):
    factory, _, _ = harness
    store = factory()
    store.subscribe("alice", params())
    with pytest.raises(EventError):
        store.match("alice", event)
    with pytest.raises(EventError):
        store.capture("alice", event, allow=[], revoke=[])


def test_stale_source_events_cannot_enter(harness):
    factory, now, _ = harness
    store = factory()
    store.subscribe("alice", params(ttlMs=None))
    now[0] = 1001 + 86400 + 1
    assert store.match("alice", EVENT) == []


def test_revocation_and_expiry_erase_secrets(harness):
    factory, now, sent = harness
    store = factory()
    store.subscribe("alice", params(ttlMs=1000))
    capture(store)
    now[0] = 1002
    factory().pump()
    assert rows(store, "subscriptions")[0]["config"] == ""
    assert len(sent) == 1
    sub = store.subscribe("alice", params())["id"]
    capture(store, occurred=1003, event_id="run2#1", allow=[])
    capture(store, occurred=1003, event_id="run3#1", allow=[], revoke=[sub])
    factory().pump()
    record = rows(store, "subscriptions")[0]
    assert record["active"] == 0 and record["config"] == ""
    assert len(sent) == 2


def test_revocation_stops_a_queued_delivery(harness):
    factory, _, sent = harness
    store = factory()
    sub = store.subscribe("alice", params())["id"]
    capture(store)
    capture(store, event_id="run1#2", allow=[], revoke=[sub])
    assert [r["state"] for r in rows(store, "deliveries")] == ["stopped"]
    assert factory().pump() == 0
    assert len(sent) == 1


def test_key_rotation_signs_both_keys_then_expires_old(harness):
    factory, now, sent = harness
    store = factory()
    sub = store.subscribe("alice", params())
    changed = params()
    changed["delivery"]["secret"] = SECOND
    assert factory().subscribe("alice", changed)["id"] == sub["id"]
    capture(store)
    store.pump()
    assert len(sent[-1][2]["webhook-signature"].split()) == 2
    now[0] += 121
    capture(store, event_id="run2#1", occurred=1121)
    store.pump()
    assert len(sent[-1][2]["webhook-signature"].split()) == 1


def test_retry_restart_and_crashed_claim_are_bounded(harness):
    factory, now, sent = harness
    store = factory()
    store.subscribe("alice", params())
    capture(store)

    def transient(owner, url, body, headers):
        sent.append((url, json.loads(body), headers, body, owner))
        return 503, b""

    for attempt in range(8):
        factory(sender=transient).pump()
        record = rows(store, "deliveries")[0]
        assert record["attempts"] == attempt + 1
        now[0] = record["due"]
    assert record["state"] == "failed"
    factory(sender=transient).pump()
    assert len(sent) == 9
    assert len({item[2]["webhook-id"] for item in sent[1:]}) == 1
    assert len({item[2]["webhook-timestamp"] for item in sent[1:]}) == 8
    capture(store, event_id="run2#1", occurred=now[0])
    with store.db() as conn:
        conn.execute(
            "UPDATE deliveries SET state='sending',lease='dead',due=? WHERE state='pending'",
            (now[0] + 60,),
        )
    assert factory().pump() == 0
    now[0] += 61
    assert factory().pump() == 1
    assert rows(store, "deliveries")[-1]["state"] == "received"


def test_a_refused_egress_is_retried_not_dropped(harness):
    factory, _, _ = harness
    store = factory()
    store.subscribe("alice", params())
    capture(store)

    def refused(*args):
        from tinyassets.broker.egress import EgressPolicyRefused

        raise EgressPolicyRefused({"host": "receiver.example", "port": 443}, 1, "ask")

    factory(sender=refused).pump()
    record = rows(store, "deliveries")[0]
    assert (record["state"], record["attempts"]) == ("pending", 1)


@pytest.mark.parametrize("status", [301, 400, 401, 403, 410, 413])
def test_permanent_failures_are_not_retried(harness, status):
    factory, _, _ = harness
    store = factory()
    store.subscribe("alice", params())
    capture(store)
    factory(sender=lambda *a: (status, b"")).pump()
    row = rows(store, "deliveries")[0]
    assert row["state"] in {"failed", "stopped"}
    assert factory().pump() == 0
    if status == 410:
        assert rows(store, "subscriptions")[0]["config"] == ""


def test_standard_webhooks_exact_bytes_known_vector():
    body = '{"text":"hello \\u263a"}'.encode()
    header = signed_headers("sub", "evt", body, SECRET, now=1000)
    expected = base64.b64encode(
        hmac.new(b"s" * 32, b"evt.1000." + body, hashlib.sha256).digest()
    ).decode()
    assert header["webhook-signature"] == "v1," + expected


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example",
        "https://user:pass@public.example",
        "https://public.example:444",
        "https://public.example/#frag",
        "https://public.example/\r\nX:evil",
        "https://x\\@127.0.0.1/",
    ],
)
def test_bad_callback_urls(url):
    with pytest.raises(EventError):
        callback_url(url)


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "whsec_bad",
        "whsec_" + base64.b64encode(b"a" * 23).decode(),
        "whsec_" + base64.b64encode(b"a" * 65).decode(),
    ],
)
def test_invalid_secret(secret):
    with pytest.raises(EventError):
        signing_key(secret)


@pytest.mark.parametrize(
    "status,response",
    [(302, b"{}"), (200, b'{"challenge":"bad"}'), (200, b"[]"), (500, b"private response")],
)
def test_verification_fails_closed(status, response):
    with pytest.raises(EventError) as error:
        verify("sub", "https://receiver.example/", SECRET, sender=lambda *a: (status, response))
    assert error.value.code == -32015
    assert "private" not in str(error.value)


def test_real_authorizer_cross_owner_and_revoked_branch(monkeypatch, tmp_path):
    from tinyassets import daemon_server as ds

    monkeypatch.setattr("tinyassets.universe_owner.owner_of", lambda *a: "alice")
    monkeypatch.setattr(ds, "universe_is_private", lambda *a, **k: True)
    monkeypatch.setattr(
        ds,
        "universe_access_permission",
        lambda *a, **k: "admin" if k["actor_id"] == "alice" else "read",
    )
    monkeypatch.setattr(
        ds, "get_branch_definition", lambda *a, **k: {"author": "alice", "visibility": "private"}
    )
    args = params()["arguments"]
    authorize(tmp_path, "alice", args)
    with pytest.raises(PermissionError):
        authorize(tmp_path, "bob", args)
    monkeypatch.setattr(
        ds, "get_branch_definition", lambda *a, **k: {"author": "bob", "visibility": "private"}
    )
    with pytest.raises(PermissionError):
        authorize(tmp_path, "alice", args)


def test_filters_reject_nonfinite_and_nested():
    for invalid in ({"x": float("nan")}, {"x": {}}, {str(i): i for i in range(17)}):
        with pytest.raises(EventError):
            arguments(params()["arguments"] | {"input_equals": invalid})


@pytest.mark.parametrize("expired", [False, True])
def test_reactivation_cannot_bypass_owner_subscription_limit(harness, expired):
    factory, now, _ = harness
    store = factory()
    store.subscribe("alice", params(ttlMs=1000))
    if expired:
        now[0] += 2
    else:
        store.unsubscribe("alice", params())
    for index in range(MAX_SUBSCRIPTIONS):
        now[0] += 2  # Respect the separate durable verification rate limit.
        active = params()
        active["arguments"]["branch_def_id"] = f"save-{index}"
        store.subscribe("alice", active)
    # An existing active subscription may refresh at capacity.
    store.subscribe("alice", active)
    with pytest.raises(EventError) as error:
        store.subscribe("alice", params())
    assert error.value.code == -32025


def test_corrupt_subscription_revoked_without_starving_other_subscriptions(harness):
    factory, _, _ = harness
    store = factory()
    first = store.subscribe("alice", params())["id"]
    second = params()
    second["delivery"]["url"] += "/other"
    good = store.subscribe("alice", second)["id"]
    with store.db() as conn:
        conn.execute("UPDATE subscriptions SET config=? WHERE id=?", ("corrupted", first))
    capture(factory())
    records = {r["id"]: r for r in rows(store, "subscriptions")}
    assert records[first]["active"] == 0 and records[first]["config"] == ""
    assert records[good]["active"] == 1
    assert [r["subscription"] for r in rows(store, "deliveries")] == [good]


def test_verification_failures_are_bounded_across_restart_and_owner_scoped(harness):
    factory, now, _ = harness
    calls = []

    def failing(*args):
        calls.append(args)
        return 500, b"{}"

    for _ in range(VERIFICATION_LIMIT):
        with pytest.raises(EventError) as error:
            factory(sender=failing).subscribe("alice", params())
        assert error.value.code == -32015
    with pytest.raises(EventError) as error:
        factory(sender=failing).subscribe("alice", params())
    assert error.value.code == -32025 and len(calls) == VERIFICATION_LIMIT
    with pytest.raises(EventError) as error:
        factory(sender=failing).subscribe("bob", params())
    assert error.value.code == -32015 and len(calls) == VERIFICATION_LIMIT + 1
    now[0] += 60
    factory().subscribe("alice", params())
    assert len(rows(factory(), "subscriptions")) == 1


def test_cached_verified_refresh_needs_no_new_verification_budget(harness):
    factory, _, sent = harness
    store = factory()
    store.subscribe("alice", params())
    with store.db() as conn:
        conn.execute("UPDATE verification_budget SET attempts=32 WHERE owner='alice'")
    factory().subscribe("alice", params())
    assert len(sent) == 1


def test_concurrent_verifications_cannot_overdraw_budget(harness, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr("tinyassets.broker.mcp_events.VERIFICATION_LIMIT", 4)
    factory, _, _ = harness
    attempted = []

    def sender(*args):
        attempted.append(1)
        return 500, b"{}"

    def subscribe(_):
        try:
            factory(sender=sender).subscribe("alice", params())
        except EventError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(subscribe, range(16)))
    assert len(attempted) == codes.count(-32015) == 4
    assert codes.count(-32025) == 12


def test_canonical_owner_allows_owned_centers_but_not_delegated_admin(tmp_path, monkeypatch):
    from tinyassets import daemon_server as ds

    ds.grant_universe_ownership(tmp_path, universe_id="center", owner_id="alice")
    ds.grant_universe_access(tmp_path, universe_id="center", actor_id="bob",
                             permission="admin", granted_by="alice")
    monkeypatch.setattr(ds, "get_branch_definition", lambda *a, **k: {
        "author": "alice", "visibility": "public"})
    authorize(tmp_path, "alice", params()["arguments"])
    with pytest.raises(PermissionError):
        authorize(tmp_path, "bob", params()["arguments"])
    # Ownership is not inferred from admin or restricted to one founder home.
    ds.grant_universe_access(tmp_path, universe_id="unattributed", actor_id="alice",
                             permission="admin", granted_by="alice")
    with pytest.raises(PermissionError):
        authorize(tmp_path, "alice", params()["arguments"] | {"command_center_id": "unattributed"})
    ds.grant_universe_ownership(tmp_path, universe_id="second", owner_id="alice")
    authorize(tmp_path, "alice", params()["arguments"] | {"command_center_id": "second"})
    # A public owned center has no ACL rows; the canonical owner still qualifies.
    with ds._connect(tmp_path) as conn:
        conn.execute("DELETE FROM universe_acl WHERE universe_id='center'")
    authorize(tmp_path, "alice", params()["arguments"])
    with pytest.raises(PermissionError):
        authorize(tmp_path, "bob", params()["arguments"])


@pytest.mark.parametrize("doc", [
    {"op": "MCP_EVENTS", "action": "subscribe", "event": EVENT},
    {"op": "MCP_EVENTS", "action": "match", "event": EVENT, "owner": "bob"},
    {"op": "MCP_EVENTS", "action": "capture", "event": EVENT, "allow": []},
])
def test_box_operation_refuses_anything_but_its_two_reports(harness, doc):
    factory, _, _ = harness
    with pytest.raises(ValueError):
        box_operation(factory(), "alice", doc)


def test_refusals_never_carry_the_callback_or_key():
    assert error_answer(PermissionError("https://receiver.example " + SECRET)) == {
        "code": -32024, "message": "Event access unavailable"}
    assert error_answer(OSError("https://receiver.example")) == {
        "code": -32603, "message": "Event service unavailable"}


def test_a_failed_delivery_is_retried_by_the_clock_alone(harness):
    """P0-5: a delivery that fails is retried on a later tick with nothing else running."""
    factory, now, sent = harness
    store = factory()
    store.subscribe("alice", params())
    capture(store)
    status = [503]

    def flaky(owner, url, body, headers):
        sent.append((url, json.loads(body), headers, body, owner))
        return status[0], b""

    store.sender = flaky
    deliveries = EventDeliveries(store)

    async def tick():
        deliveries.kick()
        await deliveries._task

    asyncio.run(tick())
    assert rows(store, "deliveries")[0]["state"] == "pending"
    status[0] = 202
    now[0] = rows(store, "deliveries")[0]["due"]
    asyncio.run(tick())  # The next waker tick: no box, no capture, no subscribe.
    assert rows(store, "deliveries")[0]["state"] == "received"
    assert [item[2]["webhook-id"] for item in sent[1:]] == [sent[1][2]["webhook-id"]] * 2
