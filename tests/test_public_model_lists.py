"""Sharing is a reviewed file; a typed id is personal forever.

The founder's chosen design (2026-09-26), after three more elaborate ones were built
and refuted:

* a charset could not tell a public model name from a private account-bearing
  selector -- it admitted Bedrock ARNs and rejected real selectors like `sonnet[1m]`;
* a distinct-owner threshold did not imply "public" either, because two colleagues
  share one organisation's private deployment id;
* attestation plus peer confirmation worked, and was over-built for the problem.

So: typed ids never leave their owner, and sharing happens where a human already
reviews things -- `models/<source-kind>.json`, with agent-opened PRs and a CI check.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tinyassets.providers.public_model_lists import (
    PublicModelListError,
    newest_listed,
    read_list,
)
from tinyassets.storage import db_path
from tinyassets.storage.learned_models import (
    COLUMNS,
    OwnModelHistory,
    record_verified_model,
)

#: An account-bearing selector: the shape that leaked under the first design.
ARN = ("arn:aws:bedrock:us-east-1:123456789012:inference-profile/"
       "us.anthropic.claude-sonnet-4-5-20250929-v1:0")


def _write(tmp_path, models, source_kind="subscription", raw=None):
    directory = tmp_path / "models"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source_kind}.json"
    path.write_text(
        raw if raw is not None
        else json.dumps({"source_kind": source_kind, "models": {"codex": models}}),
        encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# The shipped list: the founder must be able to select Fable.
# ---------------------------------------------------------------------------


def test_the_shipped_subscription_list_offers_the_newest_of_each_class():
    """The founder's actual complaint: "i cant seem to select fable as a user"."""
    listed = read_list("subscription")
    assert "claude-fable-5-1" in listed, (
        "the id the founder could not select must be on the shipped list")
    offered = newest_listed("subscription")
    assert "claude-fable-5-1" in offered, (
        "and it must survive the newest-per-class reduction, or it is still invisible")


def test_public_ids_are_attached_only_to_the_source_that_lists_them():
    from tinyassets.providers.served_model_plan import _listed_candidates

    claude = set(newest_listed("subscription", source_key="claude-code"))
    codex = set(newest_listed("subscription", source_key="codex"))
    assert claude == {
        "claude-fable-5-1", "claude-haiku-4-5-20251001",
        "claude-opus-5-5", "claude-sonnet-5",
    }
    assert codex == {"gpt-5.6-sol", "gpt-6-astra"}
    assert not claude & codex
    assert read_list("subscription", source_key="unlisted-provider") == ()
    assert {row.model_id for row in _listed_candidates(
        "subscription", source_key="claude-code", already=())} == claude
    assert {row.model_id for row in _listed_candidates(
        "subscription", source_key="codex", already=())} == codex




def test_the_shipped_list_passes_its_own_ci_check():
    from scripts.check_model_lists import KNOWN_SOURCE_KINDS, findings
    from tinyassets.providers.public_model_lists import lists_directory

    assert findings(lists_directory()) == []
    # Every shipped file names a source kind a connection can actually report, or it
    # would be silently ignored at runtime.
    for path in lists_directory().glob("*.json"):
        assert path.stem in KNOWN_SOURCE_KINDS


# ---------------------------------------------------------------------------
# The loader.
# ---------------------------------------------------------------------------


def test_only_the_newest_of_each_class_is_offered(tmp_path):
    directory = _write(tmp_path, sorted(["some-model-4-6", "some-model-4-7", "other-2-1"]))
    assert sorted(newest_listed("subscription", directory=directory)) == [
        "other-2-1", "some-model-4-7"]
    # The file keeps its history; only the OFFER is reduced.
    assert "some-model-4-6" in read_list("subscription", directory=directory)


def test_a_source_kind_with_no_file_is_simply_unlisted(tmp_path):
    assert read_list("local", directory=tmp_path / "models") == ()
    assert newest_listed("local", directory=tmp_path / "models") == ()


@pytest.mark.parametrize(
    ("models", "raw", "why"),
    [
        (None, "{not json", "invalid JSON"),
        (None, '{"source_kind": "subscription"}', "missing models"),
        (None, '{"source_kind": "subscription", "models": [], "extra": 1}', "extra key"),
        (None, '{"source_kind": "other", "models": []}', "wrong source kind"),
        (None, '{"source_kind": "subscription", "models": "a"}', "models not a list"),
        (["a-1", "a-1"], None, "duplicate"),
        (["b-2", "a-1"], None, "unsorted"),
        (["has space"], None, "not an identifier"),
        ([""], None, "empty id"),
        (["x" * 201], None, "unbounded id"),
        ([5], None, "not a string"),
    ],
)
def test_a_malformed_list_raises_rather_than_reading_as_empty(tmp_path, models, raw, why):
    """A corrupted list must not silently shrink every user's picker.

    Reading as empty would be the worst outcome: nobody sees a signal, and everyone
    quietly loses their shared models.
    """
    directory = _write(tmp_path, models, raw=raw)
    with pytest.raises(PublicModelListError):
        read_list("subscription", directory=directory)


@pytest.mark.parametrize("bad_kind", ["../escape", "Subscription", "", "a" * 40, "with space"])
def test_a_source_kind_cannot_wander_outside_the_directory(tmp_path, bad_kind):
    """The source kind names a file, so it is constrained like a filename."""
    with pytest.raises(PublicModelListError):
        read_list(bad_kind, directory=tmp_path / "models")


def test_real_selectors_the_old_charset_rejected_are_listable(tmp_path):
    """`sonnet[1m]` was refused when a charset was doing privacy work. It is data now."""
    directory = _write(tmp_path, sorted(["opus[1m]", "sonnet[1m]", "vendor/model-3-1"]))
    assert len(read_list("subscription", directory=directory)) == 3


# ---------------------------------------------------------------------------
# The CI check, which is the mechanical half of moderation.
# ---------------------------------------------------------------------------


def test_the_check_names_the_file_and_the_reason(tmp_path):
    from scripts.check_model_lists import findings

    directory = _write(tmp_path, ["b-2", "a-1"])
    problems = findings(directory)
    assert len(problems) == 1
    assert "subscription.json" in problems[0] and "sorted" in problems[0]


def test_the_check_refuses_an_unknown_source_kind(tmp_path):
    from scripts.check_model_lists import findings

    directory = _write(tmp_path, ["a-1"], source_kind="madeup")
    problems = findings(directory)
    assert len(problems) == 1 and "unknown source kind" in problems[0]


def test_the_check_refuses_an_unknown_subscription_source(tmp_path):
    from scripts.check_model_lists import findings

    directory = tmp_path / "models"
    directory.mkdir()
    (directory / "subscription.json").write_text(json.dumps({
        "source_kind": "subscription", "models": {"wrong-package": ["a-1"]},
    }), encoding="utf-8")
    assert "unknown subscription source" in findings(directory)[0]


def test_the_check_refuses_an_empty_list(tmp_path):
    """Almost certainly a mistake in a PR whose whole purpose was to add an id."""
    from scripts.check_model_lists import findings

    assert "must list models" in findings(_write(tmp_path, []))[0]


# ---------------------------------------------------------------------------
# A typed id is personal forever. This is the privacy property, restated simply.
# ---------------------------------------------------------------------------


def test_a_typed_id_never_reaches_any_shared_place(tmp_path):
    """The whole privacy argument now: nothing here is ever published.

    An account-bearing ARN stays with its owner because there is no mechanism that
    could move it -- not a threshold, not an attestation. Sharing requires a human
    merging a PR.
    """
    history = OwnModelHistory(tmp_path)
    assert history.record(source_kind="subscription", provider_ref="codex", model_id=ARN,
                          owner_user_id="alice") is True
    assert [row.model_id for row in history.ids_for("subscription", "codex", "alice")] == [ARN]
    # Nobody else's view contains it...
    assert history.ids_for("subscription", "codex", "bob") == []
    # ...and it is on no shared list, which is the only route to another user.
    assert ARN not in read_list("subscription")
    # The only table this creates is the per-owner one.
    with sqlite3.connect(db_path(tmp_path)) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "learned_models" not in tables, "the shared table is retired"
    assert "learned_model_pending" not in tables
    assert "learned_model_confirmations" not in tables


def test_one_owners_several_universes_are_one_row(tmp_path):
    history = OwnModelHistory(tmp_path)
    assert history.record(source_kind="subscription", provider_ref="codex", model_id="a-1",
                          owner_user_id="alice") is True
    assert history.record(source_kind="subscription", provider_ref="codex", model_id="a-1",
                          owner_user_id="alice") is False
    assert len(history.ids_for("subscription", "codex", "alice")) == 1


def test_verified_history_is_scoped_to_the_source(tmp_path):
    history = OwnModelHistory(tmp_path)
    assert history.record(source_kind="subscription", provider_ref="codex",
                          model_id="gpt-private", owner_user_id="alice")
    assert history.ids_for("subscription", "claude-code", "alice") == []
    assert history.record(source_kind="subscription", provider_ref="claude-code",
                          model_id="gpt-private", owner_user_id="alice")
    assert [row.model_id for row in history.ids_for(
        "subscription", "codex", "alice")] == ["gpt-private"]


def test_legacy_history_is_preserved_and_only_unambiguous_rows_are_attributed(tmp_path):
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("CREATE TABLE learned_model_evidence (source_kind TEXT NOT NULL, "
                     "model_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, "
                     "first_verified_at TEXT NOT NULL, "
                     "PRIMARY KEY(source_kind, model_id, owner_user_id))")
        conn.executemany("INSERT INTO learned_model_evidence VALUES (?, ?, ?, ?)", [
            ("agent_claude_code", "agent-private", "alice", "2026-01-01T00:00:00Z"),
            ("subscription", "ambiguous", "alice", "2026-01-02T00:00:00Z"),
            ("subscription", "single-source", "bob", "2026-01-03T00:00:00Z"),
        ])
        conn.execute("CREATE TABLE llm_credential_deposit_owners ("
                     "universe_id TEXT NOT NULL, service TEXT NOT NULL, "
                     "owner_user_id TEXT NOT NULL, PRIMARY KEY(universe_id, service))")
        conn.executemany("INSERT INTO llm_credential_deposit_owners VALUES (?, ?, ?)", [
            ("alice-codex", "codex", "alice"),
            ("alice-claude", "claude", "alice"),
            ("bob-codex", "codex", "bob"),
        ])
    history = OwnModelHistory(tmp_path)
    assert [row.model_id for row in history.ids_for(
        "agent_claude_code", "claude-code", "alice")] == ["agent-private"]
    assert history.ids_for("subscription", "claude-code", "alice") == []
    assert history.ids_for("subscription", "codex", "alice") == []
    assert [row.model_id for row in history.ids_for(
        "subscription", "codex", "bob")] == ["single-source"]
    history.record(source_kind="subscription", provider_ref="codex",
                   model_id="new-model", owner_user_id="alice")
    assert [row.model_id for row in history.ids_for(
        "subscription", "codex", "alice")] == ["new-model"]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM learned_model_evidence").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM learned_model_source_evidence").fetchone()[0] == 3
        assert conn.execute("SELECT provider_ref FROM learned_model_source_evidence "
                            "WHERE model_id = 'agent-private'").fetchone()[0] == "claude-code"
        assert conn.execute("SELECT provider_ref FROM learned_model_source_evidence "
                            "WHERE model_id = 'single-source'").fetchone()[0] == "codex"
        assert conn.execute("SELECT 1 FROM learned_model_source_evidence "
                            "WHERE model_id = 'ambiguous'").fetchone() is None
        # An old process can still use its four-value INSERT after the new schema exists.
        conn.execute("INSERT INTO learned_model_evidence VALUES (?, ?, ?, ?)",
                     ("agent_codex", "rolling-turn", "alice", "2026-01-04T00:00:00Z"))
    assert [row.model_id for row in history.ids_for(
        "agent_codex", "codex", "alice")] == ["rolling-turn"]
    assert [row.model_id for row in history.ids_for(
        "agent_codex", "codex", "alice")] == ["rolling-turn"]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM learned_model_evidence").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM learned_model_source_evidence").fetchone()[0] == 4


def test_the_per_owner_table_is_reachable_by_the_deletion_sweep(tmp_path):
    """It IS this owner's data, so deletion must find it -- by column name."""
    from tinyassets.account_deletion import PRESERVED_TABLES, PRINCIPAL_KEYS
    from tinyassets.scoped_reset import MAIN_DB_TABLE_CLASSIFICATIONS

    OwnModelHistory(tmp_path).record(source_kind="subscription", provider_ref="codex",
                                     model_id="a-1",
                                     owner_user_id="alice")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(learned_model_source_evidence)")}
    assert columns == set(COLUMNS)
    assert columns & set(PRINCIPAL_KEYS), "rows would be orphaned on account deletion"
    assert MAIN_DB_TABLE_CLASSIFICATIONS.get("learned_model_evidence") == "preserve"
    assert MAIN_DB_TABLE_CLASSIFICATIONS.get("learned_model_source_evidence") == "preserve"
    assert "learned_model_evidence" not in PRESERVED_TABLES, (
        "the owner's own data must not be preserved through an account deletion")
    assert "learned_model_source_evidence" not in PRESERVED_TABLES
    # The retired shared table is gone from both sweeps with its code.
    assert "learned_models" not in MAIN_DB_TABLE_CLASSIFICATIONS
    assert "learned_models" not in PRESERVED_TABLES


def test_a_failed_record_never_raises_at_the_call_site(tmp_path, monkeypatch):
    import tinyassets.storage.learned_models as module

    def explode(self, **_kwargs):
        raise sqlite3.DatabaseError("disk went away")

    monkeypatch.setattr(module.OwnModelHistory, "record", explode)
    assert record_verified_model(tmp_path, source_kind="subscription", provider_ref="codex",
                                 model_id="a-1", owner_user_id="alice") is False


# ---------------------------------------------------------------------------
# The lists have to REACH production. Codex on #4028: they did not.
# ---------------------------------------------------------------------------


def test_the_lists_are_packaged_into_the_runtime_artifacts():
    """A list nobody ships is a list nobody reads.

    `lists_directory()` resolves `models/` relative to the package, so the data has to
    be copied alongside it in every artifact. The Dockerfile copied the package and not
    the lists, and the MCPB staging omitted them too, so the whole feature would have
    shipped doing nothing — silently, because an absent directory reads as "unlisted".
    """
    from tinyassets.providers.public_model_lists import lists_directory

    root = lists_directory().parent

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY models/ /app/models/" in dockerfile, (
        "the image must carry the lists beside the package it resolves them from")
    # And the resolved location inside the image must actually be /app/models: the
    # package lands at /app/tinyassets, so parents[2] is /app.
    assert "COPY --from=builder /build/tinyassets /app/tinyassets" in dockerfile

    import subprocess
    import sys
    subprocess.run(
        [sys.executable, str(root / "packaging/mcpb/build_bundle.py"), "--skip-probe"],
        check=True,
    )
    bundled_list = root / "packaging/dist/tinyassets-universe-server-src/models/subscription.json"
    assert bundled_list.is_file(), (
        "the MCPB runtime resolves models/ from its own root; stage it there"
    )



def test_a_list_only_change_triggers_a_deploy():
    """Otherwise a merged model addition sits in the repo and reaches nobody.

    The build workflow is path-filtered, so data the runtime reads has to be in the
    filter or a list-only PR builds no image and deploys nothing.
    """
    from tinyassets.providers.public_model_lists import lists_directory

    workflow = (lists_directory().parent / ".github" / "workflows"
                / "build-image.yml").read_text(encoding="utf-8")
    assert "'models/**'" in workflow, (
        "a list-only PR is a real behaviour change for every user on that source kind")


def test_a_content_change_at_the_same_mtime_is_not_served_from_cache(tmp_path, monkeypatch):
    """Codex on #4028: replacing contents while preserving mtime returned the old list.

    The key carries the size as well as the nanosecond mtime, so a stale answer needs a
    byte-identical length at an identical timestamp.
    """
    import os

    import tinyassets.providers.public_model_lists as lists

    directory = _write(tmp_path, ["old-1"])
    path = directory / "subscription.json"
    monkeypatch.setattr(lists, "lists_directory", lambda: directory)
    lists._cached.cache_clear()
    assert lists.newest_listed_cached("subscription", source_key="codex") == ("old-1",)

    stat = path.stat()
    # Two DISTINCT classes, so this test is about the cache and not about the
    # newest-per-class reduction (which would keep only one of `new-2`/`new-3`).
    _write(tmp_path, sorted(["alpha-2", "beta-3"]))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))   # same mtime, new contents
    assert sorted(lists.newest_listed_cached("subscription", source_key="codex")) == [
        "alpha-2", "beta-3"]


def test_an_unreadable_list_file_is_not_silently_unlisted(tmp_path, monkeypatch):
    """"Exists but cannot be read" must not masquerade as "no models"."""
    import tinyassets.providers.public_model_lists as lists

    directory = _write(tmp_path, ["a-1"])
    monkeypatch.setattr(lists, "lists_directory", lambda: directory)
    lists._cached.cache_clear()

    def denied(self):
        raise PermissionError("no")

    monkeypatch.setattr("pathlib.Path.stat", denied)
    with pytest.raises(PublicModelListError, match="cannot be read"):
        lists.newest_listed_cached("subscription", source_key="codex")


def test_listed_rows_survive_the_compact_projection_for_the_served_agent():
    """The cross-lane seam: my rows through the bounded-results lane's reader.

    #4037 narrows `model_options` for the served agent. It carries
    `availability_basis` as data and does not bucket on it, which is what lets this
    change add bases without touching that lane — but neither lane had a test for the
    two together, and the whole point of the listed rows is that an agent can SEE an
    id it must ask the owner to grant.
    """
    from tinyassets.engine_read_views import compact_model_options

    def row(model_id, basis, selectable):
        return {
            "reference": {"provider_ref": "codex", "model_id": model_id},
            "source_kind": "subscription", "availability_basis": basis,
            "freshness": "fresh", "in_candidate_catalog": selectable,
            "reasons": [] if selectable else [
                {"reason": "model_access_optin_required", "component": ""}],
            "labels": [], "order_index": 0 if selectable else None,
            "pricing": {"freshness": "fresh", "unmetered": True, "charges": [],
                        "unknown_components": []},
            "provider_default": model_id == "", "tools": True, "context_tokens": 1,
            "input_modalities": ["text"], "output_modalities": ["text"],
            "scores": None, "basis": None,
        }

    document = {
        "kind": "advisory_model_options", "generation": 1, "policy_source": "saved",
        "mode": "automatic", "order": [], "source_failures": [], "unavailable": [],
        "preferences": {"generation": 1, "policy": None, "updated_at": ""},
        "universe_id": "u-1", "choice_authority": "accepted_manifest", "sources": [],
        "binding_state": "serving",
        "options": [
            row("", "executor_default", True),
            row("claude-fable-5-1", "publicly_listed", False),
            row("my-private-arn-1", "owner_verified_here", False),
        ],
    }
    compact = compact_model_options(document)
    top = {entry["model_id"]: entry for entry in compact["sources"][0]["top"]}

    # All three reach the agent, and the counts distinguish offered from selectable.
    assert set(top) == {"", "claude-fable-5-1", "my-private-arn-1"}
    assert compact["sources"][0]["models"] == 3
    assert compact["sources"][0]["selectable"] == 1

    # A listed id is visible, NOT selectable, and says why -- which is what makes the
    # agent able to ask the owner for the grant instead of silently failing.
    listed = top["claude-fable-5-1"]
    assert listed["availability_basis"] == "publicly_listed"
    assert listed["selectable"] is False
    assert listed["reasons"] == ["model_access_optin_required"]

    # The owner's own id is carried with its own basis, not conflated with the list.
    assert top["my-private-arn-1"]["availability_basis"] == "owner_verified_here"
