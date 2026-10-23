"""The image smoke starts the daemon the way compose does."""

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/docker-build.yml"


def test_admitted_smoke_runs_unprivileged_as_the_daemon_user():
    text = _WORKFLOW.read_text(encoding="utf-8")
    start = text.index("Start isolated protocol fixture with simulated admission")
    step = text[start:text.index("MCP initialize smoke", start)]
    assert "--user 1001:1001" in step
    assert "--group-add 1100" in step
    assert "--group-add 1101" in step
    assert "--group-add 1102" in step
    assert "--cap-drop ALL" in step
    assert "--cap-add" not in step
    assert "--user 0:0" not in step.split("docker run -d", 1)[1]
    assert "chown 1001:1001 /data" in step
