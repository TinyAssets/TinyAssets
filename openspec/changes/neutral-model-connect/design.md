## Context
Installed acquisition_presets.json and free_source_presets.json describe supported connection paths. The provider definition registry only supports api_key_http; it does not contain subscription login adapters. Do not invent unsupported sign-in buttons.

## Decisions
Publish all validated hosted presets as hosted_sources alongside sources and sign_in_sources. Remove primary. Render hosted sign-in buttons first without details wrappers; key-source names and the compatible endpoint stay visible below. An explicit choice clears prior client recovery immediately; cancellation is best-effort and cannot block a fresh flow. Preserve owner/epoch checks. The source button authorizes connecting that source for the agent; a valid callback finishes through the existing model-access answer path without a further tap. Accept legacy setup.primary as a single displayed entry during page/server rollout, never as a preselection.
Connected accounts retain optional Add source access and the existing automatic model ordering; no new authority or model-selection path.
Center the default chat when no custom layout exists and center desktop request sheets, retaining mobile bottom sheets and saved user placements.

## Verification
Targeted payload and Node-executed UI tests, layout checks at 390, 1366, 1920 and 2400 pixels, touched Python tests and ruff. No deployment.
