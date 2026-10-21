"""Real publication consent establishes lineage only after the owner answers."""

import pytest

from tests.cloud_runtime_fixture import cloud_runtime as cloud_runtime
from tests.test_command_center_packages import (
    _publish_action,
)
from tests.test_command_center_packages import _pin_data_dir as _pin_data_dir
from tests.test_command_center_packages import home as home

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def action(**release):
    value = _publish_action()
    del value["package"]
    return {**value, "release": {"summary": "Initial release", **release}}














