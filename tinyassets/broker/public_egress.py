"""Bounded public HTTP/CONNECT on a host-authenticated owner channel."""

import asyncio
import contextlib
import sqlite3

from tinyassets import rpc_frames as rf
from tinyassets.broker.box_admission import resolve
from tinyassets.universe_egress import EgressRefused, _checked_addresses, _destination


class PublicEgress:
    def __init__(self, identities):
        self.identities = identities
        self.active = {}

    async def serve(self, reader, writer, document):
        # Called only for BOX_HOST, before any guest bytes are interpreted as
        # raw HTTP. Owner identity originates in the root's mounted relay.
        owner = document.get("principal")
        admitted = False
        upstream = None
        pumps = []
        try:
            await asyncio.to_thread(resolve, self.identities,
                                    {**document, "op": "BOX_ADMISSION"})
            if sum(self.active.values()) >= 64 or self.active.get(owner, 0) >= 32:
                raise EgressRefused("owner or host egress capacity reached")
            self.active[owner] = self.active.get(owner, 0) + 1
            admitted = True
            writer.write(rf.control(rf.CONNECTION, {"op": "BOX_ADMITTED"}))
            await asyncio.wait_for(writer.drain(), 30)
            # readuntil retains body bytes, and limits are checked before DNS.
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            if len(head) > 16384:
                raise EgressRefused("proxy header exceeds bound")
            host, port, first = _destination(head)
            addresses = await asyncio.wait_for(
                asyncio.to_thread(_checked_addresses, host, port), 10)
            async with asyncio.timeout(15):
                for address in addresses:
                    try:
                        incoming, upstream = await asyncio.open_connection(address, port)
                        break
                    except OSError:
                        continue
                else:
                    raise EgressRefused("destination unavailable")
            if first is None:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await asyncio.wait_for(writer.drain(), 30)
            else:
                upstream.write(first)
                await asyncio.wait_for(upstream.drain(), 30)
            pumps = [asyncio.create_task(self._pump(reader, upstream)),
                     asyncio.create_task(self._pump(incoming, writer))]
            await asyncio.gather(*pumps)
        except (EgressRefused, ValueError, PermissionError, LookupError, OSError, sqlite3.Error,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            with contextlib.suppress(OSError, TimeoutError):
                writer.write((b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n"
                              b"Content-Length: 0\r\n\r\n") if admitted else
                             rf.control(rf.CONNECTION, {"op": "BOX_REFUSED"}))
                await asyncio.wait_for(writer.drain(), 1)
        finally:
            for task in pumps:
                task.cancel()
            if pumps:
                await asyncio.gather(*pumps, return_exceptions=True)
            if upstream is not None:
                upstream.close()
            if admitted:
                self.active[owner] -= 1
                if not self.active[owner]:
                    del self.active[owner]

    @staticmethod
    async def _pump(reader, writer):
        while data := await asyncio.wait_for(reader.read(65536), 300):
            writer.write(data)
            await asyncio.wait_for(writer.drain(), 300)
        if writer.can_write_eof():
            writer.write_eof()
