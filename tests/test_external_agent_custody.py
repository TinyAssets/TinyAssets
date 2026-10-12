"""A CLI refresh may carry its pin; a newer owner deposit always wins."""
import asyncio
import base64
import json
import sqlite3

from tinyassets.credential_vault import (
    adopt_llm_subscription_custody,
    llm_subscription_credential_record,
    load_credential_vault,
    write_credential_vault,
)
from tinyassets.external_agents.home import Custody, compare_and_swap, exclusive_refresh
from tinyassets.external_agents.registry import resolve
from tinyassets.storage import db_path

OWNER = "owner-a"
CENTER = "u-owner-a"


def _bytes(token):
    return json.dumps({"auth_mode": "subscription", "tokens": {
        "access_token": token, "refresh_token": "refresh-" + token,
        "id_token": "identity-a",
    }}).encode()


def _deposit(center, token):
    write_credential_vault(
        center, [llm_subscription_credential_record(
            service="codex", auth_json_b64=base64.b64encode(_bytes(token)).decode(),
        )], owner_user_id=OWNER, universe_id=CENTER,
    )


def _stored(center):
    record = next(row for row in load_credential_vault(center)
                  if row.get("credential_type") == "llm_subscription")
    return json.loads(base64.b64decode(record["auth_json_b64"]))["tokens"]["access_token"]


def _custody(center):
    with sqlite3.connect(db_path(center.parent), isolation_level=None) as conn:
        conn.execute("BEGIN")
        ref = adopt_llm_subscription_custody(
            conn, universe_dir=center, owner_user_id=OWNER,
            universe_id=CENTER, service="codex",
        )
        conn.commit()
    return Custody(center, OWNER, ref)


def test_cli_refresh_carries_custody_pin(tmp_path):
    center = tmp_path / CENTER
    center.mkdir()
    _deposit(center, "old")
    custody = _custody(center)
    assert custody.current()
    auth = resolve("codex").row["auth"]
    assert compare_and_swap(custody, auth, {"auth.json": _bytes("refreshed")})
    assert _stored(center) == "refreshed"
    carried = custody.reload()
    assert carried.reference_digest == custody.reference.reference_digest
    assert carried._record_digest != custody.reference._record_digest


def test_new_owner_deposit_discards_stale_cli_writeback(tmp_path):
    center = tmp_path / CENTER
    center.mkdir()
    _deposit(center, "old")
    custody = _custody(center)
    _deposit(center, "new-owner-deposit")
    auth = resolve("codex").row["auth"]
    assert not compare_and_swap(custody, auth, {"auth.json": _bytes("stale-cli")})
    assert _stored(center) == "new-owner-deposit"


def test_parallel_jobs_cannot_spend_the_same_refresh_token(tmp_path):
    center = tmp_path / CENTER
    center.mkdir()
    _deposit(center, "old")
    custody = _custody(center)
    auth = resolve("codex").row["auth"]

    async def race():
        entered = asyncio.Event()
        release = asyncio.Event()
        second_entered = asyncio.Event()

        async def first():
            async with exclusive_refresh(custody):
                entered.set()
                await release.wait()
                assert compare_and_swap(custody, auth, {"auth.json": _bytes("rotated")})

        async def second():
            await entered.wait()
            async with exclusive_refresh(custody):
                second_entered.set()
                assert not custody.current()  # stale pin never enters another CLI

        one = asyncio.create_task(first())
        two = asyncio.create_task(second())
        await entered.wait()
        await asyncio.sleep(0.1)
        assert not second_entered.is_set()
        release.set()
        await asyncio.gather(one, two)

    asyncio.run(race())
    assert _stored(center) == "rotated"


def test_failed_cli_exit_still_returns_its_rotated_token(tmp_path):
    from tinyassets.role_external_agent import CellLauncher

    class Process:
        returncode = 1

        def __init__(self):
            self.stdin = self
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            trailer = {"nonce": "n", "exit": 1, "external_agent_writeback": {
                "auth.json": base64.b64encode(_bytes("rotated")).decode()}}
            self.stdout.feed_data((json.dumps(trailer) + "\n").encode())
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        def close(self):
            pass

        async def wait(self):
            return 1

        def close_stdio(self):
            pass

    launcher = CellLauncher(universe_dir=tmp_path, owner_user_id=OWNER)

    async def close():
        proc = Process()
        launcher._nonces[id(proc)] = "n"
        return await launcher.close(proc)

    returned = asyncio.run(close())
    assert returned.exit_code == 1
    assert returned["auth.json"] == _bytes("rotated")
