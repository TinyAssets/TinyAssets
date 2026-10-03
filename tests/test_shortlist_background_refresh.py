"""A picker read never waits on discovery; per-source, off the request path.

Closes docs/concerns/2026-09-16-model-picker-global-discovery-delay.md, whose
two asks were per-source freshness and discovery isolation: one slow or expired
source must not delay selecting a DIFFERENT accepted source. The concern also
warns against claiming it solved "by promoting the button or enabling stale
choices globally", so the tests below pin that a read runs no discovery AND
that an aged-out entry is withheld rather than served.
"""

import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_native_discovery_integration import install_discovery
from tests.test_native_model_authority import native  # noqa: F401
from tinyassets.exceptions import ProviderError
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.served_model_plan import prepare_owned_model_plan
from tinyassets.providers.shortlist_refresh import (
    REFRESH_AGE,
    USABLE_AGE,
    ShortlistCache,
    warm_connected_shortlists,
)

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def catalogue(ids, *, provider="codex"):
    """A REAL NativeDiscoverySnapshot, which is what discovery returns.

    An earlier version of this helper returned a bare NativeCatalogue. The
    cache stores whatever discovery hands it, so a wrong-shaped double would
    have let the cache pass tests while serving objects the plan cannot read.
    """
    from tinyassets.credential_vault import LLMCredentialCustodyReference
    from tinyassets.providers.native_discovery import NativeDiscoverySnapshot

    now = datetime.now(timezone.utc)
    custody = LLMCredentialCustodyReference(
        "ref", "o", "u", provider, 1, "digest", "record-digest")
    return NativeDiscoverySnapshot(
        provider, "o", Path("/b/u"), custody, now, now,
        NativeCatalogue(
            tuple(NativeModel(name, frozenset({"text"})) for name in ids),
            ids[0] if ids else None, now,
        ),
    )


@pytest.fixture
def cache():
    instance = ShortlistCache()
    try:
        yield instance
    finally:
        instance.shutdown(wait=True)


def drain(cache, timeout=5.0):
    """Wait for queued refreshes to finish; the API is deliberately async."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not cache.stats()["inflight"]:
            return True
        time.sleep(0.01)
    return False


def install(monkeypatch, cache, answers):
    """Point the cache's discovery at a recording fake."""
    calls = []

    def discover(*, base_path, owner_user_id, universe_id, provider):
        calls.append(provider)
        answer = answers[provider]
        if isinstance(answer, BaseException):
            raise answer
        return answer() if callable(answer) else answer

    monkeypatch.setattr(
        "tinyassets.providers.native_discovery.discover_native_models_sync", discover,
    )
    return calls


# --------------------------------------------------------------------------
# The read never discovers.
# --------------------------------------------------------------------------


def test_a_cold_read_schedules_instead_of_discovering(monkeypatch, cache):
    """The whole point: the read returns at once and the work happens after."""
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        assert release.wait(5), "refresh never released"
        return catalogue(["m"])

    install(monkeypatch, cache, {"codex": slow})
    began = time.monotonic()
    snapshot, reason = cache.get(base="/b", owner="o", universe_id="u", provider="codex")
    elapsed = time.monotonic() - began

    assert snapshot is None
    assert reason == "catalogue_refresh_pending", "a cold source must say so truthfully"
    assert elapsed < 1.0, f"the read waited {elapsed:.2f}s on discovery"
    assert started.wait(5), "no background refresh was scheduled"
    release.set()
    assert drain(cache)
    # ...and once warm, the same read serves it with no further work.
    again, reason = cache.get(base="/b", owner="o", universe_id="u", provider="codex")
    assert reason == "" and [m.model_id for m in again.catalogue.models] == ["m"]


def test_one_wedged_source_does_not_delay_another(monkeypatch, cache):
    """Discovery isolation, which is the concern's second ask.

    A source whose refresh never returns must cost a different source nothing.
    """
    forever = threading.Event()

    def wedged():
        assert forever.wait(30)
        return catalogue(["never"])

    install(monkeypatch, cache, {"codex": wedged,
                                 "claude-code": catalogue(["fast"], provider="claude-code")})
    cache.get(base="/b", owner="o", universe_id="u", provider="codex")
    cache.refresh_now(base="/b", owner="o", universe_id="u", provider="claude-code")

    began = time.monotonic()
    snapshot, reason = cache.get(base="/b", owner="o", universe_id="u",
                                 provider="claude-code")
    elapsed = time.monotonic() - began
    assert reason == "" and snapshot is not None
    assert [m.model_id for m in snapshot.catalogue.models] == ["fast"]
    assert elapsed < 1.0, "a wedged source delayed an unrelated one"
    forever.set()


def test_each_source_is_keyed_separately(monkeypatch, cache):
    install(monkeypatch, cache, {"codex": catalogue(["a"]),
                                 "claude-code": catalogue(["b"], provider="claude-code")})
    for provider in ("codex", "claude-code"):
        cache.refresh_now(base="/b", owner="o", universe_id="u", provider=provider)
    first, _ = cache.get(base="/b", owner="o", universe_id="u", provider="codex")
    second, _ = cache.get(base="/b", owner="o", universe_id="u", provider="claude-code")
    assert [m.model_id for m in first.catalogue.models] == ["a"]
    assert [m.model_id for m in second.catalogue.models] == ["b"]


@pytest.mark.parametrize("field", ["owner", "universe_id"])
def test_a_warm_entry_never_crosses_an_owner_or_a_universe(monkeypatch, cache, field):
    """A catalogue is scoped to the custody it was read under."""
    install(monkeypatch, cache, {"codex": catalogue(["private"])})
    mine = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**mine)
    assert cache.get(**mine)[0] is not None
    other = {**mine, field: "somebody-else"}
    assert cache.get(**other)[0] is None, "a warm entry leaked across scope"


# --------------------------------------------------------------------------
# Stale is withheld, not served.
# --------------------------------------------------------------------------


def test_an_aged_out_entry_is_withheld_rather_than_offered(monkeypatch, cache):
    """The concern's explicit warning: do not solve this with stale choices.

    Past USABLE_AGE the snapshot would fail `assert_fresh` at execution, so
    offering it is a choice that breaks on use -- worse than a truthful "not
    yet".
    """
    install(monkeypatch, cache, {"codex": catalogue(["m"])})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**where)
    assert cache.get(**where)[0] is not None

    # Age is measured from the SNAPSHOT's own observed_at on the wall clock --
    # the stamp `assert_fresh` uses -- so this moves that clock, not a
    # monotonic one. Codex found the earlier monotonic-at-completion measure
    # could call a snapshot fresh that `assert_fresh` already considered dead.
    later = datetime.now(timezone.utc) + timedelta(seconds=USABLE_AGE + 1)
    monkeypatch.setattr(cache, "_now", lambda: later)
    snapshot, reason = cache.get(**where)
    assert snapshot is None, "an entry past USABLE_AGE was served"
    assert reason, "an aged-out entry must still say something truthful"
    assert USABLE_AGE < 300, "a served entry must stay inside the 5-minute freshness"
    assert REFRESH_AGE < USABLE_AGE, "the warm window must be replaced before it expires"


def test_a_failed_refresh_keeps_a_still_usable_snapshot(monkeypatch, cache):
    """A transient error must not empty a picker that worked a minute ago."""
    answers = {"codex": catalogue(["m"])}
    install(monkeypatch, cache, answers)
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**where)

    answers["codex"] = ProviderError("metadata temporarily unavailable")
    cache.refresh_now(**where)
    snapshot, _reason = cache.get(**where)
    assert snapshot is not None, "a failed refresh discarded a working catalogue"


def test_a_failed_refresh_reports_its_own_reason_when_cold(monkeypatch, cache):
    """"It failed" and "not yet" are different facts; a client sees which."""
    install(monkeypatch, cache, {"codex": ProviderError("nope")})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**where)
    assert cache.get(**where) == (None, "native_catalogue_unavailable")


def test_unsupported_enumeration_keeps_its_own_reason(monkeypatch, cache):
    install(monkeypatch, cache, {"codex": None})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**where)
    assert cache.get(**where) == (None, "native_enumeration_unsupported")


def test_an_unexpected_error_does_not_kill_the_worker(monkeypatch, cache):
    """A background thread that dies would wedge every later refresh."""
    answers = {"codex": RuntimeError("something nobody predicted")}
    install(monkeypatch, cache, answers)
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**where)
    assert cache.get(**where) == (None, "catalogue_refresh_unavailable")

    answers["codex"] = catalogue(["recovered"])
    cache.refresh_now(**where)
    assert cache.get(**where)[0] is not None, "the cache never recovered"


def test_only_one_refresh_per_source_is_in_flight(monkeypatch, cache):
    """Repeated reads must not queue a provider call each."""
    release = threading.Event()
    seen = []

    def slow():
        seen.append(1)
        assert release.wait(5)
        return catalogue(["m"])

    install(monkeypatch, cache, {"codex": slow})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    for _ in range(12):
        cache.get(**where)
    time.sleep(0.2)
    assert len(seen) == 1, f"{len(seen)} concurrent refreshes for one source"
    release.set()
    assert drain(cache)


def test_forget_drops_a_catalogue_read_under_old_custody(monkeypatch, cache):
    install(monkeypatch, cache, {"codex": catalogue(["m"])})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    cache.refresh_now(**where)
    assert cache.get(**where)[0] is not None
    cache.forget(base="/b", owner="o", universe_id="u")
    assert cache.get(**where)[0] is None, "a reconnect kept the previous catalogue"


def test_the_bound_evicts_the_coldest_rather_than_refusing_the_newest(monkeypatch, cache):
    """At the cap, a live request wins over a never-refreshed entry.

    Codex found the bound checked only in `get`, so warming and `refresh_now`
    grew the dict unchecked while a NEW picker source was the one refused --
    backwards, since an unrefreshed entry cannot be served anyway.
    """
    from tinyassets.providers import shortlist_refresh as module

    monkeypatch.setattr(module, "MAX_TRACKED", 3)
    install(monkeypatch, cache, {"codex": catalogue(["m"])})
    where = {"base": "/b", "universe_id": "u", "provider": "codex"}
    # Every insertion path is bounded, not just the read.
    for n in range(6):
        cache.refresh_now(owner=f"warm{n}", **where)
    assert cache.stats()["tracked"] <= 3
    for n in range(6):
        assert cache.schedule(owner=f"sched{n}", **where) is not None
    assert cache.stats()["tracked"] <= 3
    # An IN-FLIGHT entry is never evicted -- its worker still owns the key --
    # so while the cap is full of them a newcomer is honestly refused rather
    # than served something that was taken out from under a running refresh.
    assert drain(cache)
    # Once they settle, a brand-new source is admitted rather than turned away.
    _snapshot, reason = cache.get(owner="newcomer", **where)
    assert reason == "catalogue_refresh_pending", (
        "a new source was refused while evictable entries existed"
    )
    assert cache.stats()["tracked"] <= 3


# --------------------------------------------------------------------------
# The real plan path.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_a_display_read_runs_no_discovery_and_keeps_the_default(native, monkeypatch):  # noqa: F811
    """End to end through the real plan: cold display never calls the executor.

    The provider's own default stays selectable, because it never needed
    enumeration -- so a cold catalogue costs the enumerated rows, not the
    source.
    """
    from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE

    calls = []

    async def discover():
        calls.append(1)
        return catalogue(["enumerated-model"])

    install_discovery(native, monkeypatch, discover)
    SHORTLIST_CACHE.forget(base=native.base, owner="owner-1",
                           universe_id=native.universe.name)
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule",
                        lambda **kwargs: False)  # no background work in-test

    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1",
        agent=native.agent, allow_empty=True,
    )
    assert calls == [], "a display read ran discovery on the request path"
    offered = {m.model_id for m in prepared.catalog.connections[0].models}
    assert "" in offered, "the provider default must survive a cold catalogue"
    assert any(item.reason == "catalogue_refresh_pending"
               for item in prepared.ineligible), "the pending state was not reported"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_a_served_turn_still_discovers_inline(native, monkeypatch):  # noqa: F811
    """Execution is untouched: it is about to launch and must not guess."""
    calls = []

    async def discover():
        calls.append(1)
        return catalogue(["enumerated-model"])

    install_discovery(native, monkeypatch, discover)
    prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
    )
    assert calls, "a served plan skipped the fresh discovery it needs"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_warming_only_touches_sources_this_owner_deposited(native, monkeypatch):  # noqa: F811
    """The warm reads the deposit table; it is never handed a provider list."""
    from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE

    scheduled = []
    monkeypatch.setattr(
        SHORTLIST_CACHE, "schedule",
        lambda **kwargs: scheduled.append(kwargs["provider"]) or True,
    )
    queued = warm_connected_shortlists(
        base_path=native.base, universe_dir=native.universe,
        owner_user_id="owner-1", universe_id=native.universe.name,
    )
    assert queued == len(scheduled)
    assert scheduled == ["codex"], f"warmed something unexpected: {scheduled}"
    # A different owner has deposited nothing in this universe.
    scheduled.clear()
    assert warm_connected_shortlists(
        base_path=native.base, universe_dir=native.universe,
        owner_user_id="someone-else", universe_id=native.universe.name,
    ) == 0
    assert scheduled == []


def test_warming_never_raises_into_its_caller(tmp_path):
    """A deposit must not fail because warming did."""
    assert warm_connected_shortlists(
        base_path=tmp_path / "missing", universe_dir=tmp_path / "missing" / "u",
        owner_user_id="o", universe_id="u",
    ) == 0


def test_shutdown_is_idempotent_and_clears(monkeypatch):
    instance = ShortlistCache()
    install(monkeypatch, instance, {"codex": catalogue(["m"])})
    instance.refresh_now(base="/b", owner="o", universe_id="u", provider="codex")
    assert instance.stats()["warm"] == 1
    instance.shutdown(wait=True)
    instance.shutdown(wait=True)
    assert instance.stats() == {"tracked": 0, "warm": 0, "inflight": 0}


def test_freshness_constants_stay_inside_the_snapshot_window():
    """The cache may never hand out something execution would refuse.

    `NativeDiscoverySnapshot.assert_fresh` allows five minutes from
    `observed_at`; a served entry needs headroom to survive being read and
    then chosen.
    """
    assert 0 < REFRESH_AGE < USABLE_AGE
    assert USABLE_AGE <= timedelta(minutes=5).total_seconds() / 2
    assert replace is not None  # keep the import honest for future edits


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_a_warming_catalogue_leaves_the_default_PICKABLE(native, monkeypatch):  # noqa: F811
    """The claim above, checked where the client actually decides.

    `model_options_document` attaches source-level reasons to each row, and the
    app treats ANY reason on a row as not-pickable
    (`usable()` in app.html requires `!(row.reasons||[]).length`). So a pending
    reason left on the provider-default row would make the one lane that never
    needed enumeration unselectable while a refresh runs -- the exact opposite
    of this change's purpose, and invisible to a test that only checks the
    model list.
    """
    from tinyassets.providers.model_options import model_options_document
    from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE

    async def discover():
        return catalogue(["enumerated-model"])

    install_discovery(native, monkeypatch, discover)
    SHORTLIST_CACHE.forget(base=native.base, owner="owner-1",
                           universe_id=native.universe.name)
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule", lambda **kwargs: False)

    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1",
        agent=native.agent, allow_empty=True,
    )
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    default = next(row for row in document["options"]
                   if row["reference"]["model_id"] == "")

    assert default["in_candidate_catalog"] is True
    assert default["reasons"] == [], (
        "a warming catalogue made the provider default unpickable: "
        f"{default['reasons']}"
    )
    # ...while the source itself still reports the pending state honestly.
    assert any(reason["reason"] == "catalogue_refresh_pending"
               for entry in document["source_failures"]
               for reason in entry["reasons"])


def test_a_refresh_started_before_a_reconnect_cannot_resurrect_its_result(monkeypatch, cache):
    """Codex finding 2, which `forget` alone did not close.

    `_run` used to `setdefault` the key and write unconditionally. So a worker
    that began BEFORE a reconnect could finish after it and (a) resurrect a
    catalogue read under the previous credential, and (b) clear the
    replacement entry's in-flight flag so its own refresh never landed.
    Custody checks stop that stale reference reaching execution, but the
    picker would drop the source -- and its default -- instead.
    """
    release, returned = threading.Event(), threading.Event()

    def slow():
        assert release.wait(5), "stale worker never released"
        try:
            return catalogue(["from-the-old-credential"])
        finally:
            returned.set()

    install(monkeypatch, cache, {"codex": slow})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}

    assert cache.schedule(**where)
    # The reconnect lands while that refresh is still running.
    cache.forget(base="/b", owner="o", universe_id="u")
    release.set()
    # NOT `drain`: it counts in-flight entries, and `forget` removed this
    # one -- so draining would return instantly and the assertion below would
    # pass merely because the worker had not finished yet. Wait for the
    # worker itself, then give its write a moment to land if it is going to.
    assert returned.wait(5), "stale worker never finished"
    time.sleep(0.2)

    snapshot, reason = cache.get(**where)
    assert snapshot is None, "a forgotten key was resurrected by its old worker"
    assert reason == "catalogue_refresh_pending"


def test_a_stale_worker_does_not_clear_a_replacements_claim(monkeypatch, cache):
    """The second half of the same finding: the in-flight flag is per generation."""
    release = threading.Event()
    finished = []

    def slow():
        assert release.wait(5)
        finished.append(1)
        return catalogue(["stale"])

    install(monkeypatch, cache, {"codex": slow})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    assert cache.schedule(**where)
    cache.forget(base="/b", owner="o", universe_id="u")
    # A NEW claim for the same source, belonging to the new generation.
    assert cache.schedule(**where), "the replacement could not claim the source"
    release.set()
    deadline = time.time() + 5
    while len(finished) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(finished) == 2, "both the stale and the replacement run must finish"
    assert drain(cache)
    # The replacement's own refresh ran and landed; the stale one did not.
    snapshot, _reason = cache.get(**where)
    assert snapshot is not None
    assert [m.model_id for m in snapshot.catalogue.models] == ["stale"]


def test_shutdown_also_retires_generations(monkeypatch):
    """A worker surviving shutdown must not write into a cleared cache."""
    instance = ShortlistCache()
    release = threading.Event()

    def slow():
        assert release.wait(5)
        return catalogue(["after-shutdown"])

    install(monkeypatch, instance, {"codex": slow})
    where = {"base": "/b", "owner": "o", "universe_id": "u", "provider": "codex"}
    assert instance.schedule(**where)
    instance.shutdown(wait=False)
    release.set()
    time.sleep(0.3)
    assert instance.stats()["warm"] == 0, "a worker wrote into a shut-down cache"
    instance.shutdown(wait=True)
