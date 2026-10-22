## Context
Installed acquisition_presets.json and free_source_presets.json describe supported connection paths. The provider definition registry only supports api_key_http; it does not contain subscription login adapters. Do not invent unsupported sign-in buttons.

## Decisions
Publish all validated hosted presets as hosted_sources alongside sources and sign_in_sources. Remove primary. Render every group open, with equal button styling. An explicit selection clears prior client recovery and cancels a retained inline flow before beginning another operation. Preserve owner/epoch checks and explicit model-access consent.
Connected accounts retain optional Add source access and the existing automatic model ordering; no new authority or model-selection path.
Center the default chat when no custom layout exists and center desktop request sheets, retaining mobile bottom sheets and saved user placements.

## Verification
Targeted payload and Node-executed UI tests, layout checks at 390 and 2400 pixels, touched Python tests and ruff. No deployment.
