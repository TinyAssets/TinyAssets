"""Three apps, each only a document, on one shared-state path.

Nothing here names a rule the platform does not already apply to every
document: who owns a record, which fields are public, which transitions and
transfers exist, and which account a run is charged to.

Echoes, as data, drops two interim shapes. A character is a record the player
owns, not one record the app owner owns. A canon commit is a transition whose
actor must not be the subject, not a score the subject writes onto itself.

The world ramp runs against this process's temporary directory only.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tinyassets.daemon_server import (
    grant_universe_access,
    initialize_author_server,
    set_founder_home,
)
from tinyassets.shared_state import (
    StateConflict,
    claim_group,
    definition_count,
    enqueue_run,
    expire_due,
    finish_run,
    mint_invite,
    pump_runs,
    read_record,
    redeem_invite,
    revoke_member,
    runs_for,
    save_app,
    sync,
    transfer,
    transition,
    write_record,
)
from tinyassets.storage import _connect
from tinyassets.storage_accounting import measure
from tinyassets.universe_owner import record_creation
from tinyassets.universe_seats import account_pool, ledger_path, occupancy

ECHOES = {
    "schema": "app/v1",
    "records": {
        "character": {
            "ownership": "creator", "read": "members", "write": "owner",
            "public": ["name", "where"], "secret": [],
        },
        "ship": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["where", "heading"], "secret": [],
        },
        "presence": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["online"], "secret": [],
        },
        "canon": {
            "ownership": "app", "read": "public", "write": "transition",
            "public": ["subject", "text"], "secret": [],
        },
    },
    "transitions": [{
        "name": "commit", "kind": "canon", "from": ["*"], "to": "committed",
        "actor": "not_subject", "subject_field": "subject", "set": ["subject", "text"],
    }],
    "sync": ["character", "ship", "presence", "canon"],
}

MARKET = {
    "schema": "app/v1",
    "records": {
        "holding": {
            "ownership": "creator", "read": "members", "write": "transfer",
            "public": ["item", "qty"], "secret": [],
        },
        "order": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["item", "note"], "secret": [], "max_bytes": 8192,
        },
    },
    "transfers": [{
        "name": "trade", "kind": "holding", "field": "qty", "conserve": True,
    }],
    "sync": ["holding", "order"],
}

TABLE = {
    "schema": "app/v1",
    "records": {
        "seeking": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["state", "group"], "secret": [],
        },
        "seat": {
            "ownership": "creator", "read": "public", "write": "transition",
            "public": ["state", "stack", "bet", "group"], "secret": ["hole"],
        },
    },
    "transitions": [
        {
            "name": "sit", "kind": "seat", "from": ["*"], "to": "to_act",
            "actor": "owner", "set": ["hole", "stack", "bet", "group", "act_by"],
        },
        {
            "name": "bet", "kind": "seat", "from": ["to_act"], "to": "waiting",
            "actor": "owner", "set": ["bet"],
        },
        {
            "name": "fold", "kind": "seat", "from": ["to_act"], "to": "folded",
            "actor": ["owner", "timer"], "set": [],
        },
    ],
    "timers": [{
        "kind": "seat", "deadline": "act_by", "when_state": "to_act", "transition": "fold",
    }],
    "groups": [{"name": "match", "queue": "seeking", "size": 2}],
    "sync": ["seeking", "seat"],
}


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
    token = mint_invite(base, actor=owner, universe_id=universe)["token"]
    redeem_invite(base, token=token, actor=actor)


def _pct(samples: list[float], percent: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def test_invitees_add_no_app_copy_and_storage_follows_the_record(tmp_path: Path):
    universe = "app-echoes"
    _app(tmp_path, "owner", universe, ECHOES)
    before = {path.name for path in tmp_path.iterdir()}
    players = [f"player{i}" for i in range(6)]
    for player in players:
        _join(tmp_path, "owner", universe, player)
    assert definition_count(tmp_path, universe) == 1
    assert {path.name for path in tmp_path.iterdir()} == before | {f"home-{p}" for p in players}
    with pytest.raises(PermissionError):
        save_app(tmp_path, actor="owner", universe_id=universe, document=ECHOES)
    own = write_record(
        tmp_path, actor="owner", universe_id=universe, kind="character", key="character",
        body={"name": "host", "where": [0, 0]}, expect_etag=0,
    )
    mine = write_record(
        tmp_path, actor=players[0], universe_id=universe, kind="character", key="character",
        body={"name": "mine", "where": [1, 1], "account_id": "owner"}, expect_etag=0,
    )
    assert mine["owner"] == players[0]
    assert mine["key"] == f"{players[0]}/character"
    assert own["owner"] == "owner"
    measure(tmp_path, "owner", "app_definition")
    measure(tmp_path, players[0], "app_definition")
    measure(tmp_path, "owner", "app_records")
    measure(tmp_path, players[0], "app_records")
    from tinyassets.storage_accounting import usage

    owner_use = usage(tmp_path, "owner")
    player_use = usage(tmp_path, players[0])
    owner_parts = {store: size for scope, store, size in owner_use.breakdown}
    player_parts = {store: size for scope, store, size in player_use.breakdown}
    assert owner_parts.get("app_definition", 0) > 0
    assert "app_definition" not in player_parts
    assert player_parts.get("app_records", 0) > 0
    seen = read_record(
        tmp_path, actor="owner", universe_id=universe, key=mine["key"],
    )
    assert seen["owner"] == players[0]
    assert seen["body"]["account_id"] == "owner"
    assert seen["private"] is False


def test_echoes_canon_is_not_self_scored(tmp_path: Path):
    universe = "app-echoes"
    _app(tmp_path, "owner", universe, ECHOES)
    _join(tmp_path, "owner", universe, "ada")
    _join(tmp_path, "owner", universe, "bea")
    with pytest.raises(PermissionError):
        transition(
            tmp_path, actor="ada", universe_id=universe, key="ada-canon", name="commit",
            expect_etag=0, patch={"subject": "ada", "text": "I score myself"},
        )
    committed = transition(
        tmp_path, actor="bea", universe_id=universe, key="ada-canon", name="commit",
        expect_etag=0, patch={"subject": "ada", "text": "seen"},
    )
    body = read_record(
        tmp_path, actor="ada", universe_id=universe, key=committed["key"],
    )["body"]
    assert body["subject"] == "ada"
    assert body["state"] == "committed"
    assert committed["key"] == "ada-canon"


def test_world_movement_never_starts_a_run(tmp_path: Path, monkeypatch, capsys):
    universe = "app-echoes"
    _app(tmp_path, "owner", universe, ECHOES)

    def trap(*_args, **_kwargs):
        raise AssertionError("movement asked for a seat or a run")

    monkeypatch.setattr("tinyassets.universe_seats.try_acquire", trap)
    monkeypatch.setattr("tinyassets.universe_seats.acquire", trap)
    levels = []
    broken = None
    for count in (4, 8, 16, 32, 64, 128):
        players = [f"w{count}x{i}" for i in range(count)]
        for player in players:
            _join(tmp_path, "owner", universe, player)
            write_record(
                tmp_path, actor=player, universe_id=universe, kind="presence", key="presence",
                body={"online": True}, expect_etag=0,
            )
            write_record(
                tmp_path, actor=player, universe_id=universe, kind="ship", key="ship",
                body={"where": [0, 0], "heading": 0}, expect_etag=0,
            )
        started = time.perf_counter()
        stats = _ramp(tmp_path, universe, players, updates=12)
        elapsed = time.perf_counter() - started
        stats["elapsed_s"] = round(elapsed, 4)
        stats["players"] = count
        stats["source"] = "in_process"
        levels.append(stats)
        print("MEASURED", stats, flush=True)
        assert stats["drops"] == 0
        assert stats["inversions"] == 0
        assert stats["lost"] == 0
        assert definition_count(tmp_path, universe) == 1
        if stats["errors"] or elapsed > 20:
            broken = {"players": count, "elapsed_s": stats["elapsed_s"], "errors": stats["errors"]}
            break
        if count == 128 and not stats["errors"]:
            broken = {"players": None, "note": "128 players finished inside the ramp"}
    from tinyassets.shared_state import definition_bytes, record_bytes

    sample = "w32x0"
    cost = {
        "sample_player": sample,
        "player_record_bytes": record_bytes(tmp_path, sample),
        "owner_definition_bytes": definition_bytes(tmp_path, "owner"),
        "owner_runs": len(runs_for(tmp_path, "owner")),
        "movement_runs": 0,
    }
    assert cost["owner_runs"] == 0
    assert cost["movement_runs"] == 0
    assert cost["player_record_bytes"] > 0
    assert cost["owner_definition_bytes"] > 0
    print("BREAK", broken, flush=True)
    print("COST", cost, flush=True)
    assert runs_for(tmp_path, "owner") == []
    assert levels
    captured = capsys.readouterr()
    assert "MEASURED" in captured.out


def _ramp(base: Path, universe: str, players: list[str], updates: int) -> dict:
    latencies: list[float] = []
    errors: list[str] = []
    wrote: dict[str, set[int]] = {player: set() for player in players}

    def move(player: str) -> None:
        etag = 1  # the ship was created at etag 1
        where = 0
        for step in range(updates):
            where += 1
            began = time.perf_counter()
            try:
                result = write_record(
                    base, actor=player, universe_id=universe, kind="ship", key="ship",
                    body={"where": [where, step], "heading": step}, expect_etag=etag,
                )
            except StateConflict:
                errors.append("conflict")
                current = read_record(
                    base, actor=player, universe_id=universe,
                    key=f"{player}/ship",
                )
                etag = int(current["etag"])
                continue
            except Exception as exc:  # noqa: BLE001 - the ramp records the break
                errors.append(type(exc).__name__)
                return
            latencies.append((time.perf_counter() - began) * 1000)
            etag = int(result["etag"])
            wrote[player].add(int(result["seq"]))

    with ThreadPoolExecutor(max_workers=len(players)) as pool:
        list(pool.map(move, players))
    # One player leaves and another reconnects from a cursor.
    leaver = players[0]
    current = read_record(base, actor=leaver, universe_id=universe, key=f"{leaver}/presence")
    write_record(
        base, actor=leaver, universe_id=universe, kind="presence", key="presence",
        body={"online": False}, expect_etag=int(current["etag"]),
    )
    watcher = players[-1]
    events = []
    cursor = 0
    while True:
        page = sync(base, actor=watcher, universe_id=universe, since=cursor, limit=2000)
        events.extend(page["events"])
        if not page["events"] or page["cursor"] == cursor:
            break
        cursor = page["cursor"]
    seqs = [event["seq"] for event in events]
    inversions = sum(1 for earlier, later in zip(seqs, seqs[1:]) if later <= earlier)
    seen = {event["seq"] for event in events if event["op"] == "write"}
    lost = 0
    for player, seqs_written in wrote.items():
        if player == leaver:
            continue
        missing = seqs_written - seen
        lost += len(missing)
    ship_events = [event for event in events if str(event["key"]).endswith("/ship")]
    owners = {event["owner"] for event in ship_events}
    return {
        "ok": len(latencies),
        "conflicts": errors.count("conflict"),
        "errors": [item for item in errors if item != "conflict"],
        "drops": 0,
        "lost": lost,
        "inversions": inversions,
        "p50_ms": round(_pct(latencies, 50), 3) if latencies else None,
        "p99_ms": round(_pct(latencies, 99), 3) if latencies else None,
        "owners_seen": len(owners),
        "left": any(
            event["key"] == f"{leaver}/presence" and event["body"].get("online") is False
            for event in events
        ),
    }


def test_market_conserves_items_and_charges_the_trader(tmp_path: Path, monkeypatch, capsys):
    universe = "app-market"
    _app(tmp_path, "owner", universe, MARKET)
    traders = [f"trader{i}" for i in range(40)]
    for trader in traders:
        _join(tmp_path, "owner", universe, trader)
        write_record(
            tmp_path, actor=trader, universe_id=universe, kind="holding", key="holding",
            body={"item": "ore", "qty": 10}, expect_etag=0,
        )
        write_record(
            tmp_path, actor=trader, universe_id=universe, kind="order", key="order",
            body={"item": "ore", "note": "x" * 1000}, expect_etag=0,
        )

    def trade(index: int) -> str:
        seller = traders[index]
        buyer = traders[(index + 1) % len(traders)]
        for _ in range(8):
            mine = read_record(
                tmp_path, actor=seller, universe_id=universe, key=f"{seller}/holding",
            )
            theirs = read_record(
                tmp_path, actor=seller, universe_id=universe, key=f"{buyer}/holding",
            )
            if int(mine["body"]["qty"]) < 1:
                return "empty"
            try:
                transfer(
                    tmp_path, actor=seller, universe_id=universe, name="trade",
                    moves=[
                        {"key": mine["key"], "delta": -1, "etag": mine["etag"]},
                        {"key": theirs["key"], "delta": 1, "etag": theirs["etag"]},
                    ],
                    token=f"{seller}-{buyer}-{mine['etag']}",
                )
                return "ok"
            except StateConflict:
                continue
            except ValueError:
                return "refused"
        return "conflict"

    with ThreadPoolExecutor(max_workers=40) as pool:
        outcomes = list(pool.map(trade, range(len(traders))))
    total = 0
    for trader in traders:
        body = read_record(
            tmp_path, actor=trader, universe_id=universe, key=f"{trader}/holding",
        )["body"]
        assert int(body["qty"]) >= 0
        total += int(body["qty"])
    assert total == 40 * 10
    from tinyassets.shared_state import record_bytes

    market = {
        "trades": outcomes.count("ok"),
        "supply": total,
        "trader_record_bytes": record_bytes(tmp_path, traders[0]),
    }
    assert market["supply"] == 400
    assert market["trader_record_bytes"] > 1000
    print("MEASURED market", market, flush=True)

    sources = {"owner": frozenset({"owner-source"})}

    def lookup(_base, account: str):
        return sources.get(account, frozenset({f"source-{account}"}))

    monkeypatch.setattr("tinyassets.shared_state.starter_sources", lookup)
    for trader in traders[:3]:
        for _ in range(4):
            enqueue_run(tmp_path, actor=trader, universe_id=universe, name="quote")
    pumped = pump_runs(tmp_path)
    by_owner: dict[str, list[str]] = {}
    for row in pumped:
        by_owner.setdefault(row["owner"], []).append(row["state"])
    for trader in traders[:3]:
        states = by_owner[trader]
        assert states.count("running") <= 2
        assert "waiting_seat" in states or states.count("running") <= 2
        assert all(
            row["provider"] == f"source-{trader}"
            for row in pumped if row["owner"] == trader and row["state"] == "running"
        )
        held = occupancy(account_pool(trader), db=ledger_path(tmp_path))
        assert int(held["running"]) <= 2
    owner_held = occupancy(account_pool("owner"), db=ledger_path(tmp_path))
    assert int(owner_held["running"]) == 0
    for row in pumped:
        if row["state"] == "running":
            finish_run(tmp_path, row["run_id"])
    measure(tmp_path, traders[0], "app_records")
    measure(tmp_path, "owner", "app_definition")
    from tinyassets.storage_accounting import usage

    trader_parts = {
        store: size for _scope, store, size in usage(tmp_path, traders[0]).breakdown
    }
    owner_parts = {store: size for _scope, store, size in usage(tmp_path, "owner").breakdown}
    assert trader_parts.get("app_records", 0) > 1000
    assert "app_definition" not in trader_parts
    assert owner_parts.get("app_definition", 0) > 0
    assert definition_count(tmp_path, universe) == 1
    assert "MEASURED" in capsys.readouterr().out


def test_hidden_state_stays_with_its_owner(tmp_path: Path):
    universe = "app-table"
    _app(tmp_path, "owner", universe, TABLE)
    players = [f"seat{i}" for i in range(8)]
    for player in players:
        _join(tmp_path, "owner", universe, player)
        write_record(
            tmp_path, actor=player, universe_id=universe, kind="seeking", key="seeking",
            body={"state": "waiting", "group": ""}, expect_etag=0,
        )
    _account(tmp_path, "spectator", "home-spectator")
    from tinyassets.shared_state import grant_view

    grant_view(tmp_path, actor="owner", universe_id=universe, viewer="spectator")
    matched = []
    for _ in range(6):
        for player in players:
            try:
                result = claim_group(
                    tmp_path, actor=player, universe_id=universe, name="match",
                )
            except (StateConflict, PermissionError):
                continue
            if result["matched"] and result["group"] not in matched:
                matched.append(result["group"])
    assert len(matched) == 4
    secret = "hole-token-do-not-leak"
    seats = []
    for player in players:
        group = read_record(
            tmp_path, actor=player, universe_id=universe, key=f"{player}/seeking",
        )["body"]["group"]
        created = transition(
            tmp_path, actor=player, universe_id=universe, key="seat", name="sit",
            expect_etag=0,
            patch={
                "hole": f"{secret}-{player}", "stack": 100, "bet": 0,
                "group": group, "act_by": time.time() + 3600,
            },
        )
        seats.append(created)
        with pytest.raises(PermissionError):
            write_record(
                tmp_path, actor=player, universe_id=universe, kind="seat", key="seat",
                body={"hole": "rewritten", "state": "to_act"}, expect_etag=created["etag"],
            )
    own = read_record(
        tmp_path, actor=players[0], universe_id=universe, key=seats[0]["key"],
    )
    assert own["private"] is True
    assert secret in own["body"]["hole"]
    other = read_record(
        tmp_path, actor=players[1], universe_id=universe, key=seats[0]["key"],
    )
    owner_view = read_record(
        tmp_path, actor="owner", universe_id=universe, key=seats[0]["key"],
    )
    crowd = sync(tmp_path, actor="spectator", universe_id=universe, since=0, limit=2000)
    published = str(other["body"]) + str(owner_view["body"]) + str(crowd)
    assert secret not in published
    # A stale etag is a retryable conflict. An action the current state does
    # not allow is a refusal, even at the current etag.
    with pytest.raises(StateConflict):
        transition(
            tmp_path, actor=players[0], universe_id=universe, key=seats[0]["key"],
            name="bet", expect_etag=0, patch={"bet": 5},
        )
    moved = transition(
        tmp_path, actor=players[0], universe_id=universe, key=seats[0]["key"],
        name="bet", expect_etag=seats[0]["etag"], patch={"bet": 5},
    )
    assert moved["state"] == "waiting"
    with pytest.raises(PermissionError):
        transition(
            tmp_path, actor=players[0], universe_id=universe, key=seats[0]["key"],
            name="bet", expect_etag=moved["etag"], patch={"bet": 6},
        )
    late = transition(
        tmp_path, actor=players[2], universe_id=universe, key="late", name="sit",
        expect_etag=0,
        patch={
            "hole": f"{secret}-late", "stack": 50, "bet": 0, "group": "g",
            "act_by": 1,
        },
    )
    folded = expire_due(tmp_path, universe_id=universe, now=10)
    assert any(row["key"] == late["key"] and row["state"] == "folded" for row in folded)
    assert runs_for(tmp_path, players[2]) == []
    revoke_member(tmp_path, actor="owner", universe_id=universe, member="spectator")
    with pytest.raises(PermissionError):
        sync(tmp_path, actor="spectator", universe_id=universe, since=0)
