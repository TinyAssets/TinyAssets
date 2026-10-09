"""Scripted agent tasks through real ta discovery, schemas and engine dispatch."""
import asyncio
import json
from unittest.mock import patch

from tinyassets import ta_cli
from tinyassets.ta_capabilities import engine_dispatch


def call(engine, tool_name, **arguments):
    async def run():
        dispatch = await engine_dispatch(engine)
        assert dispatch is not None, "the signed launch must retain bash and backend grants"

        def task():
            with patch.object(ta_cli, "remote", dispatch), patch.object(
                ta_cli, "extensions", lambda _roots: {},
            ):
                found = ta_cli.main(["search", tool_name])
                assert any(item["name"] == tool_name for item in found), found
                described = ta_cli.main(["describe", tool_name])
                assert described["arguments"]["type"] == "object"
                return ta_cli.main(["call", tool_name, "--json", json.dumps(arguments)])

        return await asyncio.to_thread(task)

    return asyncio.run(run())
