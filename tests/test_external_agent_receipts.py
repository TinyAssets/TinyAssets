"""Catalogue status comes from durable attempts and never creates state on read."""
from datetime import datetime, timedelta, timezone

from tinyassets.external_agents import receipts
from tinyassets.storage.provider_work_authority import db_path


def test_status_is_read_only_and_a_failed_attempt_has_a_reason(tmp_path):
    where = dict(owner="owner", universe_id="u-owner", agent="codex")
    assert receipts.status(tmp_path, **where) == "never_run"
    assert not db_path(tmp_path).exists()

    attempt = receipts.admit(tmp_path, **where, package_digest="package",
                             custody_digest="custody")
    assert receipts.status(tmp_path, **where) == "never_run"
    receipts.start(tmp_path, attempt)
    assert receipts.status(tmp_path, **where) == "refreshing"
    receipts.finish(tmp_path, attempt, outcome="failed", reason="offline")
    assert receipts.status(tmp_path, **where) == "failed: offline"

    receipts.admit(tmp_path, **where, package_digest="package",
                   custody_digest="custody")
    later = datetime.now(timezone.utc) + timedelta(seconds=receipts.DEADLINE_SECONDS + 1)
    assert receipts.status(tmp_path, **where, now=later) == "failed: not_started"
