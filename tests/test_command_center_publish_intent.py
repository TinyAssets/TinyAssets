"""Truthful publication intent and previews, through two synthetic owners."""

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    _publish_action,
    home,  # noqa: F401
)
from tinyassets.api.publish_requests import validate_action

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))




@pytest.mark.parametrize("changes", [
    {"publish_kind": "command_center", "package": None},
    {"publish_kind": "command_center", "ui_id": ""},
    {"publish_kind": "workflows"},
    {"publish_kind": "unknown"},
])
def test_explicit_intent_cannot_silently_choose_another_publication(changes):
    with pytest.raises(ValueError):
        validate_action({**_publish_action(), **changes})
