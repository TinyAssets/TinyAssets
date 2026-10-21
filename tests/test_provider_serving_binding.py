from __future__ import annotations



def _definition() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "Serving agent",
        "description": "A private serving-binding fixture",
        "tags": ["test"],
        "components": {
            "identity": {"kind": "soul", "config": {"voice": "direct"}},
        },
    }


def _binding() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "Serving agent",
        "role": "writer",
    }


def _seed_universe(tmp_path):
    from tests.support.http_model import seed_http_assignment
    return seed_http_assignment(tmp_path, owner_user_id="owner-1", universe_id="u-owner",
                                select_for_serving=False, return_binding=True)




