"""CI FIXTURE ONLY: simulated process admission for image protocol checks.

Mounted read-only by docker-build.yml, never copied into a release image.
The separate unmodified-image test on layout-2 must refuse with exit 78. This harness
proves the image's HTTP/auth/catalog wiring, not actual cloud provenance.
"""

import runpy
import sys
from pathlib import Path

# Exercise the real fresh-volume bootstrap before any application import.
# Only cloud provenance is simulated, after PID1 has retired its authority.
if __name__ == "__main__":
    launch = runpy.run_path("/usr/local/libexec/ta-launch.py")
    launch["boot"](launch)
    sys.path.insert(0, "/app")

import tinyassets.platform_runtime_provenance as provenance


def main() -> None:
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
