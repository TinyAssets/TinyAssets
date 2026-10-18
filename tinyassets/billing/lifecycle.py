"""Serialize the single shared door's billing work with deletion tombstones."""
from functools import wraps
from threading import RLock

# The shared door owns checkout, verified webhooks and router tombstones.
# Never hold the credential dispatch lock while making a billing request.
lock = RLock()


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with lock:
            return function(*args, **kwargs)
    return wrapped
