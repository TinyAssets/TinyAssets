# Sandbox sync streaming digests as built (2026-11-18)

The runner records the SHA-256 of a whole file in its SQLite mirror. A download updates it as bytes reach the local file. An upload computes it during the scan that supplies the separate chunk digests for the upload request. Both paths now begin with the contract's existing `sha256Stream()`, as C6 and O4 specified. The same Node hash consumes the same bytes in the same order. The runner already imported this module for individual chunk digests. Its two B7 exceptions can be removed.

The sync regression downloads a binary file with a short final chunk, then uploads it from a fresh mirror. It checks both stored file digests against a one-shot digest of the original bytes and checks the separate ordered chunk hashes offered to the upload door. Omitting the first byte from each update independently on either path failed its mirror assertion. Restoring both paths passed.

The Node 22 runner and probes build as a pair. Changing the runner changes both bundle digests because the probes embed the runner digest. The existing pair check therefore applies to the eventual integrated runner before deployment.

## Rehearsal with the current sandbox callers

- [x] The three source and test changes on 493276e2 applied cleanly to the
  ten-stage rehearsal recorded at 1da3cd6f with the shared paste contract
  9c4c50ac. The combined test file includes the new refused-child regression
  2a02ac62. On Node 22 and 24, the ordinary whole-file digest case and the
  size case with the 20 MiB ignore file both passed. Full Node 24 platform
  TypeScript and all 63 sandbox guards passed on this eleven-stage tree.
- [x] Node 22 and 24 built byte-identical runner and probe pairs. The runner
  measured 267,377 bytes with SHA-256
  a82ff2a8df5a00936b159442380fd87b7e2e162f71320d88dbc28d96187a3def.
  The 202,039-byte probes embedded that digest and had SHA-256
  e0c125308fc91455e5700ba0c5ee7eecb86125819f56b1d0a0c89a7e90a05bd0.
- [x] In the first CI layer/kernel run, the layer inventory counted thirteen
  local node_modules symlinks as repository entries (63 pass, one failure).
  A clean offline npm ci using the platform lockfile installed 282 packages.
  On the same source all 64 layer/kernel checks then passed without changing
  a guard or its scope.
- [ ] These are local build and regression results. Pinned image acceptance,
  recovery of refused local edits and integration on main remain open.
