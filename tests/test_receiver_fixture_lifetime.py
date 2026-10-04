"""A receiver fixture cannot lend its unfinished worker the next test's root."""

import json
import os
import threading

import pytest

from tests.test_open_receivers import _connect, _create, _seed_attributed, _send
from tests.test_receiver_links import env
from tinyassets import runs
from tinyassets.storage import deliveries


def test_receiver_fixture_drains_delivery_before_restoring_its_provider_and_root(
    tmp_path, authenticate_request,
):
    entered = threading.Event()
    release = threading.Event()
    observed = []
    future = None
    fixture = None
    first_base = tmp_path / "first" / "data"

    def bind(provider_call, universe_id, *, principal_id):
        entered.set()
        assert release.wait(10), "fixture did not wait for its accepted delivery"
        observed.append((os.environ["TINYASSETS_DATA_DIR"], principal_id, universe_id))
        return lambda *args, **kwargs: json.dumps({"result": "done", "extra": "private"})

    try:
        with pytest.MonkeyPatch.context() as patch:
            fixture = env.__wrapped__(tmp_path / "first", patch, authenticate_request)
            base, auth = next(fixture)
            _seed_attributed(base)
            patch.setattr("tinyassets.api.runs._bind_run_provider_call", bind)
            auth("receiver", capabilities=[
                "tinyassets.extensions.read", "tinyassets.extensions.write",
                "tinyassets.extensions.costly",
            ])
            receiver = _create(open_to_all=True)
            auth("outsider", capabilities=[
                "tinyassets.extensions.read", "tinyassets.extensions.write",
                "tinyassets.extensions.costly",
            ])
            receipt = _send(_connect(receiver))
            assert "delivery_id" in receipt, receipt
            assert entered.wait(3), "accepted delivery did not start asynchronously"
            with deliveries.transaction(base) as conn:
                run_id = conn.execute(
                    "SELECT run_id FROM graph_delivery_attempts WHERE delivery_id=?",
                    (receipt["delivery_id"],),
                ).fetchone()[0]
            future = runs.get_future(run_id)
            assert future is not None and not future.done()
            result = future.result

            def observed_wait(*args, **kwargs):
                # Release only when fixture teardown actually joins this Future.
                # Removing that join leaves the worker pending at the assertion.
                observed.append("fixture_waited")
                release.set()
                return result(*args, **kwargs)

            patch.setattr(future, "result", observed_wait)
            fixture.close()
            assert future.done(), "fixture returned with a live delivery worker"
            assert observed == ["fixture_waited", (str(first_base), "receiver", "u-receiver")]
            assert runs.get_run(base, run_id)["status"] == runs.RUN_STATUS_COMPLETED

        with pytest.MonkeyPatch.context() as patch:
            following = env.__wrapped__(tmp_path / "following", patch, authenticate_request)
            try:
                next_base, _ = next(following)
                assert next_base != first_base
                assert future.done()
                assert observed[-1][0] == str(first_base)
            finally:
                following.close()
    finally:
        # A failing mutation must not leak the deliberately paused worker itself.
        release.set()
        if future is not None:
            future.result(timeout=10)
        if fixture is not None:
            fixture.close()
