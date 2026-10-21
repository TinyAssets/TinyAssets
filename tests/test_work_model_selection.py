"""Real foreground admission and HTTP execution with synthetic network only."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver
from tests.test_discovery_snapshot import _model
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.provider_assignment import provider_assignment_admission
from tinyassets.providers import discovery_snapshot
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.storage.provider_work_authority import db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def http_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    reads, writes, errors = [], [], []
    original_call = _ForegroundRunProviderSession._call

    def call(*args, **kwargs):
        try:
            return original_call(*args, **kwargs)
        except Exception as exc:
            import traceback

            errors.append("".join(traceback.format_exception(exc)))
            raise

    monkeypatch.setattr(_ForegroundRunProviderSession, "_call", call)
    def check_unlocked():
        with provider_assignment_admission().exclusive(tmp_path / "universe_alice"):
            with sqlite3.connect(db_path(tmp_path), timeout=0.1) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()

    def read(**kwargs):
        # A second thread can enter both fences while remote discovery runs.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(check_unlocked).result(timeout=2)
        reads.append(kwargs)
        return {"data": [_model("synthetic-model"), _model("future-company/new-choice")]}

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            writes.append((verb, document))
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "model": "observed-answer-model",
                        "choices": [{"message": {"content": "selected workflow answer"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": 0},
                    }
                ),
            }

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", read)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: Proxy()))
    return reads, writes, errors
