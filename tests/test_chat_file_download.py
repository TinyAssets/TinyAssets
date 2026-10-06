"""Real owner middleware + ACL + folder bytes, not a mocked download decision."""

import pytest

from tests.test_owner_door import HOME_A, A, B, _headers, door  # noqa: F401
from tinyassets.universe_tools import WORKSPACE_DIR


@pytest.mark.parametrize("payload", [b"col,value\r\nA,2\r\n", b"PK\x03\x04\x00\xffbinary", b""])
def test_owner_downloads_exact_workspace_bytes_and_other_user_cannot(door, payload):  # noqa: F811
    client, base = door
    exports = base / HOME_A / WORKSPACE_DIR / "exports"
    exports.mkdir(parents=True)
    (exports / "report.xlsx").write_bytes(payload)
    args = {"graph_id": HOME_A, "path": "exports/report.xlsx"}
    response = client.post("/app/api/file", headers=_headers(A), json=args)
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    denied = client.post("/app/api/file", headers=_headers(B), json=args)
    assert denied.status_code == 404
    assert denied.json() == {"error": "not_found"}
    assert client.post("/app/api/file", json=args).status_code == 401
    assert (
        client.post(
            "/app/api/file", headers=_headers(A), json={**args, "path": "/u/exports/report.xlsx"}
        ).content
        == payload
    )


@pytest.mark.parametrize(
    "path",
    [
        "../soul.md",
        "/etc/passwd",
        "exports/../../soul.md",
        "exports\\x",
        "missing.csv",
        "/u",
        "./soul.md",
    ],
)
def test_invalid_and_missing_paths_are_indistinguishable(door, path):  # noqa: F811
    client, _ = door
    response = client.post(
        "/app/api/file", headers=_headers(A), json={"graph_id": HOME_A, "path": path}
    )
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


def test_public_visibility_does_not_grant_file_access(door):  # noqa: F811
    from tinyassets.api.visibility import set_universe_visibility
    from tinyassets.daemon_server import ensure_universe_registered

    client, base = door
    ensure_universe_registered(base, universe_id=HOME_A, universe_path=base / HOME_A)
    set_universe_visibility(HOME_A, "public", source="owner")
    response = client.post(
        "/app/api/file", headers=_headers(B), json={"graph_id": HOME_A, "path": "soul.md"}
    )
    assert response.status_code == 404


def test_link_cannot_download_other_folder(door):  # noqa: F811
    import os

    client, base = door
    outside = base / "secret"
    outside.write_bytes(b"not yours")
    link = base / HOME_A / "leak"
    try:
        link.symlink_to(outside)
    except OSError:
        if os.name == "nt":
            pytest.skip(
                "Windows symlink privilege absent; runs-in=linux-oracle owner=chat-render"
            )
        raise
    response = client.post(
        "/app/api/file", headers=_headers(A), json={"graph_id": HOME_A, "path": "leak"}
    )
    assert response.status_code == 404


def test_request_shape_and_method_are_bounded(door):  # noqa: F811
    client, _ = door
    assert client.get("/app/api/file", headers=_headers()).status_code == 405
    assert client.post("/app/api/file", headers=_headers(), json={"path": "x"}).status_code == 400
    assert client.post("/app/api/file", headers=_headers(), content="x" * 17000).status_code == 413
