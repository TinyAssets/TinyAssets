"""Real stored opt-in, held stores and quota; never activate a background worker."""


import pytest

from tests.test_command_center_release_policy import published as _published
from tinyassets import command_center_update_executor as executor
from tinyassets import command_center_update_policy as policy
from tinyassets.app_ui_store import get_app_ui


@pytest.fixture(name="published")
def owner_release_fixture(tmp_path, monkeypatch):
    from tinyassets.commons_catalog import _agent_connect
    from tinyassets.universe_owner import record_creation

    data = _published.__wrapped__(tmp_path, monkeypatch)
    with _agent_connect(tmp_path) as conn:
        record_creation(conn, universe_id=data["uid"], owner_id=data["owner"])
    from tinyassets.broker import owner_reads

    plans = owner_reads.Plans(tmp_path)
    plans.apply(data["owner"], tier="free", event_created=0)
    remote = owner_reads.remote
    monkeypatch.setattr(owner_reads, "remote", lambda operation, **args: (
        plans.read(data["owner"]) if operation == "plan" else remote(operation, **args)))
    return data


def apply(data):
    return executor.apply_next(
        data["base"],
        owner_id=data["owner"],
        universe_id=data["uid"],
        adoption_id=data["adoption"]["adoption_id"],
    )


def ui(data):
    return get_app_ui(data["base"], owner_user_id=data["owner"], universe_id=data["uid"])


def release(data, **edit):
    return data["publish"](parent=data["first"], edit=lambda value: value.update(**edit))


def disable(data):
    from tests.test_command_center_packages import _as

    with _as(data["owner"]):
        req = policy.preview_policy(
            universe_id=data["uid"], adoption_id=data["adoption"]["adoption_id"], enabled=False
        )
        return policy.commit_policy(
            universe_id=data["uid"],
            request_id=req["request_id"],
            plan_digest=req["plan_digest"],
            decision="accepted",
        )




















































