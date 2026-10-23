## 1. Box view

- [ ] 1.1 Extend the protected browser login view through `BoxProvider.view`; isolate its browser and view server under a separate box-local OS user and process/mount namespace, with no agent-reachable capture, input or debug channel.
- [ ] 1.2 Render one live takeover in the interface package with owner start, stop, reconnect and truthful unavailable states.

## 2. Acceptance

- [ ] 2.1 Add `test_box_view_agent_isolation` through `scripts/linux_oracle.py`: agent bash/tools cannot open the view, capture frames, inspect private input or drive the debug channel; prove owner takeover, foreign-owner refusal and stale-view fencing.
- [ ] 2.2 Build the `box-view` shape guard, update registry row N4, then run the guard, Ruff and affected interface checks; verify the live owner view and sync the accepted spec.
