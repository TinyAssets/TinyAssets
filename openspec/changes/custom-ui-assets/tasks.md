## 1. Assets and libraries (A + B)

- [x] 1.1 Blob table, write-time manifest validation, storage charge and GC (`custom_agents.py`, `storage_accounting.py`)
- [x] 1.2 `put_asset` / `remove_asset` operations, `from_file` through the owner read
- [x] 1.3 Owner-door byte route for assets and vendored libraries (`/app/api/ui-asset`)
- [x] 1.4 Vendored library set with manifest + SHA-384, excluded from the plugin mirror
- [x] 1.5 Frame: `blob:` policy, import map, global libraries, `ta-asset:` substitution, `tinyassets.asset()`
- [x] 1.6 App: parse new fields, fetch + verify + post bytes, drop the 49 KB bounds
- [x] 1.7 Publish carries libraries and refuses assets by name; handbook docstring updated
- [x] 1.8 Real-browser proof (`real_browser`): three.js module UI renders from blobs; exfil attempts blocked

## 2. Visual self-check (C)

- [x] 2.1 Harness `read` shows images, bounded by one shared helper (#4306)
- [x] 2.2 `read_graph target="app_ui_preview"`: headless render of the owner's own UI, no network; screenshot to /u/previews, fps, errors
- [ ] 2.3 chromium-headless-shell in the daemon image (interim; box image later)

## 3. Remaining

- [ ] 3.1 Live proof: the founder's agent rebuilds the village with a library and assets; deployed-sha assertion
