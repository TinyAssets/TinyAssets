"""The cutover's read-only inventory and exact layout registry (design E1/E6)."""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import command_center_inventory as inv
from tinyassets.command_center_layout import (
    PLATFORM,
    PLATFORM_DB_NAMES,
    PLATFORM_LOCK_NAMES,
    PLATFORM_NAMES,
    USER,
    USER_NAMES,
    classify,
    sqlite_family,
)

HOME = "u-01kxm1vszd8hwp7em418asq8h9"
OTHER = "u-01ky3zh1arr8qth8jee7zx63pq"


@pytest.mark.parametrize("name,expected", [
    ("soul.md", USER), ("soul_versions", USER), ("config.yaml", USER),
    ("AGENTS.md", USER), ("skills", USER), ("notes.json", USER),
    ("workspace", USER), ("workspaces", USER), (".agent-workspace", USER),
    ("soul.edit.md", PLATFORM), ("dispatcher_config.yaml", PLATFORM),
    (".soul.lock", PLATFORM), (".provider-assignment-admission.lock", PLATFORM),
    # nobody writes a bare .lock as a home entry -- its creator puts it inside a
    # credential snapshot directory, so at the top level it is unknown
    (".lock", None),
    (".credentials", PLATFORM), (".credentials.json", PLATFORM),
    ("lancedb", PLATFORM), ("ledger.json", PLATFORM),
    (".conversation_memory.db", PLATFORM), (".conversation_memory.db-wal", PLATFORM),
    (".conversation_memory.db.bak-premigrate-1787982564", PLATFORM),
    (".conversation_memory.db.bak-premigrate-1787982564-wal", PLATFORM),
    (".worker_supervisor.json", PLATFORM),
    (".worker_supervisor.worker_assigned_abc.json", PLATFORM),
    (".worker_supervisor..json", None), ("worker_supervisor.abc.json", None),
    (".worker_supervisor.abc.jsonl", None),
    ("agent-project.db", None), ("agent-project.db-wal", None),
    ("agent-project.db.bak-x", None), ("agent-project.lock", None),
    ("new-policy.md", None), ("something-nobody-classified.bin", None),
    (".universe_id", None), (".command_center_id", None),
])
def test_the_layout_table_puts_trust_in_platform_and_content_with_the_agent(name, expected):
    assert classify(name) == expected


def test_registry_names_have_source_provenance():
    from tinyassets.storage_accounting import UNIVERSE_ENTRIES

    repo = Path(__file__).resolve().parents[1]
    literals = set()
    for directory in ("tinyassets", "fantasy_daemon", "scripts"):
        for path in (repo / directory).rglob("*.py"):
            # The table and its consumer cannot testify to their own provenance.
            if path.name in ("command_center_layout.py", "command_center_inventory.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            literals.update(n.value for n in ast.walk(tree)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str))
    names = USER_NAMES | PLATFORM_NAMES | PLATFORM_DB_NAMES | PLATFORM_LOCK_NAMES
    assert not names - literals
    # Every name storage_accounting says lives INSIDE a home must be classified --
    # that registry is where a new home entry gets added, so this is the gate that
    # notices one the layout table has not been taught yet.
    # UNIVERSE_ENTRIES means "lives inside a universe directory" at ANY depth,
    # while `classify` answers only for a home's own entries. These are the
    # names whose every creator writes them into a SUBdirectory, so the home
    # table has nothing to say about them -- each cited, because the exemption
    # needs the same discipline as the table it bypasses.
    nested_in_a_subdirectory = {
        ".manifest.json": "canon/ (fantasy_daemon/api.py, ingestion/core.py) -- and"
                          " canon/ is a verbatim-exempt subtree, so it is never scanned",
        ".lock": "a credential snapshot directory, beside auth.json"
                 " (credential_vault.py)",
    }
    unknown = {name for name in UNIVERSE_ENTRIES
               if classify(name) is None and name not in nested_in_a_subdirectory}
    assert not unknown, (
        "UNIVERSE_ENTRIES names the layout table does not know -- classify each in "
        "command_center_layout.py, or, if its creators only ever write it into a "
        f"subdirectory of a home, record it here with where it lives: {unknown}"
    )
    assert not PLATFORM_DB_NAMES & PLATFORM_NAMES
    assert sqlite_family("x") == ("x", "x-wal", "x-shm", "x-journal")


def test_names_real_creators_write_into_a_home_are_classified(tmp_path):
    """Provenance by behaviour, not by grepping for a literal.

    A string literal proves a name is mentioned somewhere; it does not prove the
    table knows what a creator actually puts in a home. `.provider-authority`
    passed the literal check and was still unclassified.
    """
    from tinyassets import provider_authority, soul_edit

    home = tmp_path / HOME
    home.mkdir()
    provider_authority.write_record(home, {})
    with soul_edit._soul_lock(home):
        pass
    created = sorted(p.name for p in home.iterdir())
    assert created, "the creators wrote nothing -- this test would prove nothing"
    unknown = [name for name in created if classify(name) is None]
    assert not unknown, f"creators wrote home entries the table does not know: {unknown}"
    assert classify(".provider-authority") == PLATFORM


@pytest.mark.parametrize("name", ["AGENTS.md", "identity.md", "MEMORY.md", "settings.yaml"])
def test_root_harness_content_does_not_block_cutover_inventory(tmp_path, name):
    from tinyassets.command_center_packages import HARNESS_ROOT_FILES, destination

    assert name in HARNESS_ROOT_FILES
    assert destination(name, "helper") == f"agents/helper/{name}"
    home = tmp_path / HOME
    home.mkdir()
    content = "model: owner-choice\n" if name == "settings.yaml" else "- Remember this\n"
    (home / name).write_text(content, encoding="utf-8")
    before = _digest(tmp_path)

    report = inv.inventory(tmp_path)

    assert report["homes"][HOME]["unclassified"] == []
    assert report["homes"][HOME]["user"] == [name]
    assert report["homes"][HOME]["platform"] == []
    assert report["totals"]["unclassified_entries"] == 0
    assert report["complete"]
    assert inv.main([str(tmp_path)]) == 0
    assert _digest(tmp_path) == before


def _fixture(root: Path) -> None:
    home = root / HOME
    home.mkdir(parents=True)
    (home / "soul.md").write_text("# Soul\n", encoding="utf-8")
    (home / "soul.edit.md").write_text("policy\n", encoding="utf-8")
    (home / "config.yaml").write_text(
        "allowed_providers: [codex]\nengine_assignment_state: ready\nstyle: terse\n",
        encoding="utf-8",
    )
    (home / "mystery.bin").write_bytes(b"\x00")
    (home / "status.json").write_text(json.dumps({"universe_id": HOME, "peer": OTHER}),
                                     encoding="utf-8")
    db = sqlite3.connect(root / ".tinyassets.db")
    db.executescript(f"""
        CREATE TABLE universes (universe_id TEXT PRIMARY KEY, display_name TEXT);
        CREATE TABLE runs (run_id TEXT, actor TEXT, payload BLOB,
            storage_class TEXT CHECK (storage_class IN ('scratch','universe')));
        CREATE INDEX runs_by_universe_actor ON runs(actor) WHERE actor LIKE 'universe:%';
        INSERT INTO universes VALUES ('{HOME}', 'Ada');
        INSERT INTO runs VALUES ('r1', 'universe:{HOME}',
            x'7b22757365223a22{HOME.encode().hex()}227d', 'scratch');
        INSERT INTO runs VALUES ('r2', 'someone', NULL, 'scratch');
    """)
    db.commit()
    db.close()


def _digest(root: Path) -> tuple[str, dict]:
    h = hashlib.sha256()
    metadata = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        st = os.lstat(path)
        metadata[rel] = (st.st_size, st.st_mtime_ns)
        h.update(rel.encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest(), metadata


def test_the_inventory_finds_what_the_cutover_must_change_and_writes_nothing(tmp_path):
    _fixture(tmp_path)
    before = _digest(tmp_path)
    report = inv.inventory(tmp_path)
    assert _digest(tmp_path) == before
    home = report["homes"][HOME]
    assert "soul.md" in home["user"] and "config.yaml" in home["user"]
    assert "soul.edit.md" in home["platform"] and "status.json" in home["platform"]
    assert home["unclassified"] == ["mystery.bin"]
    assert home["config_authority"] == ["engine_assignment_state", "allowed_providers"]
    db = report["sqlite"][".tinyassets.db"]
    assert db["tables"] == ["universes"]
    assert {"table": "universes", "column": "universe_id", "generated": False} in db["columns"]
    kinds = {(o["type"], o["name"]) for o in db["schema_sql"]}
    assert ("index", "runs_by_universe_actor") in kinds and ("check", "runs") in kinds
    cells = {(v["table"], v["column"]): v for v in db["values"]}
    assert cells[("runs", "actor")]["actor_prefix"] == 1
    assert cells[("runs", "payload")]["u_ids"] == 1
    assert cells[("universes", "universe_id")]["u_ids"] == 1
    assert report["json"][f"{HOME}/status.json"] == {
        "keys": 1, "u_id_keys": 0, "u_id_values": 2, "word_values": 0, "actor_values": 0,
        "values_truncated": 0,
    }
    assert report["totals"]["unclassified_entries"] == 1
    assert report["totals"]["configs_with_authority"] == 1
    assert not report["complete"]


def test_wal_commits_are_scanned_without_any_source_write_and_artifact_is_disposable(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    db = sqlite3.connect(root / "data-without-extension")
    try:
        db.execute("PRAGMA journal_mode=wal")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE t (value TEXT)")
        db.execute("INSERT INTO t VALUES (?)", (HOME,))
        db.commit()
        # Keep the writer open: closing the last connection normally checkpoints WAL.
        assert (root / "data-without-extension-wal").stat().st_size > 0
        before = _digest(root)
        report = inv.inventory(root)
        assert _digest(root) == before
        assert report["totals"]["value_rows_with_u_ids"] == 1
        assert report["acquisition"]["hot_journals"] == ["data-without-extension-wal"]
        assert not Path(report["acquisition"]["artifact"]).exists()
        artifact = tmp_path / "retained"
        report = inv.inventory(root, keep_artifact=artifact)
        assert artifact.is_dir()
        assert report["totals"]["value_rows_with_u_ids"] == 1
        assert _digest(root) == before
    finally:
        db.close()


def test_a_write_locked_database_still_reports_its_committed_findings(tmp_path):
    """The -shm is never copied, so a busy writer cannot blank a database's counts.

    A writer mid-transaction holds the -shm unreadable (Windows raises
    PermissionError); SQLite rebuilds it from the -wal, so requiring it would
    have discarded every finding in the database instead of counting it.
    """
    source = tmp_path / "source"
    source.mkdir()
    db = sqlite3.connect(source / ".conversation_memory.db")
    holder = sqlite3.connect(source / ".conversation_memory.db")
    try:
        db.execute("PRAGMA journal_mode=wal")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE t (value TEXT)")
        db.execute("INSERT INTO t VALUES (?)", (HOME,))
        db.commit()
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES ('in-flight')")
        assert (source / ".conversation_memory.db-shm").exists()

        report = inv.inventory(source)

        assert ".conversation_memory.db" in report["sqlite"]
        assert report["totals"]["value_rows_with_u_ids"] == 1
        assert {"path": ".conversation_memory.db-shm", "reason": "sqlite-shm"} in (
            report["exemptions"])
        assert not any("-shm" in entry["path"] for entry in report["unscanned"])
        assert not any("incomplete-sqlite-family" in reason
                       for reason in report["incomplete_reasons"])
    finally:
        holder.rollback()
        holder.close()
        db.close()


def test_symlinks_including_config_are_never_read(tmp_path):
    source = tmp_path / "source"
    home = source / HOME
    home.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("allowed_providers: [secret]", encoding="utf-8")
    try:
        os.symlink(outside, home / "config.yaml")
        os.symlink(tmp_path, source / "escape", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        # owner=cowork-agent runs-in=linux-ci: this host cannot create a symlink
        # (os.symlink raises WinError 1314 without developer mode), and the case
        # is also covered by the POSIX containment probe under WSL.
        pytest.skip(f"owner=cowork-agent runs-in=linux-ci: no symlink here ({exc})")
    report = inv.inventory(source)
    assert {e["path"] for e in report["unscanned"]} == {f"{HOME}/config.yaml", "escape"}
    assert not report["homes"][HOME]["config_authority"]
    assert not report["complete"]


def test_fifo_is_never_opened(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("owner=cowork-agent runs-in=linux-ci: os.mkfifo is POSIX-only")
    os.mkfifo(tmp_path / "pipe")
    report = inv.inventory(tmp_path)
    assert {"path": "pipe", "category": "link-or-special"} in report["unscanned"]
    assert not report["complete"]


def test_encodings_generated_columns_nested_check_json_values_and_derived_ids(tmp_path):
    db = sqlite3.connect(tmp_path / "backup.db.bak-premigrate-x")
    db.executescript(f"""
        CREATE TABLE t (storage_class TEXT CHECK (lower(storage_class) != 'universe'),
          value TEXT, number INTEGER, universe_generated TEXT
          GENERATED ALWAYS AS (lower(storage_class)) VIRTUAL);
        INSERT INTO t(storage_class, value, number) VALUES ('scratch', 'lease-{HOME}', 42);
    """)
    db.close()
    (tmp_path / "status.json").write_text(
        '{"actor": "universe:alice", "scope": "universe"}', encoding="utf-8")
    report = inv.inventory(tmp_path)
    found = report["sqlite"]["backup.db.bak-premigrate-x"]
    assert {"type": "check", "name": "t"} in found["schema_sql"]
    assert {"type": "table_sql", "name": "t"} in found["schema_sql"]
    assert {"table": "t", "column": "universe_generated", "generated": True} in found["columns"]
    assert found["table_values"]["t"]["columns_not_value_scanned"] == ["number"]
    assert found["table_values"]["t"]["u_ids"] == 1
    assert report["json"]["status.json"]["actor_values"] == 1
    assert report["json"]["status.json"]["word_values"] == 1
    assert "hashed lease keys" in report["deferred"][0]
    assert not report["migration_ready"]
    assert report["complete"]
    assert not inv.U_ID.search(("x" + HOME).encode())
    assert not inv.U_ID.search((HOME + "Z").encode())
    assert inv.WORD.search(b"universes")


def test_limits_oversize_prunes_and_deadline_are_explicit(tmp_path):
    home = tmp_path / HOME
    home.mkdir()
    (home / "status.json").write_bytes(b"x" * 100)
    for directory in (home / "workspace", tmp_path / ".git"):
        directory.mkdir()
        (directory / "not-read").write_bytes(b"secret")
    report = inv.inventory(tmp_path, limits=inv.Limits(max_file_bytes=10))
    assert {"path": f"{HOME}/status.json", "category": "oversize", "size": 100} in (
        report["unscanned"])
    assert {"path": f"{HOME}/workspace", "reason": "verbatim:workspace"} in report["exemptions"]
    assert {"path": ".git", "reason": "prune:.git"} in report["exemptions"]
    assert report["acquisition"]["files"][0]["bytes"] == 10
    assert not report["complete"]
    for limits, reason in ((inv.Limits(deadline_s=0), "deadline"),
                           (inv.Limits(max_entries=0), "max_entries"),
                           (inv.Limits(max_total_bytes=1), "max_total_bytes")):
        report = inv.inventory(tmp_path, limits=limits)
        assert not report["complete"]
        assert any(reason in r for r in report["incomplete_reasons"])


def test_the_value_limit_binds_json_and_lancedb_not_only_sqlite(tmp_path):
    """An id past --max-value-bytes is not counted, and the gap is recorded.

    The cap used to apply only to SQLite, so JSON and LanceDB read whole values
    and reported `complete: true` while advertising a bound they did not apply.
    """
    buried = "x" * 100 + "-" + HOME
    (tmp_path / "status.json").write_text(json.dumps({"actor": buried}), encoding="utf-8")

    report = inv.inventory(tmp_path, limits=inv.Limits(max_value_bytes=4))

    assert report["json"]["status.json"]["u_id_values"] == 0
    assert report["json"]["status.json"]["values_truncated"]
    assert not report["complete"]
    assert any("JSON value limit: status.json" in r for r in report["incomplete_reasons"])
    # The same id inside the cap is still found, so the bound is what changed.
    assert inv.inventory(tmp_path)["json"]["status.json"]["u_id_values"] == 1


def test_the_value_limit_binds_lancedb_rows(tmp_path):
    try:
        import lancedb
    except ImportError:
        # Without the library there are no rows to bound, and the store being
        # reported unscanned is the assertion that matters -- which the test
        # below already makes. Branch rather than skip, so both environments
        # check something.
        (tmp_path / "lancedb").mkdir()
        assert not inv.inventory(tmp_path)["lancedb"]["lancedb"]["scanned"]
        return
    store = lancedb.connect(str(tmp_path / "lancedb"))
    store.create_table("ids", [{"id": "x" * 100 + "-" + HOME}])

    report = inv.inventory(tmp_path, limits=inv.Limits(max_value_bytes=4))

    found = report["lancedb"]["lancedb"]["tables"]["ids"]
    assert found["u_ids"] == 0
    assert found["values_truncated"]
    assert not report["complete"]
    assert any("LanceDB row/value limit" in r for r in report["incomplete_reasons"])
    assert inv.inventory(tmp_path)["lancedb"]["lancedb"]["tables"]["ids"]["u_ids"] == 1


def test_an_argparse_usage_error_exits_one_like_every_other_usage_error(tmp_path):
    """Exit 1 is the documented usage code; argparse's own exit 2 means incomplete."""
    assert inv.main([]) == 1
    assert inv.main([str(tmp_path), "--strict"]) == 1
    assert inv.main([str(tmp_path), "--max-rows", "not-a-number"]) == 1
    with pytest.raises(SystemExit) as asked_for_help:
        inv.main(["--help"])
    assert not asked_for_help.value.code


def test_failure_semantics_default_exit_partial_and_corrupt_sqlite(tmp_path, capsys):
    _fixture(tmp_path)
    assert inv.main([str(tmp_path)]) == 2
    assert "mystery.bin" in capsys.readouterr().out
    (tmp_path / HOME / "mystery.bin").unlink()
    assert inv.main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert inv.main([str(tmp_path), "--no-values", "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["partial"] and not report["complete"] and not report["migration_ready"]
    assert report["json"][f"{HOME}/status.json"]["u_id_values"] == 0
    assert report["totals"]["value_rows_with_u_ids"] == 0
    (tmp_path / "corrupted").write_bytes(inv.MAGIC + b"invalid" * 100)
    report = inv.inventory(tmp_path)
    assert report["sqlite"]["corrupted"]["error"]
    assert not report["complete"]


def test_rows_and_cells_have_distinct_counts_and_sql_bounds_are_explicit(tmp_path):
    db = sqlite3.connect(tmp_path / "db")
    db.execute("CREATE TABLE t (a TEXT, b BLOB)")
    db.executemany("INSERT INTO t VALUES (?, ?)", [(HOME, HOME.encode()), ("tail", b"x" * 30)])
    db.commit()
    db.close()
    report = inv.inventory(tmp_path)
    assert report["totals"]["value_rows_with_u_ids"] == 1
    assert report["totals"]["value_cells_with_u_ids"] == 2
    report = inv.inventory(tmp_path, limits=inv.Limits(max_rows=1))
    assert report["sqlite"]["db"]["table_values"]["t"]["truncated"]
    assert not report["complete"]
    report = inv.inventory(tmp_path, limits=inv.Limits(max_value_bytes=10))
    assert report["sqlite"]["db"]["table_values"]["t"]["values_truncated"]
    assert not report["complete"]


def test_artifact_containment_and_usage_errors(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    assert inv.main([str(source), "--keep-artifact", str(source / "artifact")]) == 1
    assert inv.main([str(tmp_path / "missing")]) == 1
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("keep", encoding="utf-8")
    assert inv.main([str(source), "--keep-artifact", str(existing)]) == 1
    assert (existing / "keep").read_text() == "keep"


def test_source_mutation_is_reported(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "status.json"
    path.write_text("{}", encoding="utf-8")
    original = inv.os.lstat
    calls = 0

    def mutate_on_recheck(name, *args, **kwargs):
        nonlocal calls
        if Path(name) == path:
            calls += 1
            if calls == 2:
                path.write_text('{"changed": true}', encoding="utf-8")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(inv.os, "lstat", mutate_on_recheck)
    report = inv.inventory(source)
    assert "status.json" in report["acquisition"]["source_mutated"]
    assert not report["complete"]


def test_lancedb_rows_or_explicit_unscanned(tmp_path):
    try:
        import lancedb
    except ImportError:
        (tmp_path / "lancedb").mkdir()
        report = inv.inventory(tmp_path)
        assert not report["lancedb"]["lancedb"]["scanned"]
        assert not report["complete"]
        return
    store = lancedb.connect(str(tmp_path / "lancedb"))
    store.create_table("ids", [{"id": HOME, "universe_actor": "universe:alice"}])
    report = inv.inventory(tmp_path)
    found = report["lancedb"]["lancedb"]
    assert found["scanned"], found
    assert found["tables"]["ids"]["rows_scanned"] == 1
    assert found["tables"]["ids"]["rows_total"] == 1
    assert found["tables"]["ids"]["u_ids"] == 1
    assert found["tables"]["ids"]["actor_prefix"] == 1
    assert not found["tables"]["ids"]["truncated"]


def test_lancedb_import_failure_is_incomplete(tmp_path, monkeypatch):
    (tmp_path / "lancedb").mkdir()
    original = builtins.__import__

    def fail_import(name, *args, **kwargs):
        if name == "lancedb":
            raise ImportError("deliberate")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    report = inv.inventory(tmp_path)
    assert not report["lancedb"]["lancedb"]["scanned"]
    assert not report["complete"]
    assert any("lancedb not importable" in r for r in report["incomplete_reasons"])


def test_temp_root_inside_source_is_refused_before_creating_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(inv.tempfile, "gettempdir", lambda: str(tmp_path))
    before = _digest(tmp_path)
    with pytest.raises(ValueError, match="temporary directory is inside source"):
        inv.inventory(tmp_path)
    assert _digest(tmp_path) == before


def test_replaced_file_descriptor_is_rechecked_before_read(tmp_path, monkeypatch):
    import stat

    source = tmp_path / "source"
    source.mkdir()
    (source / "status.json").write_text("{}", encoding="utf-8")
    real_fstat = os.fstat
    swapped = {"done": False}

    def fstat_as_fifo(fd):
        """A real stat_result with only its type bits changed.

        The double has to stay a stat_result: `os.fstat` is not ours alone, and
        shutil.rmtree calls it too (on Linux, where it compares st_ino to guard
        the artifact cleanup). A stand-in with one attribute passed on Windows
        and broke there.
        """
        st = real_fstat(fd)
        if swapped["done"] or not stat.S_ISREG(st.st_mode):
            return st
        swapped["done"] = True
        fields = list(st)
        fields[0] = (st.st_mode & ~stat.S_IFMT(st.st_mode)) | stat.S_IFIFO
        return os.stat_result(tuple(fields))

    monkeypatch.setattr(inv.os, "fstat", fstat_as_fifo)
    report = inv.inventory(source)
    assert swapped["done"], "the descriptor was never re-checked"
    assert {"path": "status.json", "category": "replaced-file"} in report["unscanned"]
    assert not report["acquisition"]["files"]
    assert not report["complete"]


def test_an_already_expired_deadline_refuses_the_scan_before_it_opens_anything(
        tmp_path, monkeypatch):
    """The cheap case: the deadline is gone before the scan starts."""
    source = tmp_path / "source"
    source.mkdir()
    db = sqlite3.connect(source / "data")
    db.execute("CREATE TABLE t (value TEXT)")
    db.close()
    original = inv.scan_sqlite
    opened = []

    def expired_scan(path, standalone, **kwargs):
        kwargs["deadline"] = 0
        return original(path, standalone, **kwargs)

    monkeypatch.setattr(inv, "scan_sqlite", expired_scan)
    monkeypatch.setattr(inv.sqlite3, "connect",
                        lambda *a, **k: opened.append(a) or pytest.fail("opened a database"))
    report = inv.inventory(source)
    assert report["sqlite"]["data"]["deadline_hit"]
    assert not opened, "an expired deadline must refuse before opening anything"
    assert not report["complete"]
    assert any("deadline_hit: data" in r for r in report["incomplete_reasons"])


def _scan_with_clock_tripping_after(monkeypatch, trips_after):
    """Run the real scan with a clock that runs out mid-scan, not before it.

    `trips_after` monotonic reads happen inside the deadline; the next one is
    past it. Only the sqlite scan sees this clock -- acquisition has already
    finished, and the real clock is restored before the caller continues.
    """
    original_scan = inv.scan_sqlite
    real_monotonic = inv.time.monotonic
    reads = {"n": 0}

    def scan(path, standalone, **kwargs):
        deadline = kwargs["deadline"]

        def clock():
            reads["n"] += 1
            return deadline - 1 if reads["n"] <= trips_after else deadline + 1

        inv.time.monotonic = clock
        try:
            return original_scan(path, standalone, **kwargs)
        finally:
            inv.time.monotonic = real_monotonic

    monkeypatch.setattr(inv, "scan_sqlite", scan)
    return reads


def test_the_deadline_cancels_a_backup_already_under_way(tmp_path, monkeypatch):
    """Running out of time DURING the backup is reported, not swallowed.

    The first monotonic read is scan_sqlite's own check, which passes; nothing
    else reads the clock before `Connection.backup`, so the read that trips is
    inside it.
    """
    source = tmp_path / "source"
    source.mkdir()
    db = sqlite3.connect(source / "data")
    db.execute("CREATE TABLE t (value TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [(f"row-{i}",) for i in range(2000)])
    db.commit()
    db.close()
    reads = _scan_with_clock_tripping_after(monkeypatch, trips_after=1)

    report = inv.inventory(source)

    found = report["sqlite"]["data"]
    assert reads["n"] > 1, "the backup never read the clock"
    assert found["deadline_hit"] and found["error"]
    assert found["table_values"] == {}, "it stopped in the backup, before any table"
    assert not report["complete"]


def test_the_deadline_cancels_a_value_scan_part_way_through_a_table(tmp_path, monkeypatch):
    """Running out of time while READING rows abandons the rest, and says so.

    The clock is tripped once the first row's value has been examined, so the
    backup and the schema pass are already done and the cancellation can only
    come from inside the row loop.
    """
    source = tmp_path / "source"
    source.mkdir()
    db = sqlite3.connect(source / "data")
    db.execute("CREATE TABLE t (value TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [(f"{HOME}-{i}",) for i in range(2000)])
    db.commit()
    db.close()

    original_scan = inv.scan_sqlite
    real_monotonic = inv.time.monotonic
    real_as_bytes = inv._as_bytes

    def scan(path, standalone, **kwargs):
        deadline = kwargs["deadline"]
        state = {"read_a_value": False}

        def as_bytes(value):
            state["read_a_value"] = True
            return real_as_bytes(value)

        inv.time.monotonic = lambda: (
            deadline + 1 if state["read_a_value"] else deadline - 1)
        inv._as_bytes = as_bytes
        try:
            return original_scan(path, standalone, **kwargs)
        finally:
            inv.time.monotonic = real_monotonic
            inv._as_bytes = real_as_bytes

    monkeypatch.setattr(inv, "scan_sqlite", scan)

    report = inv.inventory(source)

    found = report["sqlite"]["data"]
    assert found["table_values"]["t"]["rows_total"] == 2000, "the backup completed"
    assert found["table_values"]["t"]["rows_scanned"] == 1, "it read past the deadline"
    assert found["error"] == "TimeoutError" and found["deadline_hit"]
    assert not report["complete"]
    assert report["totals"]["value_rows_with_u_ids"] == 1, (
        "the one row it did read is still counted -- an abandoned scan reports "
        "what it saw, it does not report a zero"
    )
