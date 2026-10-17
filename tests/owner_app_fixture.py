"""Unit assembly of handlers retained inside the box, without a runtime process.

Only construction selects the owner route table. Request and lifespan behaviour
keep each test's existing doubles; real admission/secret coverage lives in the
owner daemon and router subprocess acceptance tests.
"""
from unittest.mock import patch

from tinyassets.universe_server import create_streamable_http_app as _create


def create_owner_app(**kwargs):
    with patch("tinyassets.owner_runtime.current", return_value=object()):
        return _create(**kwargs)
