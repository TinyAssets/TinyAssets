"""Real attempt locks and conservative recovery; no public intake proof."""


import pytest

from tests.test_delivery_reservations import (
    _accept,
    _read,
    delivery_env,  # noqa: F401
)
from tests.test_receiver_links import env as management_env  # noqa: F401
from tinyassets.storage import deliveries, delivery_lock


@pytest.fixture
def reserved(delivery_env):  # noqa: F811
    base, _, _, link = delivery_env
    with deliveries.transaction(base) as conn:
        receipt = _accept(conn, link)
        private = _read(conn, receipt, principal="receiver", universe="u-receiver")
    return base, receipt, private["run_id"]


def _lock(base, receipt):
    return delivery_lock.try_attempt_lock(base, delivery_id=receipt["delivery_id"], attempt=1)






















