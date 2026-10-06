"""The owner's live view: project progress and activities (harness D2, the village).

A building grows with its project; a villager shows waiting when its agent waits
on the owner. Counts come from every run, never a page of them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tinyassets import agent_activities as acts
from tinyassets import live_view


def _seed_runs(base: Path, rows):
    from tinyassets.runs import initialize_runs_db

    db = initialize_runs_db(base)
    with sqlite3.connect(db) as conn:
        for run_id, branch, status, actor, started, finished in rows:
            conn.execute(
                "INSERT INTO runs (run_id, branch_def_id, thread_id, status, actor, "
                "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, branch, run_id, status, actor, started, finished))


def test_projects_count_every_run_of_this_command_center(tmp_path):
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(tmp_path)
    rows = [(f"r{i}", "branch-map", "completed", "universe:u-alpha", 100 + i, 101 + i)
            for i in range(70)]
    rows += [("rf", "branch-map", "failed", "universe:u-alpha", 300, 301),
             ("rr", "branch-mail", "running", "universe:u-alpha", 400, None),
             ("other", "branch-map", "completed", "universe:u-beta", 500, 501)]
    _seed_runs(tmp_path, rows)
    got = {p["project_id"]: p for p in live_view.projects(tmp_path, "u-alpha")}
    assert set(got) == {"branch-map", "branch-mail"}, "another command center's runs stay out"
    assert (got["branch-map"]["runs"], got["branch-map"]["completed"],
            got["branch-map"]["failed"]) == (71, 70, 1), "every run counted, not a page"
    assert got["branch-map"]["state"] == "failed" and got["branch-mail"]["state"] == "running"
    assert got["branch-mail"]["last_activity_at"] == 400


def test_activities_and_agent_states_show_who_waits_on_the_owner(tmp_path):
    from tinyassets.custom_agents import create_binding, publish_definition

    universe = tmp_path / "u-alpha"
    universe.mkdir()
    definition = publish_definition(tmp_path, author_id="o", payload={
        "schema_version": 1, "name": "Mapper", "components": {
            "identity": {"kind": "soul", "config": {"instructions": "Map projects."}},
        },
    })
    agent_id = create_binding(
        tmp_path, universe_id=universe.name,
        definition_id=definition["agent_definition_id"], created_by="o",
        payload={"schema_version": 1, "name": "Mapper"},
    )["agent_binding_id"]
    working = acts.create(universe, owner_principal="o", title="map", brief="b",
                          origin_kind="ask", agent_id=agent_id)["activity_id"]
    gen = acts.claim(universe, working, replaceable=lambda r: False)
    acts.bind_run(universe, working, gen, "run-1")
    waiting = acts.create(universe, owner_principal="o", title="mail", brief="b",
                          origin_kind="ask")["activity_id"]
    gen2 = acts.claim(universe, waiting, replaceable=lambda r: False)
    acts.bind_run(universe, waiting, gen2, "run-2")
    acts.wait_on(universe, waiting, "req-1", "approve the email")
    for i in range(live_view.RECENT_FINISHED + 5):
        done = acts.create(universe, owner_principal="o", title=f"done {i}", brief="b",
                           origin_kind="ask")["activity_id"]
        acts.transition(universe, done, acts.COMPLETED, outcome="stopped", event="stopped")
    rows = live_view.activity_rows(universe)
    assert {r["activity_id"] for r in rows if r["status"] not in acts.TERMINAL} == {
        working, waiting}
    assert sum(r["status"] in acts.TERMINAL for r in rows) == live_view.RECENT_FINISHED
    assert all("brief" not in r and "owner_principal" not in r for r in rows)
    assert live_view.agent_states(rows) == {agent_id: "working",
                                            "main": "waiting_on_you"}
