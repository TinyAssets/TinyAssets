"""One owner-bound outbound socket; admitted connector streams reach the broker.

One event loop serves every relay (P0-8): an open stream costs a coroutine and
two descriptors, not a thread. Streams are bounded per owner, so one busy owner
cannot take the fleet's, and in total.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.admission import OwnerRegistry
from tinyassets.boxes.bootstrap import peer
from tinyassets.boxes.provider import BoxAuthError

# The 10,000-user model peaks near 330 model streams; each holds one relay
# stream for its call. The fleet bound keeps descriptors under boxhostd's limit.
OWNER_STREAMS = 128
FLEET_STREAMS = 2048
_IDLE = 30


class OwnerRelays:
    def __init__(self, directory: Path, admission: OwnerRegistry) -> None:
        self.directory, self.admission = directory, admission
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._bindings = {}
        self._servers = {}
        self._open = {}  # Touched only on the loop thread.
        # Serializes bind/retire/close. The loop thread never takes it, so a
        # caller may wait on the loop while holding it.
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def bind(self, owner: str, uid: int) -> Path:
        with self._lock:
            if uid in self._bindings:
                if self._bindings[uid] != owner:
                    raise BoxAuthError("relay UID is already bound to another owner")
                return self.directory / f"{uid}.sock"
            path = self.directory / f"{uid}.sock"
            path.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            os.chown(path, uid, uid)
            os.chmod(path, 0o600)
            listener.listen(OWNER_STREAMS)  # A burst up to the owner's bound queues.
            started = asyncio.run_coroutine_threadsafe(asyncio.start_unix_server(
                lambda reader, writer: self._serve(reader, writer, owner, uid),
                sock=listener, backlog=OWNER_STREAMS), self._loop)
            try:
                server = started.result(5)
            except BaseException:
                started.cancel()
                # The loop owns the listener from here: it closes it, never this thread.
                asyncio.run_coroutine_threadsafe(self._abandon(started, listener), self._loop)
                path.unlink(missing_ok=True)
                raise
            self._bindings[uid] = owner
            self._servers[uid] = server
            return path

    def _admit(self, uid):
        if (sum(self._open.values()) >= FLEET_STREAMS
                or self._open.get(uid, 0) >= OWNER_STREAMS):
            return False
        self._open[uid] = self._open.get(uid, 0) + 1
        return True

    def _release(self, uid):
        self._open[uid] -= 1
        if not self._open[uid]:
            del self._open[uid]

    async def _serve(self, guest_reader, guest, owner, uid):
        if not self._admit(uid):
            guest.close()
            return
        broker = None
        try:
            # The socket itself is unique and mounted only into this owner box.
            # runsc may proxy AF_UNIX peers as root; no identity is accepted from
            # that peer or request. Owner identity was fixed by bind(), above.
            first = await asyncio.wait_for(rf.read_frame(guest_reader), _IDLE)
            if first is None or first.kind != rf.CONTROL or first.stream != rf.CONNECTION:
                raise BoxAuthError("relay requires a bounded operation")
            doc = first.control()
            if doc == {"op": "WHOAMI"}:
                guest.write(rf.control(rf.CONNECTION, {
                    "op": "OWNER_IS", "principal": owner, "uid": uid,
                }))
                await guest.drain()
                return
            if set(doc) == {"op", "request"} and doc["op"] == "ROUTER_CREDENTIAL":
                from tinyassets.router_credentials import relay_request

                # Fixed endpoint binding; C13 post-write admission orders mints.
                bound_owner = self._bindings.get(uid)
                if bound_owner is None:
                    raise BoxAuthError("relay endpoint is retired")
                answer = await asyncio.to_thread(
                    relay_request, self.admission.data_root, bound_owner, doc["request"])
                guest.write(rf.control(rf.CONNECTION, answer))
                await guest.drain()
                return
            owner_public = doc == {"op": "BOX_PUBLIC"}
            if not owner_public and doc != {"op": "BOX_CONNECT"}:
                raise BoxAuthError("invalid relay operation")
            if self.admission is None:
                raise BoxAuthError("relay broker admission unavailable")
            broker_reader, broker = await asyncio.wait_for(asyncio.open_unix_connection(
                str(self.admission.broker_socket)), _IDLE)
            if peer(broker.get_extra_info("socket"))[1:] != (1002, 1002):
                raise BoxAuthError("relay peer is not the broker")
            request = {"op": "BOX_PUBLIC" if owner_public else "BOX_CHANNEL",
                       "principal": owner}
            broker.write(rf.control(rf.CONNECTION, request))
            await broker.drain()
            answer = await asyncio.wait_for(rf.read_frame(broker_reader), _IDLE)
            if answer is None or answer.control().get("op") != "BOX_ADMITTED":
                raise BoxAuthError("broker refused box channel")
            guest.write(rf.control(rf.CONNECTION, {"op": "BOX_CONNECTED"}))
            await guest.drain()
            # Fixed-size forwarding prevents a peer's input from growing an
            # unbounded queue. The broker owns framing, credit and policy.
            # The stream ends after _IDLE seconds with no bytes either way.
            last = [self._loop.time()]
            pipes = [asyncio.ensure_future(self._pipe(guest_reader, broker, last)),
                     asyncio.ensure_future(self._pipe(broker_reader, guest, last))]
            try:
                done, _ = await asyncio.wait(pipes, return_when=asyncio.FIRST_EXCEPTION)
            finally:
                for pipe in pipes:
                    pipe.cancel()
                await asyncio.gather(*pipes, return_exceptions=True)
            for pipe in done:
                pipe.result()
        except (OSError, ValueError, LookupError, EOFError, rf.FrameError):
            pass  # no identity or credential details in guest errors
        finally:
            for writer in (guest, broker):
                if writer is not None:
                    writer.close()
            self._release(uid)

    async def _pipe(self, reader, writer, last):
        while True:
            remaining = _IDLE - (self._loop.time() - last[0])
            if remaining <= 0:
                raise TimeoutError("relay stream idle")
            try:
                data = await asyncio.wait_for(reader.read(65536), remaining)
            except TimeoutError:
                continue  # Either direction's last byte restarts the idle end.
            if not data:
                if writer.can_write_eof():
                    writer.write_eof()
                return
            last[0] = self._loop.time()
            writer.write(data)
            # A peer that stops reading cannot hold the stream past the idle end.
            await asyncio.wait_for(writer.drain(), _IDLE)

    async def _abandon(self, started, listener):
        try:
            server = await asyncio.wrap_future(started)
        except BaseException:
            listener.close()  # The server never started.
        else:
            server.close()

    async def _stop(self, servers):
        # Only the loop closes its listeners, so its selector never keeps a
        # closed descriptor that the next bind would reuse.
        for server in servers:
            server.close()

    async def _shutdown(self, servers):
        await self._stop(servers)
        streams = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for stream in streams:
            stream.cancel()
        await asyncio.gather(*streams, return_exceptions=True)

    def close(self):
        with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
            self._bindings.clear()
            # Streams end on the loop, each releasing its slot, before it stops.
            asyncio.run_coroutine_threadsafe(self._shutdown(servers), self._loop).result(35)
            self._loop.call_soon_threadsafe(self._loop.stop)

    def retire(self, uid):
        with self._lock:
            server = self._servers.pop(uid, None)
            self._bindings.pop(uid, None)
            if server is not None:
                asyncio.run_coroutine_threadsafe(self._stop([server]), self._loop).result(5)
            (self.directory / f"{uid}.sock").unlink(missing_ok=True)
