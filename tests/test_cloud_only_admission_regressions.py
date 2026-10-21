"""Negative regressions for cloud-only runtime admission (change task 9).

Test-matrix rows 1, 2 and the bounded 4/5 pair from
`openspec/changes/cloud-only-runtime-admission/design.md`. These are written
against the **unfixed** tree on purpose: the negatives assert the refusal that
tasks 6-7 are supposed to add, so they fail here at the missing-refusal
assertion and pass once admission is actually wired. The positives assert
preserved behaviour and pass at baseline, which is what proves the negatives
are refusals rather than broken setup.

Nothing here is enforcement, and nothing here is production authority. Every
case runs on a temp data root with a fixture DB and an injected resolver
(`design.md` § Local isolated tests vs production authority): a green run
establishes no custody, no cloud fact and no live claim. The resolver is
injected through the existing `ProcessProvenanceObservation` seam — no
environment bypass, no tests-only production helper, and no production edit.
"""

from __future__ import annotations

import pytest

from tinyassets.platform_runtime_provenance import (
    CLOUD,
    NOT_CLOUD,
    RuntimeProvenance,
)

NOW = "2026-07-24T08:01:00+00:00"

UNADMITTED = RuntimeProvenance(
    verdict=NOT_CLOUD,
    reason="metadata_unreachable",
    metadata_reachable=False,
    expected_identity_prepared=False,
)
ADMITTED = RuntimeProvenance(
    verdict=CLOUD,
    reason="instance_match",
    metadata_reachable=True,
    expected_identity_prepared=True,
)




# --- matrix 5: the copied-container label set (shared by every site) -------

#: Non-path environment values `deploy/compose.yml` bakes onto the `daemon`
#: service (read 2026-09-23). These are copied **literally**, because that is
#: exactly what an off-cloud checkout of this repo, a leaked env file or a
#: `docker run` of the published image gets for free.
_COPIED_CONTAINER_ENV = {
    "TINYASSETS_ALLOW_API_KEY_PROVIDERS": "0",
    "TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY": "1",
    "TINYASSETS_AUTO_SHIP_RUBRIC_MODE": "enforce",
    "TINYASSETS_AUTO_SHIP_TRAJECTORY_MODE": "enforce",
    "TINYASSETS_MCP_CANARY_URL": "https://tinyassets.io/mcp",
    "TINYASSETS_ONBOARDING_APP": "1",
}

#: Container **labels**: the hostname a copied deployment can trivially claim,
#: and the service name. `socket.gethostname`/`getfqdn` are patched too, so the
#: lie is not only in the environment block — a guard that resolved the host
#: itself instead of reading provenance would still be fooled here.
_COPIED_CONTAINER_HOSTNAME = "mcp.tinyassets.io"
_COPIED_CONTAINER_NAME = "tinyassets-daemon"

#: Path-valued container env (`compose.yml`: `HOME=/app`,
#: `TINYASSETS_DATA_DIR=/data`, `CODEX_HOME=/data/.codex`,
#: `CLAUDE_CONFIG_DIR=/data/.claude`,
#: `TINYASSETS_REPO_ROOT=/data/community-pool`), keyed by the path *relative to
#: the data root* so it can be re-rooted. `None` means "the data root itself".
_COPIED_CONTAINER_PATH_ENV = {
    "HOME": None,
    "TINYASSETS_DATA_DIR": None,
    "CODEX_HOME": ".codex",
    "CLAUDE_CONFIG_DIR": ".claude",
    "TINYASSETS_REPO_ROOT": "community-pool",
}




#: Parameters for a positive control that must hold **both** bare and dressed.
#: Every admitted control below runs twice: once as a plain process, once
#: wearing the full copied-container label set. The `copied_labels` leg is the
#: positive half of matrix row 5 — it proves the label set is inert in the
#: admitted direction too, so the row-5 negatives are refusing on the injected
#: verdict rather than on some incidental breakage the labels introduce (a
#: re-rooted `HOME`, an unresolvable `CODEX_HOME`). Without it, every row-5
#: negative would be consistent with "the labels broke the fixture".
COPIED_LABEL_VARIANTS = [
    pytest.param(False, id="plain"),
    pytest.param(True, id="copied_labels"),
]














# --- matrix 1: negative direct claim --------------------------------------






# --- matrix 2: negative registration --------------------------------------






# --- matrix 4/5 (bounded): a row and a cloud-looking host are not authority --






