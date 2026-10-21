"""Resident orientation and greeting continuation after prod turn bdec018e."""

from dataclasses import replace

from tests import test_interactive_http_agent as http

rig = http.rig
reader = http.reader
served = http.served
agent = http.agent
def run(agent, *args, free_requests=None, **kwargs):
    """Explicit owner allocation for long-work tests; ordinary defaults stay real."""
    if free_requests is None:
        return http.run(agent, *args, **kwargs)
    from tinyassets.providers.model_call import open_allocation

    budget = open_allocation(
        "owner", agent.served.context.universe_dir,
        free_limit=free_requests, free_pool_limit=free_requests,
    )
    original = agent.config
    agent.config = replace(original, request_budget=budget)
    try:
        return http.run(agent, *args, **kwargs)
    finally:
        agent.config = original
        budget.close()


HEADING = "## What is in my folder now"


def seed(root):
    (root / "workflows/x").mkdir(parents=True)
    (root / "workflows/x/index.html").write_bytes(b"x" * 1024)
    (root / "notes").mkdir(exist_ok=True)
    (root / "notes/a.md").write_bytes(b"a" * 2048)






















































