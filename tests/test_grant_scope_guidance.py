"""The served guidance must teach the grant shape that makes patching possible.

Founder, 2026-08-29, after the universe successfully shipped a one-line colour
change but could not do anything more: the goal was not met *"in any real useful
ongoing sense of users being able to scope, build and push patches to github"*.

The cause was not a missing capability. ``path_template`` has supported a
multi-segment ``{name+}`` rest placeholder all along, and
``/repos/o/r/contents/{path+}`` reaches every file in a repo while still refusing
traversal and other repos. But every example in the served guidance was an exact
path, and the rest placeholder was mentioned nowhere the agent could see it — so
the agent asked for one file at a time, which is the one thing it cannot
enumerate up front, and each new file cost the user another approval.

These tests hold both halves together: the guidance must TEACH the pattern, and
the pattern must actually WORK. Either alone is how this regressed.
"""

from __future__ import annotations

import pytest

from tinyassets.api.http_connection import _parse_allowed_endpoints
from tinyassets.storage.outbound_connections import _path_matches_template

_REPO = "/repos/tinyassets/tinyassets"
_PATH_RE = r"[A-Za-z0-9._\-/]{1,200}"


def _contents_endpoint():
    return _parse_allowed_endpoints(
        [
            {
                "host": "api.github.com",
                "path_template": f"{_REPO}/contents/{{path+}}",
                "methods": ["GET", "PUT"],
                "param_patterns": {"path": _PATH_RE},
            }
        ]
    )[0]


# --- the guidance must say it ------------------------------------------------






# --- and the pattern must actually work --------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        f"{_REPO}/contents/tinyassets/onboarding/request_theme.json",
        f"{_REPO}/contents/tinyassets/api/prompts.py",
        f"{_REPO}/contents/deploy/compose.yml",
        f"{_REPO}/contents/README.md",
    ],
)
def test_one_grant_reaches_every_file_in_the_repo(path: str):
    endpoint = _contents_endpoint()
    assert _path_matches_template(
        path, endpoint.path_template, dict(endpoint.param_patterns)
    ), f"{path} should be inside a contents/{{path+}} grant"


@pytest.mark.parametrize(
    "path",
    [
        # traversal out of the repo
        f"{_REPO}/contents/../../etc/passwd",
        # somebody else's repository
        "/repos/someoneelse/private/contents/secrets.env",
        # a different API surface on the same repo
        f"{_REPO}/git/refs/heads/main",
        # the prefix itself, with no file
        f"{_REPO}/contents",
    ],
)
def test_the_grant_is_still_tight(path: str):
    """Breadth over the repo must not become breadth over anything else."""
    endpoint = _contents_endpoint()
    assert not _path_matches_template(
        path, endpoint.path_template, dict(endpoint.param_patterns)
    ), f"{path} must NOT be inside a contents/{{path+}} grant"


def test_a_rest_placeholder_without_a_pattern_is_refused():
    """The regex is what keeps it tight, so it cannot be optional."""
    with pytest.raises(Exception):
        _parse_allowed_endpoints(
            [
                {
                    "host": "api.github.com",
                    "path_template": f"{_REPO}/contents/{{path+}}",
                    "methods": ["GET"],
                }
            ]
        )


# --- the ask path must not strip the pattern that makes a job-scoped grant valid


def test_an_extend_ask_for_a_job_scoped_grant_keeps_its_param_patterns():
    """Found 2026-08-29 before the live test: `_validated_endpoint_list` rebuilt
    each endpoint with only host/path/methods, so the exact request the agent is
    taught to raise was refused at ask time with "param_patterns must declare
    exactly the path placeholders". The verb, the enforcement and the teaching
    all existed; the request pipe dropped the one field that made it valid."""
    from tinyassets.api.pending_requests import _validated_action

    out = _validated_action({
        "type": "extend_http", "destination": "github",
        "endpoints": [{
            "host": "api.github.com",
            "path_template": f"{_REPO}/contents/{{path+}}",
            "methods": ["GET", "PUT"],
            "param_patterns": {"path": _PATH_RE},
        }, {
            "host": "api.github.com",
            "path_template": f"{_REPO}/git/refs/heads/{{branch+}}",
            "methods": ["PATCH"],
            "param_patterns": {"branch": r"[A-Za-z0-9._\-/]{1,120}"},
        }],
    })
    assert out["type"] == "extend_http"
    kept = {e["path_template"]: e.get("param_patterns") for e in out["endpoints"]}
    assert kept[f"{_REPO}/contents/{{path+}}"] == {"path": _PATH_RE}
    assert kept[f"{_REPO}/git/refs/heads/{{branch+}}"] == {"branch": r"[A-Za-z0-9._\-/]{1,120}"}


def test_an_ask_with_a_placeholder_and_no_pattern_is_still_refused():
    """Forwarding is not trusting: the deposit parser still validates."""
    from tinyassets.api.pending_requests import _validated_action

    with pytest.raises(Exception):
        _validated_action({
            "type": "extend_http", "destination": "github",
            "endpoints": [{"host": "api.github.com",
                           "path_template": f"{_REPO}/contents/{{path+}}",
                           "methods": ["GET"]}],
        })
