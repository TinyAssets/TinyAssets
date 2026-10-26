"""CI FIXTURE ONLY: simulated process admission for image protocol checks.

Mounted read-only by docker-build.yml, never copied into a release image.
The unmodified-image step runs /usr/local/libexec/ta-box-entry.py and refuses
a non-owner-box marker (RuntimeError, process exit 1). This harness proves
the image's HTTP/auth/catalog wiring, not actual cloud provenance.
"""

import json
import os
import runpy
import sys
from unittest.mock import patch

# Load the image entry the Dockerfile CMD execs. runpy sets __name__ to
# "<run_path>", so main() does not start the broker. The protocol smoke
# serves universe_server itself after simulated provenance.
if __name__ == "__main__":
    entry = runpy.run_path("/usr/local/libexec/ta-box-entry.py")
    if not callable(entry.get("check_layout")) or not callable(entry.get("main")):
        raise RuntimeError(
            "image entry /usr/local/libexec/ta-box-entry.py has no layout gate"
        )
    sys.path.insert(0, "/app")

import tinyassets.platform_runtime_provenance as provenance


def install_canary_credential() -> None:
    """Publish the smoke token where wiki_canary_token_matches reads it."""
    if "TINYASSETS_WIKI_CANARY_TOKEN" not in os.environ:
        return
    from tinyassets.storage import data_dir

    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".router-credentials.json"
    path.write_text(
        json.dumps({"TINYASSETS_WIKI_CANARY_TOKEN": os.environ["TINYASSETS_WIKI_CANARY_TOKEN"]}),
        encoding="utf-8",
    )
    path.chmod(0o600)


def install_fixture_supervisor() -> None:
    """Let this single container pass the PID1 supervisor check."""
    import tinyassets.broker.supervisor as supervisor

    original = supervisor.get_supervisor

    def get_supervisor(data_root):
        live = original(data_root)
        if live is not None:
            return live
        return object()

    supervisor.get_supervisor = get_supervisor


def install_owner_construction() -> None:
    """Select the owner route table only while the app is constructed.

    Lifespan and requests keep ``owner_runtime.current()`` unset, matching
    ``tests/owner_app_fixture.py``.
    """
    import tinyassets.universe_server as universe_server

    create = universe_server.create_streamable_http_app

    def create_streamable_http_app():
        with patch("tinyassets.owner_runtime.current", return_value=object()):
            return create()

    universe_server.create_streamable_http_app = create_streamable_http_app


def main() -> None:
    from pathlib import Path

    module_path = Path(provenance.__file__).resolve()
    if Path("/app/tinyassets").resolve() not in module_path.parents:
        raise RuntimeError("fixture must import the built image's runtime")

    def no_metadata(*args, **kwargs):
        raise AssertionError("simulated fixture must not read cloud metadata")

    provenance.read_metadata_instance_id = no_metadata
    provenance._read_metadata_instance_id = no_metadata
    provenance.build_metadata_opener = no_metadata
    simulated = provenance.RuntimeProvenance(
        verdict=provenance.CLOUD,
        reason="fixture_simulated_admitted_process",
        metadata_reachable=True,
        expected_identity_prepared=True,
    )
    provenance._PROCESS_OBSERVATION = provenance.ProcessProvenanceObservation(
        resolver=lambda: simulated,
    )
    install_canary_credential()
    install_fixture_supervisor()
    install_owner_construction()
    print("CI FIXTURE ONLY: simulated admission, not cloud acceptance", flush=True)
    # Call the imported module. run_module would exec a fresh copy and drop
    # the construction wrapper installed on tinyassets.universe_server.
    import tinyassets.universe_server as universe_server

    universe_server.main()


if __name__ == "__main__":
    main()
