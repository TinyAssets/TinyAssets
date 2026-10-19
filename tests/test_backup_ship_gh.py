"""Tests for scripts/backup_ship_gh.py — offsite GH release asset upload."""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

# Load the script as a module without executing __main__.
_SCRIPT = Path(__file__).parent.parent / "scripts" / "backup_ship_gh.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("backup_ship_gh", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bsg = _load()


# ── Helpers ───────────────────────────────────────────────────────────

def _fake_post_fn(responses: list[dict]) -> object:
    """Returns a post_fn that pops responses in order."""
    resp_iter = iter(responses)

    def _fn(req):
        return next(resp_iter)

    return _fn


def _make_tarball(
    tmp_path: Path, name: str = "tinyassets-data-2026-04-20T02-00-00Z.tar.gz",
) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x1f\x8b" + b"\x00" * 100)  # minimal gzip magic bytes
    return p


# ── Token validation ──────────────────────────────────────────────────


def test_ship_exits_1_without_gh_token(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    tarball = _make_tarball(tmp_path)
    with pytest.raises(SystemExit) as exc:
        bsg.ship(tarball, post_fn=lambda req: {})
    assert exc.value.code == 1


def test_ship_exits_2_when_tarball_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    missing = tmp_path / "no-such-file.tar.gz"
    with pytest.raises(SystemExit) as exc:
        bsg.ship(missing, post_fn=lambda req: {})
    assert exc.value.code == 2


# ── ensure_repo ───────────────────────────────────────────────────────


def test_ensure_repo_noop_when_exists() -> None:
    calls = []

    def _post(req):
        calls.append(req.full_url)
        return {"id": 1}

    bsg.ensure_repo("tok", "owner/repo", post_fn=_post)
    assert len(calls) == 1
    assert "/repos/owner/repo" in calls[0]
    assert "user/repos" not in calls[0]


def test_ensure_repo_creates_when_missing() -> None:
    calls = []
    seq = [RuntimeError("404: not found"), {"id": 2}]
    idx = [0]

    def _post(req):
        calls.append(req.full_url)
        resp = seq[idx[0]]
        idx[0] += 1
        if isinstance(resp, Exception):
            raise resp
        return resp

    bsg.ensure_repo("tok", "owner/newrepo", post_fn=_post)
    assert idx[0] == 2
    assert calls == [
        "https://api.github.com/repos/owner/newrepo",
        "https://api.github.com/user/repos",
    ]


def test_ensure_repo_re_raises_non_404() -> None:
    def _post(req):
        raise RuntimeError("500: server error")

    with pytest.raises(RuntimeError, match="500"):
        bsg.ensure_repo("tok", "owner/repo", post_fn=_post)


# ── create_release ────────────────────────────────────────────────────


def test_create_release_posts_correct_tag() -> None:
    captured = []

    def _post(req):
        captured.append(json.loads(req.data.decode()))
        return {"id": 42, "upload_url": "https://uploads.github.com/upload{?name,label}"}

    result = bsg.create_release("tok", "owner/repo", "my-tag", post_fn=_post)
    assert captured[0]["tag_name"] == "my-tag"
    assert result["id"] == 42


# ── _upload_asset ─────────────────────────────────────────────────────


def test_upload_asset_strips_template_suffix(tmp_path) -> None:
    tarball = _make_tarball(tmp_path)
    captured_urls = []

    def _post(req):
        captured_urls.append(req.full_url)
        return {"name": tarball.name, "browser_download_url": "https://example.com"}

    bsg._upload_asset(
        "tok",
        "https://uploads.github.com/repos/owner/repo/releases/1/assets{?name,label}",
        tarball.name,
        tarball,
        post_fn=_post,
    )
    url = captured_urls[0]
    assert "{" not in url
    assert f"name={tarball.name}" in url


def test_upload_asset_uses_gzip_content_type(tmp_path) -> None:
    tarball = _make_tarball(tmp_path)
    captured_headers = []

    def _post(req):
        captured_headers.append(dict(req.headers))
        return {"name": tarball.name}

    bsg._upload_asset("tok", "https://uploads.example.com/upload{?name,label}",
                      tarball.name, tarball, post_fn=_post)
    ct = captured_headers[0].get("Content-type", "")
    assert "gzip" in ct


# ── prune_releases ────────────────────────────────────────────────────


def _make_releases(n: int) -> list[dict]:
    # Prunable (backup-prefixed) tags — prune_releases ignores anything
    # outside PRUNABLE_TAG_PREFIXES since the 2026-07-15 scoping fix.
    return [
        {"id": i, "tag_name": f"tinyassets-data-2026-04-{i + 1:02d}T00-00-00Z",
         "created_at": f"2026-04-{i + 1:02d}T00:00:00Z"}
        for i in range(1, n + 1)
    ]


def test_prune_releases_keeps_newest(monkeypatch) -> None:
    releases = _make_releases(35)
    deleted_ids = []

    def _post(req):
        if "DELETE" in str(req.get_method()):
            deleted_ids.append(req.full_url)
            return {}
        return releases  # GET releases

    # Monkey-patch list_releases + delete_release to avoid URL parsing noise.
    monkeypatch.setattr(bsg, "list_releases",
                        lambda token, repo, **kw: releases)

    def _del(token, repo, rid, tag, **kw):
        deleted_ids.append(rid)

    monkeypatch.setattr(bsg, "delete_release", _del)

    pruned = bsg.prune_releases("tok", "owner/repo", keep=30)
    assert pruned == 5
    # Oldest 5 (ids 1..5) should be deleted.
    assert set(deleted_ids) == {1, 2, 3, 4, 5}


def test_prune_releases_noop_when_within_limit(monkeypatch) -> None:
    releases = _make_releases(10)
    monkeypatch.setattr(bsg, "list_releases",
                        lambda token, repo, **kw: releases)
    monkeypatch.setattr(bsg, "delete_release", lambda *a, **kw: None)
    pruned = bsg.prune_releases("tok", "owner/repo", keep=30)
    assert pruned == 0


def test_delete_release_reports_stale_404_without_hiding_other_errors(
    monkeypatch,
) -> None:
    def _missing(*args, **kwargs):
        raise bsg.GitHubAPIError(404, "missing")

    monkeypatch.setattr(bsg, "_api", _missing)
    assert bsg.delete_release("tok", "owner/repo", 7, "old-tag") is False

    def _forbidden(*args, **kwargs):
        raise bsg.GitHubAPIError(403, "forbidden")

    monkeypatch.setattr(bsg, "_api", _forbidden)
    with pytest.raises(bsg.GitHubAPIError, match="forbidden"):
        bsg.delete_release("tok", "owner/repo", 7, "old-tag")


# ── ship() end-to-end ─────────────────────────────────────────────────


def test_ship_dry_run_makes_no_api_calls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    tarball = _make_tarball(tmp_path)
    calls = []
    bsg.ship(tarball, dry_run=True, post_fn=lambda req: calls.append(req) or {})
    assert calls == []


def test_ship_calls_ensure_create_upload_prune(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    tarball = _make_tarball(tmp_path)

    steps = []

    def _ensure(token, repo, **kw):
        steps.append("ensure")

    def _create(token, repo, tag, **kw):
        steps.append("create")
        return {"id": 1, "upload_url": "https://uploads.github.com/u{?name,label}"}

    def _upload(token, url, name, path, **kw):
        steps.append("upload")
        return {"name": name, "browser_download_url": "https://example.com"}

    def _prune(token, repo, keep, **kw):
        steps.append("prune")
        return 0

    monkeypatch.setattr(bsg, "ensure_repo", _ensure)
    monkeypatch.setattr(bsg, "create_release", _create)
    monkeypatch.setattr(bsg, "_upload_asset", _upload)
    monkeypatch.setattr(bsg, "prune_releases", _prune)

    bsg.ship(tarball)
    assert steps == ["ensure", "create", "upload", "prune"]


def test_ship_exits_3_on_api_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    tarball = _make_tarball(tmp_path)

    def _ensure(token, repo, **kw):
        raise RuntimeError("500: server error")

    monkeypatch.setattr(bsg, "ensure_repo", _ensure)

    with pytest.raises(SystemExit) as exc:
        bsg.ship(tarball)
    assert exc.value.code == 3


# ── Tag derivation from filename ──────────────────────────────────────


def test_tag_derived_from_tarball_stem(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    tarball = _make_tarball(tmp_path, "tinyassets-data-2026-04-20T02-00-00Z.tar.gz")
    tags_seen = []

    def _ensure(token, repo, **kw):
        pass

    def _create(token, repo, tag, **kw):
        tags_seen.append(tag)
        return {"id": 1, "upload_url": "https://uploads.github.com/u{?name,label}"}

    monkeypatch.setattr(bsg, "ensure_repo", _ensure)
    monkeypatch.setattr(bsg, "create_release", _create)
    monkeypatch.setattr(bsg, "_upload_asset",
                        lambda *a, **kw: {"name": "x"})
    monkeypatch.setattr(bsg, "prune_releases", lambda *a, **kw: 0)

    bsg.ship(tarball)
    assert tags_seen == ["tinyassets-data-2026-04-20T02-00-00Z"]


# ── backup.sh integration ─────────────────────────────────────────────








# ── Runbook ───────────────────────────────────────────────────────────


def test_runbook_mentions_gh_restore() -> None:
    runbook = (
        Path(__file__).parent.parent
        / "docs" / "ops" / "backup-restore-runbook.md"
    )
    if not runbook.exists():
        pytest.skip("backup-restore-runbook.md not yet created")
    text = runbook.read_text(encoding="utf-8")
    assert "github" in text.lower() or "gh release" in text.lower()


# ── _api empty-body handling (204 DELETE) ─────────────────────────────


def test_api_tolerates_empty_body_204(monkeypatch) -> None:
    """DELETE release (retention prune) returns 204 with an empty body.
    json.loads("") raised and failed every nightly ship once the repo
    crossed BACKUP_GH_RETAIN releases — live-hit 2026-07-15 on the first
    post-rename ship (renamed repo carried 30+ pre-rename releases)."""
    import contextlib
    import io
    import urllib.request

    seen_timeout = None

    @contextlib.contextmanager
    def fake_urlopen(req, *, timeout):
        nonlocal seen_timeout
        seen_timeout = timeout
        yield io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = bsg._api("tok", "DELETE", "https://api.github.invalid/x")
    assert result == {}
    assert seen_timeout == bsg.GH_API_TIMEOUT_SECONDS


def test_api_timeout_uses_controlled_transport_error(monkeypatch) -> None:
    import urllib.request

    seen_timeout = None

    def _timeout(req, *, timeout):
        nonlocal seen_timeout
        seen_timeout = timeout
        raise TimeoutError

    monkeypatch.setattr(urllib.request, "urlopen", _timeout)
    with pytest.raises(RuntimeError, match="transport error: TimeoutError"):
        bsg._api("tok", "GET", "https://api.github.invalid/x")
    assert seen_timeout == bsg.GH_API_TIMEOUT_SECONDS


def test_retention_reconcile_budget_is_bounded_to_two_minutes() -> None:
    assert bsg.PRUNE_RECONCILE_BUDGET_SECONDS <= 120


def test_stale_victim_reconciliation_stops_at_wall_clock_budget(
    monkeypatch,
) -> None:
    now = [0.0]
    current = {
        "id": 31,
        "tag_name": "tinyassets-brain-2026-07-31T03-00-00Z",
        "published_at": "2026-07-31T03:00:10Z",
    }
    listed = [
        {
            "id": index,
            "tag_name": f"tinyassets-data-2026-07-{index:02d}T03-00-00Z",
            "published_at": f"2026-07-{index:02d}T03:00:10Z",
        }
        for index in range(1, 31)
    ] + [current]

    def _list(*args, timeout_seconds, **kwargs):
        now[0] += timeout_seconds
        return list(listed)

    def _delete(*args, deadline, monotonic_fn, **kwargs):
        now[0] += min(
            bsg.GH_API_TIMEOUT_SECONDS,
            deadline - monotonic_fn(),
        )
        return False

    def _sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(bsg, "list_releases", _list)
    monkeypatch.setattr(bsg, "delete_release", _delete)
    with pytest.raises(RuntimeError, match="budget"):
        bsg.prune_releases(
            "tok",
            "o/r",
            keep=30,
            include_release=current,
            sleep_fn=_sleep,
            monotonic_fn=lambda: now[0],
        )
    assert now[0] <= bsg.PRUNE_RECONCILE_BUDGET_SECONDS


# ── prune scoping (non-backup releases are permanent) ─────────────────


def test_prune_never_touches_non_backup_releases(monkeypatch) -> None:
    """Retention prune must only consider backup-prefixed tags; a parked
    one-off artifact (e.g. the 2026-07-15 volume-audit content archive)
    is permanent even when it is the oldest release in the repo."""
    releases = [
        {"id": 1, "tag_name": "volume-content-archive-workflow-data-2026-07-15",
         "created_at": "2026-07-15T01:00:00Z"},
        {"id": 2, "tag_name": "tinyassets-brain-2026-07-16T03-00-00Z",
         "created_at": "2026-07-16T03:00:00Z"},
        {"id": 3, "tag_name": "tinyassets-data-2026-07-17T03-00-00Z",
         "created_at": "2026-07-17T03:00:00Z"},
        {"id": 4, "tag_name": "workflow-brain-2026-06-01T03-00-00Z",
         "created_at": "2026-06-01T03:00:00Z"},
    ]
    deleted: list[str] = []
    monkeypatch.setattr(bsg, "list_releases", lambda *a, **kw: list(releases))
    monkeypatch.setattr(
        bsg, "delete_release",
        lambda tok, repo, rid, tag, **kw: deleted.append(tag),
    )
    pruned = bsg.prune_releases("tok", "o/r", keep=1)
    # Three prunable backup releases, keep newest 1 → two deleted; the
    # archive (id 1, oldest of all) is never a victim.
    assert pruned == 2
    assert sorted(deleted) == [
        "tinyassets-brain-2026-07-16T03-00-00Z",
        "workflow-brain-2026-06-01T03-00-00Z",
    ]


# ── retention ordering (created_at is the COMMIT date on GitHub) ──────


def test_prune_orders_by_published_at_when_created_at_ties(monkeypatch) -> None:
    """Live shape (Codex review 2026-07-15): all releases in the real
    backup repo share one created_at (GitHub uses the target commit's
    date), so ordering must come from published_at — the old sort
    deleted whichever release the list order put first."""
    same = "2026-04-21T06:26:57Z"
    releases = [
        # Listed newest-first (GitHub's API order) to expose the bug.
        {"id": 2, "tag_name": "tinyassets-brain-2026-07-15T03-00-00Z",
         "created_at": same, "published_at": "2026-07-15T03:00:10Z"},
        {"id": 1, "tag_name": "tinyassets-brain-2026-06-01T03-00-00Z",
         "created_at": same, "published_at": "2026-06-01T03:00:10Z"},
    ]
    deleted: list[int] = []
    monkeypatch.setattr(bsg, "list_releases", lambda *a, **kw: list(releases))
    monkeypatch.setattr(
        bsg, "delete_release", lambda tok, repo, rid, tag, **kw: deleted.append(rid),
    )
    assert bsg.prune_releases("tok", "o/r", keep=1) == 1
    assert deleted == [1]  # the genuinely older one, not list-order-first


def test_prune_falls_back_to_tag_timestamp_without_published_at(monkeypatch) -> None:
    same = "2026-04-21T06:26:57Z"
    releases = [
        {"id": 2, "tag_name": "tinyassets-data-2026-07-15T01-22-44Z",
         "created_at": same},
        {"id": 1, "tag_name": "workflow-data-2026-06-10T17-22-30Z",
         "created_at": same},
    ]
    deleted: list[int] = []
    monkeypatch.setattr(bsg, "list_releases", lambda *a, **kw: list(releases))
    monkeypatch.setattr(
        bsg, "delete_release", lambda tok, repo, rid, tag, **kw: deleted.append(rid),
    )
    assert bsg.prune_releases("tok", "o/r", keep=1) == 1
    assert deleted == [1]


def test_prune_counts_just_created_release_before_list_converges(
    monkeypatch,
) -> None:
    """A successful create/upload can precede list-endpoint visibility.

    Retention must count the release returned by create_release even when
    list_releases still returns only the prior keep-sized set.
    """
    releases = [
        {
            "id": index,
            "tag_name": f"tinyassets-data-2026-07-{index:02d}T03-00-00Z",
            "published_at": f"2026-07-{index:02d}T03:00:10Z",
        }
        for index in range(1, 31)
    ]
    just_created = {
        "id": 31,
        "tag_name": "tinyassets-brain-2026-07-31T03-00-00Z",
        "published_at": "2026-07-31T03:00:10Z",
    }
    listed = iter((list(releases), [*releases, just_created]))
    list_calls = 0

    def _list(*args, **kwargs):
        nonlocal list_calls
        list_calls += 1
        return next(listed)

    deleted: list[int] = []
    monkeypatch.setattr(bsg, "list_releases", _list)
    monkeypatch.setattr(
        bsg, "delete_release", lambda tok, repo, rid, tag, **kw: deleted.append(rid),
    )

    assert bsg.prune_releases(
        "tok",
        "o/r",
        keep=30,
        include_release=just_created,
        sleep_fn=lambda _: None,
    ) == 1
    assert list_calls == 2
    assert deleted == [1]


def test_prune_does_not_double_count_converged_created_release(
    monkeypatch,
) -> None:
    release = {
        "id": 31,
        "tag_name": "tinyassets-brain-2026-07-31T03-00-00Z",
        "published_at": "2026-07-31T03:00:10Z",
    }
    monkeypatch.setattr(bsg, "list_releases", lambda *a, **kw: [release])
    deleted: list[int] = []
    monkeypatch.setattr(
        bsg, "delete_release", lambda tok, repo, rid, tag, **kw: deleted.append(rid),
    )

    assert bsg.prune_releases(
        "tok",
        "o/r",
        keep=1,
        include_release=dict(release),
    ) == 0
    assert deleted == []


def test_prune_fails_when_created_release_never_becomes_list_visible(
    monkeypatch,
) -> None:
    listed = [{
        "id": 1,
        "tag_name": "tinyassets-data-2026-07-01T03-00-00Z",
        "published_at": "2026-07-01T03:00:10Z",
    }]
    just_created = {
        "id": 2,
        "tag_name": "tinyassets-brain-2026-07-31T03-00-00Z",
        "published_at": "2026-07-31T03:00:10Z",
    }
    list_calls = 0

    def _list(*args, **kwargs):
        nonlocal list_calls
        list_calls += 1
        return list(listed)

    monkeypatch.setattr(bsg, "list_releases", _list)
    with pytest.raises(RuntimeError, match="did not converge"):
        bsg.prune_releases(
            "tok",
            "o/r",
            keep=1,
            include_release=just_created,
            sleep_fn=lambda _: None,
        )
    assert list_calls == bsg.PRUNE_RECONCILE_ATTEMPTS


def test_sequential_upload_pruning_skips_stale_already_deleted_victim(
    monkeypatch,
) -> None:
    initial_listing = [
        {
            "id": index,
            "tag_name": f"tinyassets-data-2026-07-{index:02d}T03-00-00Z",
            "published_at": f"2026-07-{index:02d}T03:00:10Z",
        }
        for index in range(1, 31)
    ]
    full_release = {
        "id": 31,
        "tag_name": "tinyassets-data-2026-07-31T03-00-00Z",
        "published_at": "2026-07-31T03:00:10Z",
    }
    brain_release = {
        "id": 32,
        "tag_name": "tinyassets-brain-2026-07-31T03-00-00Z",
        "published_at": "2026-07-31T03:00:20Z",
    }
    delete_attempts: list[int] = []
    deleted: set[int] = set()
    listings = iter(
        (
            initial_listing,
            [*initial_listing, full_release],
            [*initial_listing, full_release, brain_release],
            [*initial_listing[1:], full_release, brain_release],
        )
    )

    def _delete(token, repo, release_id, tag, **kwargs):
        delete_attempts.append(release_id)
        if release_id in deleted:
            return False
        deleted.add(release_id)
        return True

    monkeypatch.setattr(
        bsg,
        "list_releases",
        lambda *args, **kwargs: list(next(listings)),
    )
    monkeypatch.setattr(bsg, "delete_release", _delete)

    assert bsg.prune_releases(
        "tok",
        "o/r",
        keep=30,
        include_release=full_release,
        sleep_fn=lambda _: None,
    ) == 1
    assert bsg.prune_releases(
        "tok",
        "o/r",
        keep=30,
        include_release=brain_release,
        sleep_fn=lambda _: None,
    ) == 1
    assert delete_attempts == [1, 1, 2]
    assert deleted == {1, 2}
