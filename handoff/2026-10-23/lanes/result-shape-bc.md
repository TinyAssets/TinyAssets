Head: `993bb6c73aee7f0889341d9df522b33f9ec0f4f3`. Worktree clean.

- **B:** `e3fbe113595` — discovery moved into owner boxes; broker detection and budget guard deleted.
- **C:** `0a6c9d1688e` — one-shot repair, strict membership preservation, narrowed stale-default fallback.
- **D:** `993bb6c73ae` — committed locks, digest verification, `npm ci`, minimal build environment.

**D remains integration-blocked:** your reserved-record boundary prevented applying four digest fields. Lane A must apply the prepared [digest-only patch](C:/Users/Jonathan/Projects/wf-source-records/openspec/changes/owner-daemon-in-box/subscription-lock-digests.patch) before building. Missing digests deliberately fail the build.

Runtime/build files, relative to `tinyassets/`:

| Role | Files |
|---|---|
| Daemon container / shared door | `onboarding/{__init__,model_connect,connection_definition}.py` |
| Broker container | `broker/{http_connect,model_connect,server}.py`; registration helper in `connection_oauth/discovery.py` |
| Box harness / owner daemon | `connection_oauth/{discovery,model_discovery}.py`; `onboarding/{model_discovery,connection_definition,source_connect}.py`; `api/{connection_uses,model_access_requests}.py`; `providers/served_model_plan.py` |
| Page | `onboarding/app/main.js` |
| Box image | `providers/subscription_image.py`; four `providers/source_locks/*/{package.json,package-lock.json}` pairs |
| Offline repair | `cutover_assignment_repair.py` |

Lead’s repair command, once per stopped mounted owner volume:

```sh
python -m tinyassets.cutover_assignment_repair \
  --owner-root <mounted-owner-volume> \
  --ledger <broker-outbound.db> \
  --stopped
```

Focused Linux oracle runs passed **99, 19, and 13 tests**. Ruff, `node --check`, commit hooks, and patch applicability passed. Lane A’s reserved files were untouched; no production access occurred.