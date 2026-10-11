"""A FastMCP server with one tool that runs far longer than any drain.

Stands in for the production daemon on 2026-10-01: a long codex turn was
running in a FastMCP tool (an AnyIO worker thread) when the deploy SIGTERMed
the container. See run.sh for how it is driven.
"""

import os
import time

import uvicorn
from fastmcp import FastMCP

m = FastMCP("deploy-drain-repro")


@m.tool()
def converse() -> str:
    time.sleep(600)  # the long turn: uncancellable worker thread
    return "TURN_FINISHED"


app = m.http_app(path="/mcp", transport="streamable-http")

if __name__ == "__main__":
    uvicorn.run(
        app, host="0.0.0.0", port=8001,
        timeout_graceful_shutdown=float(os.environ["GRACEFUL_SHUTDOWN_S"]),
    )
