"""Connect in the app must make the universe SERVE, not just hold a credential.

Live phone test 2026-08-21: the OpenAI credential was deposited, yet every turn
still failed with "exactly one founder serving binding is required". These run
the REAL custom-agent + serving modules against a temp base path (same chain
the served-router tests use) — no mocks of the authority layer.
"""

from __future__ import annotations

from tinyassets.onboarding import serving as sv


def _grant_admin(tmp_path, owner="owner-1", uid="u-owner"):
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path, universe_id=uid, actor_id=owner, permission="admin", granted_by=owner
    )




def _serving_binding(tmp_path, uid="u-owner", owner="owner-1"):
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding

    return resolve_serving_agent_binding(tmp_path, universe_id=uid, owner_user_id=owner)














def test_anonymous_or_missing_universe_is_held_not_raised(tmp_path):
    assert (
        sv.ensure_founder_serving(
            base_path=tmp_path,
            universe_dir=tmp_path,
            owner_user_id="anonymous",
            universe_id="u-owner",
            service="codex",
        )["status"]
        == "held"
    )
    # A name that is neither alias nor registered connection is HELD, but for a
    # real authority reason -- not because the platform keeps a vendor
    # allowlist. It used to answer `unsupported_service` for anything but
    # claude/codex, which refused every user with their own endpoint (founder,
    # 2026-09-03: "we shouldnt have a chatgpt spacific path").
    held = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=tmp_path,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="gemini",
    )
    assert held["status"] == "held"
    assert held["reason"] != "unsupported_service", held
    assert held.get("detail"), "a refusal has to say why"


def test_an_unnamed_service_is_held_rather_than_guessed(tmp_path):
    """Opening the name up does not mean accepting an empty one."""
    held = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=tmp_path,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="   ",
    )
    assert held == {"status": "held", "reason": "no_service_named"}
















