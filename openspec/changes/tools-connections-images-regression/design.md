## Context

Production reported owner-content startup, connection and image failures. The prior HTTP acceptance replaced `_broker_channel`, bypassing the broker failure. The broker's inference classifier uses the general owner-content reader to read provider definitions. That reader opens center directories for listing, while broker custody deliberately grants only traversal and access to specific metadata leaves.

## Goals / Non-Goals

Restore tools, images and granted HTTP requests while preserving owner UIDs, mount boundaries, grant revocation and inference accounting. No host changes, migration or provider-specific behavior.

## Decisions

- Reproduce on the restored production snapshot and shipped image. Fix shared startup boundaries, without bypassing the cell or its proof.
- Preserve model classification from trusted broker capability records and registered definitions. Read the existing broker-readable definition metadata through traversal-only, no-follow descriptors; do not grant directory listing or owner-content access. Keep inference reservations mandatory for model sends.
- Use a local HTTP endpoint beneath real broker dispatch, including agent calls and the stored intake workflow; retain cross-owner refusal checks.
- Read an actual PNG through the advertised read tool and verify decoded image content reaches each provider.

## Risks / Trade-offs

- Model classification bypass: test model capability and registered model paths alongside ordinary HTTP; retain fail-closed behavior.
- Synthetic checks masking production startup: use the production image, migrated backup and real HTTP converse with both installed CLIs.

## Acceptance inputs

- Production revision: `b5631fdb05f800fa98117fbff7bf79a15a7c0f40`; immutable image `ghcr.io/tinyassets/tinyassets-daemon@sha256:70dc8ba65bfb6bbd08a9f0b6d051776d81b28d85f0582244e0fbff129c4da778`.
- Local backup: `C:/Users/Jonathan/AppData/Local/Temp/celle2e-round4/production.tar.gz`; command center `u-01kxm1vszd8hwp7em418asq8h9`.
- Production was inspected read-only. All mutations and HTTP fixture calls ran on disposable local copies with no network uplink.

## Confirmed findings and implementation (2026-10-10 resumed)

- Real broker POST failed on the restored/migrated b5631fdb05 image with `inference usage authority refused`. `read_definitions` now walks pinned O_PATH/no-follow directories and reads only the daemon-published metadata leaf; both classification and source validation use it. The classifier was introduced in #4409; isolation's broker UID/metadata custody exposed its incompatible general owner-content reader. No extra directory permissions or inference exemptions were added.
- A real converse image read reproduced `could not enter the admitted image decoder cell`. The owner-only decoder cutover (5c99c09a5b2) required a center, but `universe_tools._read_image` still omitted it. Pass the existing bound center; preserve decoder owner admission.
- The restored snapshot additionally exposed `published seed version changed` before any vendor request. #4585 changed starter-access bytes without advancing immutable release 1. Publish release 2, retain normal upgrade/customization behavior, and pin the release hash. Claude peer diagnosis agreed with this cause; resetting the restored seed store would hide the regression and was not done.
- The reported `invalid owner content frame` has NOT reproduced: unmodified b5631's direct four-tool preflight passed, and real served turns pass after the two fixes above and the seed version bump. No speculative owner-content/launcher change has been made. This remains an explicit attribution limit, not a claimed root cause.
- Acceptance now uses a real local HTTPS peer, normal DNS resolution and TLS verification on a network namespace with no uplink. No broker, cell, launcher, or relay function is replaced, including former observation wrappers. A structural guard rejects these substitutions. Both CLIs and a bound sub-agent prove all tools, decoded pixels at the vendor input, and ta service POST; background, automation, and stored Patch Request Intake prove broker effects. Cross-owner refusal and workspace provisioning remain covered.
- Fresh production-copy acceptance passed from an untouched backup restore: 6 centers migrated with zero remaining ownership differences, real server health/canary and role checks passed, then 3 converse turns proved all four tools, decoded images and service calls. All 8 HTTPS POSTs traversed the real broker; background, automation and stored intake completed, including the actual `file_issue` event. Foreign callers were refused and workspace provisioning completed. The permanent probe has no direct tool preflight that could prime startup. Patched image digest: `sha256:a3e4064bbe1acc891f04f3bc8b698c332847a94ef39549755776a4236dad3f94`.
- Linux suites passed 363 + 205 + 152 tests with no skips. Structural guards passed 594 checks twice; the acceptance-integrity guard passed again after removing the diagnostic preflight. Ruff, plugin build and strict spec validation pass. The committed-diff hygiene gate passed: 7 tests added, none removed, no tampering findings. Production is unchanged.


## Cross-family floor review

Claude returned `VERDICT: APPROVE`, with no floor/correctness findings. AGREE: the metadata reader preserves publisher custody and model accounting, the image scope is the existing admitted scope, and seed release 2 preserves owner customizations. AGREE with removing the stale investigation handoff. The original tool-frame cause remains unconfirmed; the review found no evidence justifying a launcher change.
