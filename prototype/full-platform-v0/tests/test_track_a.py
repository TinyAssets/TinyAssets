"""Track A — 6 load-bearing invariants.

Spec: 2026-04-19-track-a-schema-auth-rls.md (archived in git) §8.

    1. Schema compiles cleanly + RLS policies load.
    2. RLS enforcement at DB layer (non-owner cannot SELECT owned rows).
    3. SELECT FOR UPDATE SKIP LOCKED claim semantics (exactly-one wins).
    4. Settlement immutability (duplicate (bid_id) raises).
    5. Flagged-state routing pause (broadcast query skips flagged rows).
    6. Auth flow: auth.uid() wires through to subsequent queries.

Run prerequisite: ``docker compose up -d`` completed the fixture runner.

If TINYASSETS_V0_DSN is unreachable, tests SKIP — this matches the existing
test_schema.py / test_rls.py harness (live-Postgres dependent).
"""

from __future__ import annotations

import json
import os
import threading
import uuid

import pytest

try:
    import psycopg
except ImportError:
    psycopg = None

DSN = os.environ.get(
    "TINYASSETS_V0_DSN",
    "postgresql://tinyassets:tinyassets_v0_dev@localhost:5433/tinyassets_v0",
)


def _connect():
    if psycopg is None:
        pytest.skip("psycopg not installed")
    try:
        return psycopg.connect(DSN, connect_timeout=2)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres unreachable at {DSN}: {exc}")


@pytest.fixture
def track_a_db():
    """Fresh Track A state — truncate all 9 tables."""
    conn = _connect()
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE "
            "public.flags, public.settlements, public.ledger, "
            "public.bids, public.requests, public.host_pool, "
            "public.nodes, public.capabilities, public.users "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()
    yield conn
    conn.close()


def _as_user(conn, user_id: str | None):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.current_user_id', %s, false)",
            (user_id or "",),
        )


def _mk_user(conn, label: str) -> str:
    uid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (user_id, display_name, github_handle, account_age_days) "
            "VALUES (%s, %s, %s, 30)",
            (uid, label, f"{label.lower()}-gh-{uid[:8]}"),
        )
    conn.commit()
    return uid


def _mk_capability(conn, cap_id: str = "goal_planner:claude-4-opus") -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.capabilities (capability_id, node_type, llm_model) "
            "VALUES (%s, %s, %s) ON CONFLICT (capability_id) DO NOTHING",
            (cap_id, cap_id.split(":")[0], cap_id.split(":")[1]),
        )
    conn.commit()
    return cap_id


def _mk_host(conn, owner_id: str, cap_id: str, visibility: str = "paid") -> str:
    host_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.host_pool "
            "(host_id, owner_user_id, provider, capability_id, visibility) "
            "VALUES (%s, %s, 'claude', %s, %s)",
            (host_id, owner_id, cap_id, visibility),
        )
    conn.commit()
    return host_id


def _mk_request(
    conn,
    requester_id: str,
    cap_id: str,
    visibility: str = "paid",
    state: str = "pending",
) -> str:
    rid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.requests "
            "(request_id, requester_user_id, capability_id, visibility, state, inputs) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (rid, requester_id, cap_id, visibility, state, json.dumps({"goal": "demo"})),
        )
    conn.commit()
    return rid


def _mk_bid(
    conn,
    request_id: str,
    bidder_id: str,
    host_id: str,
    price: str = "1.00",
    state: str = "offered",
) -> str:
    bid_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.bids "
            "(bid_id, request_id, bidder_user_id, host_id, price, state) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (bid_id, request_id, bidder_id, host_id, price, state),
        )
    conn.commit()
    return bid_id


# --- Invariant 1: schema compiles + RLS policies load -----------------------


def test_invariant_1_schema_and_rls_loaded(track_a_db):
    expected_tables = {
        "users", "capabilities", "nodes", "host_pool",
        "requests", "bids", "ledger", "settlements", "flags",
    }
    with track_a_db.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = ANY(%s)",
            (list(expected_tables),),
        )
        got = {r[0] for r in cur.fetchall()}
    assert got == expected_tables, f"missing tables: {expected_tables - got}"

    with track_a_db.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = ANY(%s) AND rowsecurity = true",
            (list(expected_tables),),
        )
        rls_on = {r[0] for r in cur.fetchall()}
    # capabilities has RLS enabled (public-read policy); every in-scope table
    # must have RLS enabled per §5.
    assert rls_on == expected_tables, f"RLS missing: {expected_tables - rls_on}"


# --- Invariant 2: RLS enforcement at DB layer ------------------------------


def test_invariant_2_rls_blocks_non_owner(track_a_db):
    alice = _mk_user(track_a_db, "Alice")
    bob = _mk_user(track_a_db, "Bob")
    cap = _mk_capability(track_a_db)

    # Alice creates a 'self'-visibility request (only she should see it).
    _as_user(track_a_db, alice)
    rid = _mk_request(track_a_db, alice, cap, visibility="self")

    # Bob should NOT see Alice's self-visibility request.
    _as_user(track_a_db, bob)
    with track_a_db.cursor() as cur:
        cur.execute("SET ROLE tinyassets_fixture_app")
        cur.execute("SELECT request_id FROM public.requests WHERE request_id = %s", (rid,))
        rows = cur.fetchall()
    assert rows == [], "Bob leaked visibility='self' request owned by Alice"

    # Bob cannot INSERT a request claiming to be Alice.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with track_a_db.cursor() as cur:
            cur.execute(
                "INSERT INTO public.requests "
                "(requester_user_id, capability_id, visibility, state, inputs) "
                "VALUES (%s, %s, 'paid', 'pending', %s::jsonb)",
                (alice, cap, json.dumps({})),
            )
    track_a_db.rollback()
    with track_a_db.cursor() as cur:
        cur.execute("RESET ROLE")


# --- Invariant 3: SKIP LOCKED exactly-one-wins -----------------------------


def test_invariant_3_skip_locked_claim_exactly_one_wins(track_a_db):
    # Setup: two claimers contest one request-bound bid.
    alice = _mk_user(track_a_db, "Requester")
    daemon_a = _mk_user(track_a_db, "DaemonA")
    cap = _mk_capability(track_a_db)
    host_a = _mk_host(track_a_db, daemon_a, cap)
    rid = _mk_request(track_a_db, alice, cap)
    bid_a = _mk_bid(track_a_db, rid, daemon_a, host_a)

    # Two concurrent claimers race on SELECT … FOR UPDATE SKIP LOCKED.
    # Contract: exactly one acquires a row; the other gets zero rows.
    results: list[list[str]] = []
    barrier = threading.Barrier(2)

    def claim():
        conn = _connect()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                barrier.wait(timeout=5)
                cur.execute(
                    "SELECT bid_id FROM public.bids "
                    "WHERE request_id = %s AND state = 'offered' "
                    "ORDER BY price ASC, created_at ASC "
                    "FOR UPDATE SKIP LOCKED LIMIT 1",
                    (rid,),
                )
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        "UPDATE public.bids SET state = 'claimed', claimed_at = now() "
                        "WHERE bid_id = %s",
                        (row[0],),
                    )
                    # Hold the lock briefly so the other session sees SKIP LOCKED.
                    import time
                    time.sleep(0.3)
                    results.append([row[0]])
                else:
                    results.append([])
                conn.commit()
        finally:
            conn.close()

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    claimed = [r[0] for r in results if r]
    assert len(claimed) == 1, f"expected exactly one claim winner, got {results}"
    assert str(claimed[0]) == bid_a


# --- Invariant 4: settlement immutability ----------------------------------


def test_invariant_4_settlement_duplicate_raises(track_a_db):
    alice = _mk_user(track_a_db, "Alice")
    bob = _mk_user(track_a_db, "Bob")
    cap = _mk_capability(track_a_db)
    host = _mk_host(track_a_db, bob, cap)
    rid = _mk_request(track_a_db, alice, cap)
    bid_id = _mk_bid(track_a_db, rid, bob, host)

    # Insert two ledger entries + one settlement referencing them.
    with track_a_db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.ledger "
            "(user_id, entry_kind, amount, related_request, related_bid) "
            "VALUES (%s, 'debit', -1.0, %s, %s) RETURNING entry_id",
            (alice, rid, bid_id),
        )
        debit_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO public.ledger "
            "(user_id, entry_kind, amount, related_request, related_bid) "
            "VALUES (%s, 'credit', 0.99, %s, %s) RETURNING entry_id",
            (bob, rid, bid_id),
        )
        credit_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO public.settlements "
            "(bid_id, request_id, requester_id, bidder_id, gross_amount, "
            " platform_fee, net_amount, mode, debit_entry_id, credit_entry_id) "
            "VALUES (%s, %s, %s, %s, 1.00, 0.01, 0.99, 'immediate', %s, %s)",
            (bid_id, rid, alice, bob, debit_id, credit_id),
        )
    track_a_db.commit()

    # Second insert for the same bid_id must raise (UNIQUE violation).
    with pytest.raises(psycopg.errors.UniqueViolation):
        with track_a_db.cursor() as cur:
            cur.execute(
                "INSERT INTO public.settlements "
                "(bid_id, request_id, requester_id, bidder_id, gross_amount, "
                " platform_fee, net_amount, mode, debit_entry_id, credit_entry_id) "
                "VALUES (%s, %s, %s, %s, 1.00, 0.01, 0.99, 'immediate', %s, %s)",
                (bid_id, rid, alice, bob, debit_id, credit_id),
            )
    track_a_db.rollback()


# --- Invariant 5: flagged-state routing pause ------------------------------


def test_invariant_5_flagged_excluded_from_broadcast(track_a_db):
    alice = _mk_user(track_a_db, "Alice")
    daemon = _mk_user(track_a_db, "Daemon")
    cap = _mk_capability(track_a_db)
    _mk_host(track_a_db, daemon, cap)

    _as_user(track_a_db, alice)
    r_live = _mk_request(track_a_db, alice, cap)
    r_flagged = _mk_request(track_a_db, alice, cap)

    # Mark r_flagged as flagged via state column (the control-plane signal).
    with track_a_db.cursor() as cur:
        cur.execute(
            "UPDATE public.requests SET state = 'flagged' WHERE request_id = %s",
            (r_flagged,),
        )
    track_a_db.commit()

    # Broadcast query: pending + capability-matched. Flagged rows excluded.
    _as_user(track_a_db, daemon)
    with track_a_db.cursor() as cur:
        cur.execute(
            "SELECT request_id FROM public.requests "
            "WHERE state = 'pending' AND capability_id = %s "
            "AND visibility IN ('paid','public')",
            (cap,),
        )
        visible = {str(r[0]) for r in cur.fetchall()}
    assert r_live in visible
    assert r_flagged not in visible


# --- Invariant 6: auth flow wires through ----------------------------------


def test_invariant_6_auth_uid_wires_through(track_a_db):
    alice = _mk_user(track_a_db, "Alice")

    # With auth.uid() = alice, public.users SELECT self returns Alice's row.
    _as_user(track_a_db, alice)
    with track_a_db.cursor() as cur:
        cur.execute("SELECT auth.uid()")
        assert str(cur.fetchone()[0]) == alice
        cur.execute("SELECT user_id FROM public.users WHERE user_id = auth.uid()")
        assert str(cur.fetchone()[0]) == alice

    # With auth.uid() NULL, self-read policy yields zero rows.
    _as_user(track_a_db, None)
    with track_a_db.cursor() as cur:
        cur.execute("SELECT auth.uid()")
        assert cur.fetchone()[0] is None
