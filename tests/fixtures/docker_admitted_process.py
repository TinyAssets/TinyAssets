"""CI FIXTURE ONLY: simulated process admission for image protocol checks.

Mounted read-only by docker-build.yml, never copied into a release image.
The unmodified-image step runs /usr/local/libexec/ta-box-entry.py and refuses
a non-owner-box marker (RuntimeError, process exit 1). This harness proves
the image's HTTP/auth/catalog wiring, not actual cloud provenance.
"""

import runpy
import sys

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
    print("CI FIXTURE ONLY: simulated admission, not cloud acceptance", flush=True)
    runpy.run_module("tinyassets.universe_server", run_name="__main__")


if __name__ == "__main__":
    main()
