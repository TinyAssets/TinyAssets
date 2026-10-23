"""Bounded public HTTP/CONNECT on a host-authenticated owner channel."""

import asyncio
import contextlib
import sqlite3

from tinyassets import rpc_frames as rf
from tinyassets.boxes.network import EgressRefused, _destination
from tinyassets.broker.admission import owner_lock
from tinyassets.broker.box_admission import resolve_owner
from tinyassets.broker.egress import EgressPolicyRefused, destination
from tinyassets.storage.outbound_connections import SsrfValidationError


class PublicEgress:
    def __init__(self, identities, data_root=None, *, binding=None):
        self.binding = binding
        self.identities = identities
        self.data_root = data_root
        self.active = {}
        self.channels = {}

    async def serve(self, reader, writer, document):
        # Called only for BOX_HOST, before any guest bytes are interpreted as
        # raw HTTP. Owner identity originates in the root's mounted relay.
        owner = document.get("principal")
        admitted = False
        upstream = None
        pumps = []
        task = asyncio.current_task()
        self.channels[task] = (owner, writer)
        try:
            async with owner_lock(self.identities.path, owner):
                await asyncio.to_thread(resolve_owner, self.identities, document,
                                        data_root=self.data_root)
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
            async with owner_lock(self.identities.path, owner):
                addresses = await asyncio.wait_for(
                    asyncio.to_thread(destination, self.binding(owner), host, port), 10)
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
        except EgressPolicyRefused as exc:
            import json

            detail = json.dumps(exc.detail, separators=(",", ":")).encode("ascii")
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n"
                         b"X-TinyAssets-Egress: " + detail + b"\r\nContent-Length: 0\r\n\r\n")
            await asyncio.wait_for(writer.drain(), 1)
        except (EgressRefused, SsrfValidationError, ValueError, PermissionError,
                LookupError, OSError, sqlite3.Error,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            with contextlib.suppress(OSError, TimeoutError):
                writer.write((b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n"
                              b"Content-Length: 0\r\n\r\n") if admitted else
                             rf.control(rf.CONNECTION, {"op": "BOX_REFUSED"}))
                await asyncio.wait_for(writer.drain(), 1)
        finally:
            self.channels.pop(task, None)
            for pump in pumps:
                pump.cancel()
            if upstream is not None:
                if task.cancelling():
                    upstream.transport.abort()
                else:
                    upstream.close()
            if admitted:
                self.active[owner] -= 1
                if not self.active[owner]:
                    del self.active[owner]
            writer.close()
            await asyncio.gather(*pumps, return_exceptions=True)

    def close_owner(self, owner):
        for task, (principal, writer) in tuple(self.channels.items()):
            if principal == owner:
                writer.transport.abort()
                task.cancel()

    @staticmethod
    async def _pump(reader, writer):
        while data := await asyncio.wait_for(reader.read(65536), 300):
            writer.write(data)
            await asyncio.wait_for(writer.drain(), 300)
        if writer.can_write_eof():
            writer.write_eof()
