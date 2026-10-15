"""Legacy cell git launch forms over the shared route registry."""
from __future__ import annotations

import contextlib
import shlex

from tinyassets.boxes.git_routes import HOST, _open_routes, _routes, config_pairs, serve

__all__ = ["HOST", "_open_routes", "_routes", "config_pairs", "serve", "routes", "rewrite_options"]


@contextlib.contextmanager
def routes(proxy, client, connections, agent, *, authority=lambda: None):
    with _open_routes(proxy, client, connections, agent, authority=authority) as rewrites:
        env = {"GIT_CONFIG_COUNT": str(len(rewrites)), "GIT_TERMINAL_PROMPT": "0"}
        for index, (key, value) in enumerate(config_pairs(rewrites)):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        yield "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + "; "


@contextlib.contextmanager
def rewrite_options(proxy, client, connections, agent, *, authority=lambda: None):
    with _open_routes(proxy, client, connections, agent, authority=authority) as rewrites:
        options = []
        for key, value in config_pairs(rewrites):
            options.extend(("-c", f"{key}={value}"))
        yield options
