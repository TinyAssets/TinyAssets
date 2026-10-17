"""Real owner daemon and tick, with a file clock and the automation execution seam."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def main():
    from tinyassets import automations, owner_runtime
    from tinyassets.control_plane import next_wake, tick
    from tinyassets.owner_daemon import create_app, serve
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    root, relay, secret, socket = map(Path, sys.argv[1:])
    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        os.environ.pop(name, None)
    os.environ["TINYASSETS_ASSIGNED_QUEUE_POLL_SECONDS"] = "0.05"
    owner_runtime.OWNER_MOUNT = root
    owner_runtime._is_owner_mount = lambda path: path == root

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromtimestamp(float((root / "clock").read_text()), timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    tick.datetime = next_wake.datetime = automations.datetime = Clock

    def execute(base, automation, provider_call, branch, inputs, on_run_started=None):
        # Keep admission, the durable claim, completion and recurrence real.
        with (root / "executed").open("a") as stream:
            stream.write(automation.automation_id + "\n")
        return SimpleNamespace(run_id="scheduled-run", status="completed", error="")

    automations._execute = execute
    serve(create_app(relay=relay, secret_file=secret), socket)


if __name__ == "__main__":
    main()
