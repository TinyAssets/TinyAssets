# Check inline file bytes before staging

- [x] Reviewed the bundle writer, host staging pass, Doors reader and Owner import on main at `f934871ad`. The writer uses the contract's inline content ID for the disk filename. The index hashes the metadata parts. Doors and Owner already independently check the inline bytes against that ID. Host staging checked chunk names but omitted this check for small files.
- [x] Exercised the ordinary staging entry with a locally truncated nonempty file from the synthetic author's bundle. On main, the fake service received all three messages and the host reported success. On this branch the host refuses the damaged file before its first request. All nine ordinary staging cases passed on Node 24, including valid bundles and dry runs.
- [x] The host now uses the existing inline size limit, content ID and part name from the shared contract. Produced bundles and their format stay compatible.
- [ ] Run the full importer suite and platform typecheck, then exercise the change alongside the staged identity, digest and historical bundle changes. Coordinator integration on main remains open.
