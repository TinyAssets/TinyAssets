"""Blind generality pass. These documents were written against the public
shared-state surface and do not name a game the platform knows.

A spec that cannot be stored or operated as app data fails here. Specs that
already pass are left as they are. This file does not add a platform path.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tinyassets import shared_state
from tinyassets.daemon_server import (
    grant_universe_access,
    initialize_author_server,
    set_founder_home,
)
from tinyassets.shared_state import (
    definition_count,
    finish_run,
    pump_runs,
    read_record,
    runs_for,
    save_app,
    sync,
    transfer,
    transition,
    write_record,
)
from tinyassets.storage import _connect
from tinyassets.universe_owner import record_creation
from tinyassets.universe_seats import account_pool, ledger_path, occupancy


def _account(base: Path, actor: str, home: str) -> None:
    initialize_author_server(base)
    (base / home).mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        base, universe_id=home, actor_id=actor, permission="admin", granted_by=actor,
    )
    set_founder_home(base, founder_sub=actor, universe_id=home)
    with _connect(base) as conn:
        record_creation(conn, universe_id=home, owner_id=actor)


def _app(base: Path, owner: str, universe: str, document: dict) -> None:
    _account(base, owner, f"home-{owner}")
    initialize_author_server(base)
    (base / universe).mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        base, universe_id=universe, actor_id=owner, permission="admin", granted_by=owner,
    )
    with _connect(base) as conn:
        record_creation(conn, universe_id=universe, owner_id=owner)
    save_app(base, actor=owner, universe_id=universe, document=document)


def _join(base: Path, owner: str, universe: str, actor: str) -> None:
    _account(base, actor, f"home-{actor}")
    token = shared_state.mint_invite(base, actor=owner, universe_id=universe)["token"]
    shared_state.redeem_invite(base, token=token, actor=actor)


def _pct(samples: list[float], percent: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def test_blind_large_session_is_only_app_data(tmp_path: Path, capsys):
    """One session, many accounts, each with its own records. No new kind of API."""
    universe = "app-crowd"
    document = {
        "schema": "app/v1",
        "records": {
            "marker": {
                "ownership": "creator", "read": "public", "write": "owner",
                "public": ["n"], "secret": [],
            },
        },
        "sync": ["marker"],
    }
    _app(tmp_path, "owner", universe, document)
    count = 64
    players = [f"c{i}" for i in range(count)]
    for player in players:
        _join(tmp_path, "owner", universe, player)
        write_record(
            tmp_path, actor=player, universe_id=universe, kind="marker", key="marker",
            body={"n": 0}, expect_etag=0,
        )
    latencies: list[float] = []
    errors: list[str] = []

    def step(player: str) -> None:
        etag = 1
        for n in range(1, 4):
            began = time.perf_counter()
            try:
                result = write_record(
                    tmp_path, actor=player, universe_id=universe, kind="marker",
                    key="marker", body={"n": n}, expect_etag=etag,
                )
            except Exception as exc:  # noqa: BLE001 - the ramp records the break
                errors.append(type(exc).__name__)
                return
            latencies.append((time.perf_counter() - began) * 1000)
            etag = int(result["etag"])

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(step, players))
    elapsed = time.perf_counter() - started
    page = sync(tmp_path, actor=players[0], universe_id=universe, since=0, limit=2000)
    seen = {row["key"] for row in page["events"]}
    stats = {
        "players": count,
        "elapsed_s": round(elapsed, 4),
        "writes": len(latencies),
        "errors": len(errors),
        "p50_ms": round(_pct(latencies, 50), 3) if latencies else None,
        "p99_ms": round(_pct(latencies, 99), 3) if latencies else None,
        "synced_keys": len(seen),
    }
    print("BLIND_LARGE", stats, flush=True)
    assert not errors
    assert len(latencies) == count * 3
    assert definition_count(tmp_path, universe) == 1
    assert len(seen) == count
    assert "BLIND_LARGE" in capsys.readouterr().out


def test_blind_rules_change_mid_session(tmp_path: Path):
    """A member authors a proposal. The owner replaces the one document at its etag."""
    universe = "app-mod"
    document = {
        "schema": "app/v1",
        "records": {
            "piece": {
                "ownership": "creator", "read": "members", "write": "transition",
                "public": ["state", "rule"], "secret": [],
            },
            "proposal": {
                "ownership": "creator", "read": "members", "write": "owner",
                "public": ["text"], "secret": [],
            },
        },
        "transitions": [{
            "name": "open", "kind": "piece", "from": ["*"], "to": "open",
            "actor": "owner", "set": ["rule"],
        }],
        "sync": ["piece", "proposal"],
    }
    _app(tmp_path, "owner", universe, document)
    _join(tmp_path, "owner", universe, "modder")
    write_record(
        tmp_path, actor="modder", universe_id=universe, kind="proposal", key="proposal",
        body={"text": "allow close"}, expect_etag=0,
    )
    opened = transition(
        tmp_path, actor="modder", universe_id=universe, key="piece", name="open",
        expect_etag=0, patch={"rule": "v1"},
    )
    revised = {
        **document,
        "transitions": document["transitions"] + [{
            "name": "close", "kind": "piece", "from": ["open"], "to": "closed",
            "actor": "owner", "set": ["rule"],
        }],
    }
    revise = getattr(shared_state, "revise_app", None)
    if revise is None:
        pytest.fail("platform cannot replace the one app document at its etag")
    with pytest.raises(PermissionError):
        revise(
            tmp_path, actor="modder", universe_id=universe, document=revised, expect_etag=1,
        )
    stored = revise(
        tmp_path, actor="owner", universe_id=universe, document=revised, expect_etag=1,
    )
    assert stored["etag"] == 2
    assert definition_count(tmp_path, universe) == 1
    with pytest.raises(shared_state.StateConflict):
        revise(
            tmp_path, actor="owner", universe_id=universe, document=revised, expect_etag=1,
        )
    closed = transition(
        tmp_path, actor="modder", universe_id=universe, key=opened["key"],
        name="close", expect_etag=opened["etag"], patch={"rule": "v2"},
    )
    assert closed["state"] == "closed"
    with pytest.raises(PermissionError):
        save_app(tmp_path, actor="owner", universe_id=universe, document=revised)


def test_blind_item_moves_across_apps_without_a_copy(tmp_path: Path):
    """One scarce field is debited in one app and credited in another."""
    shared = {
        "schema": "app/v1",
        "records": {
            "holding": {
                "ownership": "creator", "read": "members", "write": "transfer",
                "public": ["qty"], "secret": [],
            },
        },
        "transfers": [{
            "name": "carry", "kind": "holding", "field": "qty", "conserve": True,
        }],
        "sync": ["holding"],
    }
    _app(tmp_path, "owner", "app-left", shared)
    _app(tmp_path, "owner", "app-right", shared)
    _join(tmp_path, "owner", "app-left", "porter")
    _join(tmp_path, "owner", "app-right", "porter")
    left = write_record(
        tmp_path, actor="porter", universe_id="app-left", kind="holding", key="bag",
        body={"qty": 5}, expect_etag=0,
    )
    right = write_record(
        tmp_path, actor="porter", universe_id="app-right", kind="holding", key="bag",
        body={"qty": 1}, expect_etag=0,
    )
    moved = transfer(
        tmp_path, actor="porter", universe_id="app-left", name="carry",
        moves=[
            {"key": left["key"], "delta": -2, "etag": left["etag"], "universe_id": "app-left"},
            {
                "key": right["key"], "delta": 2, "etag": right["etag"],
                "universe_id": "app-right",
            },
        ],
    )
    assert moved["moves"][0]["qty"] == 3
    assert moved["moves"][1]["qty"] == 3
    assert definition_count(tmp_path, "app-left") == 1
    assert definition_count(tmp_path, "app-right") == 1
    again_left = read_record(
        tmp_path, actor="porter", universe_id="app-left", key=left["key"],
    )
    again_right = read_record(
        tmp_path, actor="porter", universe_id="app-right", key=right["key"],
    )
    assert again_left["owner"] == "porter"
    assert again_right["owner"] == "porter"
    assert again_left["body"]["qty"] == 3
    assert again_right["body"]["qty"] == 3


def test_blind_server_steps_motion_without_a_run(tmp_path: Path, monkeypatch):
    """Position advances on a clock. The client cannot write that field after birth."""
    universe = "app-motion"

    def trap(*_args, **_kwargs):
        raise AssertionError("a motion step asked for a seat or a run")

    monkeypatch.setattr("tinyassets.universe_seats.try_acquire", trap)
    monkeypatch.setattr("tinyassets.universe_seats.acquire", trap)
    document = {
        "schema": "app/v1",
        "records": {
            "body": {
                "ownership": "creator", "read": "public", "write": "owner",
                "public": ["where", "velocity"], "secret": [],
                "server": ["where"],
            },
        },
        "motion": [{
            "kind": "body", "position": "where", "velocity": "velocity", "tick_s": 0.5,
        }],
        "sync": ["body"],
    }
    _app(tmp_path, "owner", universe, document)
    _join(tmp_path, "owner", universe, "pilot")
    born = write_record(
        tmp_path, actor="pilot", universe_id=universe, kind="body", key="body",
        body={"where": [0, 0], "velocity": [2, 0]}, expect_etag=0,
    )
    advance = getattr(shared_state, "advance", None)
    if advance is None:
        pytest.fail("platform has no clock step for declared numeric motion")
    stepped = advance(tmp_path, universe_id=universe, now=born.get("sim_at", 0) + 1.0)
    assert stepped
    seen = read_record(
        tmp_path, actor="pilot", universe_id=universe, key=born["key"],
    )
    assert seen["body"]["where"] == [2.0, 0.0] or seen["body"]["where"] == [2, 0]
    # A later client write may change velocity and must leave position alone.
    write_record(
        tmp_path, actor="pilot", universe_id=universe, kind="body", key="body",
        body={"where": [99, 99], "velocity": [0, 4]}, expect_etag=seen["etag"],
    )
    held = read_record(tmp_path, actor="pilot", universe_id=universe, key=born["key"])
    assert held["body"]["where"] == seen["body"]["where"]
    assert held["body"]["velocity"] == [0, 4]
    assert runs_for(tmp_path, "pilot") == []
    assert runs_for(tmp_path, "owner") == []


def test_blind_npcs_keep_a_schedule_while_nobody_is_present(tmp_path: Path, monkeypatch):
    """The document asks for a repeating run. The host pumps it. No client enqueues it."""
    universe = "app-world"
    document = {
        "schema": "app/v1",
        "records": {
            "npc": {
                "ownership": "app", "read": "public", "write": "app",
                "public": ["where"], "secret": [],
            },
        },
        "runs": [{"name": "npc-tick", "actor": "app", "every_s": 30}],
        "sync": ["npc"],
    }
    monkeypatch.setattr(
        "tinyassets.shared_state.starter_sources",
        lambda _base, account: frozenset({f"source-{account}"}),
    )
    _app(tmp_path, "owner", universe, document)
    for index in range(40):
        write_record(
            tmp_path, actor="owner", universe_id=universe, kind="npc", key=f"npc-{index}",
            body={"where": [index, 0]}, expect_etag=0,
        )
    assert runs_for(tmp_path, "owner") == []
    first = pump_runs(tmp_path, now=1_000.0)
    due = [row for row in first if row.get("name") == "npc-tick" or row["state"] == "running"]
    assert due, "a scheduled run did not start with no client enqueue"
    running = [row for row in due if row["state"] == "running"]
    assert running
    assert running[0]["owner"] == "owner"
    assert running[0]["provider"] == "source-owner"
    held = occupancy(account_pool("owner"), db=ledger_path(tmp_path))
    assert int(held["running"]) == 1
    # Still inside the interval, and one run is in flight: no second seat.
    again = pump_runs(tmp_path, now=1_010.0)
    assert [row for row in again if row["state"] == "running"] == []
    finish_run(tmp_path, running[0]["run_id"])
    early = pump_runs(tmp_path, now=1_020.0)
    assert [row for row in early if row["state"] == "running"] == []
    nxt = pump_runs(tmp_path, now=1_030.0)
    started = [row for row in nxt if row["state"] == "running"]
    assert len(started) == 1
    assert started[0]["owner"] == "owner"
    assert definition_count(tmp_path, universe) == 1
    finish_run(tmp_path, started[0]["run_id"])
