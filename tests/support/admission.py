import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tinyassets.broker.owner_reads import Plans as BrokerPlans

_routes = {}


def bind(path, routing):
    _routes[Path(path)] = routing


class Projection:
    def __init__(self, path):
        self.path = Path(path)

    @property
    def server(self):
        return getattr(_routes.get(self.path), "server", None)

    async def changed(self, owner):
        routing = _routes.get(self.path)
        if routing is not None:
            loop = getattr(routing.server, "_loop", None)
            if loop is not None and loop.is_running() and loop is not asyncio.get_running_loop():
                return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
                    routing.changed(owner), loop))
            return await routing.changed(owner)


def projection(path):
    return Projection(path)


class Plans(BrokerPlans):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.routing = projection(self.admission_path)


def suspend(plans, *args, **kwargs):
    plans.routing = projection(plans.admission_path)
    return drive(plans.set_suspension(*args, **kwargs))


def drive(coroutine):
    """Drive the real async API on its fixture broker's loop."""
    frame = coroutine.cr_frame.f_locals
    server = (frame.get('server') or getattr(frame.get('self'), 'server', None)
              or getattr(frame.get('routing'), 'server', None)
              or getattr(getattr(frame.get('self'), 'lifecycle', None), 'server', None))
    loop = getattr(server, '_loop', None)
    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(60)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result(60)


def complete_deletion(jobs, owner):
    """Existing full-job tests explicitly wait for the post-enqueue task."""
    async def finish():
        await jobs.enqueue(owner)
        await asyncio.gather(*jobs.server.routing.tasks.values())
        return jobs.public(jobs.records.load(__import__(
            'tinyassets.broker.admission', fromlist=['digest']).digest(owner)), include_url=True)
    coroutine = finish()
    loop = getattr(jobs.server, '_loop', None)
    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(60)
    return drive(coroutine)


def async_call(function):
    if inspect.iscoroutinefunction(function):
        return function
    async def call(*args, **kwargs):
        return await asyncio.to_thread(function, *args, **kwargs)
    return call


def retire_local(root, center):
    """Exercise local center membership independently of the shared owner map."""
    from tinyassets.owner_centers import Centers

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    centers = Centers(root)
    centers.create(center)
    centers.retire(center)
