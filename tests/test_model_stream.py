"""Async broker transport emits previews early and cancels without replay."""
import asyncio
import json
import threading
from contextlib import asynccontextmanager

import pytest

from tinyassets import rpc_frames as rf
from tinyassets.agent_loop.model_stream import (
    TextDeltas,
    collect_stream,
    observe_model_stream,
    shared_broker_client,
)
from tinyassets.broker.aclient import AsyncBrokerClient


def _chunk(text):
    return ('data: ' + json.dumps({'choices': [{'delta': {'content': text}}]},
                                 ensure_ascii=False) + '\n\n').encode()


def test_preview_exposes_only_content_and_preserves_split_unicode():
    parser = TextDeltas()
    raw = _chunk("hi 🌍")
    position = raw.index("🌍".encode()) + 1
    assert list(parser.feed(raw[:position])) == []
    assert list(parser.feed(raw[position:])) == ["hi 🌍"]
    assert list(parser.feed(b'data: {"choices":[{"delta":{"reasoning":"private",'
                            b'"tool_calls":[{"arguments":"private"}]}}]}\n\n')) == []


def test_stream_previews_before_terminal_and_preserves_usage_reference(tmp_path):
    async def scenario():
        release, got_preview = asyncio.Event(), asyncio.Event()
        requests, events = [], []
        path = tmp_path / 'broker.sock'

        async def peer(reader, writer):
            try:
                opened = await rf.read_frame(reader)
                requests.append(opened.control())
                writer.write(rf.control(opened.stream, {'op': 'HEAD', 'status': 200}))
                writer.writelines(rf.data(opened.stream, _chunk('early')))
                await writer.drain()
                await release.wait()
                writer.writelines(rf.data(opened.stream, b'data: [DONE]\n\n'))
                writer.write(rf.control(opened.stream, {'op': 'END', 'outcome': 'completed'}))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(peer, path)
        client = AsyncBrokerClient(path, principal='owner', command_center='cc',
                                   fence=lambda: (1, 'test-owner-token'))

        class Usage:
            operation_id = 'usage-operation'

            def document(self):
                return {'reference': 'opaque-reference', 'usage_id': 'usage'}

        class Proxy:
            @asynccontextmanager
            async def stream(self, verb, request, *, inference_usage, operation_id):
                async with client.stream(grant_id='grant', connection_id='connection',
                                         verb=verb, request=request, op_id=operation_id,
                                         inference_usage=inference_usage.document()) as stream:
                    yield stream

        def observer(event):
            events.append(event)
            if event['type'] == 'model_text_delta':
                got_preview.set()

        try:
            with observe_model_stream(observer):
                task = asyncio.create_task(collect_stream(Proxy(), {}, usage=Usage()))
                await asyncio.wait_for(got_preview.wait(), 3)
                assert not task.done()
                assert events[-1]['text'] == 'early'
                release.set()
                result = await asyncio.wait_for(task, 3)
            assert 'early' in result['body']
            assert requests[0]['inference_usage'] == Usage().document()
            assert requests[0]['op_id'] == Usage.operation_id
            assert events[-1] == {'type': 'model_end', 'operation_id': Usage.operation_id,
                                  'status': 'completed'}
        finally:
            release.set()
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_cancellation_reaches_broker_and_never_reopens(tmp_path):
    async def scenario():
        opened, cancelled = asyncio.Event(), asyncio.Event()
        operations = []
        path = tmp_path / 'broker.sock'

        async def peer(reader, writer):
            try:
                while (frame := await rf.read_frame(reader)) is not None:
                    document = frame.control()
                    if document['op'] == 'OPEN':
                        operations.append(document['op_id'])
                        opened.set()
                    elif document['op'] == 'CANCEL':
                        cancelled.set()
                        writer.write(rf.control(frame.stream, {'op': 'END',
                                                              'outcome': 'cancelled'}))
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(peer, path)
        client = AsyncBrokerClient(path, principal='owner', command_center='cc',
                                   fence=lambda: (1, 'test-owner-token'))

        async def wait():
            async with client.stream(grant_id='g', connection_id='c', verb='POST',
                                     request={}, op_id='one-operation') as stream:
                await stream.head()

        task = asyncio.create_task(wait())
        try:
            await asyncio.wait_for(opened.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), 3)
            assert operations == ['one-operation']
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_500_waiting_streams_share_one_socket_without_worker_threads(tmp_path):
    async def scenario():
        path = tmp_path / 'broker.sock'
        ready, release = asyncio.Event(), asyncio.Event()
        opened, connections = [], []
        before = {thread.ident for thread in threading.enumerate()}

        async def peer(reader, writer):
            connections.append(writer)
            try:
                while len(opened) < 500:
                    frame = await rf.read_frame(reader)
                    if frame.control()['op'] == 'OPEN':
                        opened.append(frame.stream)
                ready.set()
                await release.wait()
                for stream_id in opened:
                    writer.write(rf.control(stream_id, {'op': 'HEAD', 'status': 200}))
                    writer.write(rf.control(stream_id, {'op': 'END', 'outcome': 'completed'}))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(peer, path)
        def fence():
            return 1, 'test-owner-token'

        async def request(index):
            async with shared_broker_client(path, principal='owner', command_center='cc',
                                            fence=fence, verify_peer=None,
                                            refresh_factory=None) as client:
                async with client.stream(grant_id='g', connection_id='c', verb='POST',
                                         request={}, op_id=str(index)) as stream:
                    await stream.head()
                    async for _ in stream.body():
                        pass

        tasks = [asyncio.create_task(request(index)) for index in range(500)]
        try:
            await asyncio.wait_for(ready.wait(), 5)
            assert len(connections) == 1
            assert {thread.ident for thread in threading.enumerate()} == before
            assert all(not task.done() for task in tasks)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
