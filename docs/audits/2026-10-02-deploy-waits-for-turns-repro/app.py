"""A FastMCP daemon whose one tool is a long turn holding a REAL account seat.

Stands in for production: `converse` takes an interactive seat through
`tinyassets.universe_seats.hold` -- the same call a chat turn makes -- in the
data volume, keeps it for TURN_S seconds, and returns TURN_FINISHED. See run.py.
"""

import os
import time
from pathlib import Path

import uvicorn
from fastmcp import FastMCP

from tinyassets import universe_seats as seats

DATA = Path(os.environ["TINYASSETS_DATA_DIR"])
m = FastMCP("deploy-waits-for-turns-repro")


@m.tool()
def converse() -> str:
    with seats.hold(
        "acct-founder", seat_class=seats.CLASS_INTERACTIVE, kind=seats.KIND_CHAT_TURN,
        universe_id="u-village", db=seats.ledger_path(DATA),
    ):
        time.sleep(float(os.environ["TURN_S"]))
    return f"TURN_FINISHED gen={os.environ['GEN']}"


@m.tool()
def gen() -> str:
    return os.environ["GEN"]


app = m.http_app(path="/mcp", transport="streamable-http")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, timeout_graceful_shutdown=10)
