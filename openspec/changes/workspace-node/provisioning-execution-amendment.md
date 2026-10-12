# Provisioning execution: candidate implementation boundary

September15,2026. This supplements D3/D4 for the unfinished cloud dependency
path; it is NOT implementation approval or a new public MCP handle. Existing
workspace-node proposal, consent, storage and publication rules remain binding.
Do not implement the proposed network bridge before cross-family shape review.
The original checkout/push review cap is not reset by this document.

Fable5.1 execution-shape review31374 completed223s/exit0, ADAPT. Reviewer
confirmed this is the never-designed execution seam, not a checkout/push
re-review. Required corrections incorporated below after source inspection at
ec867fa4. Lead accepts the corrected shape for implementation; this is NOT an
exact-head runtime approval. See 2026-09-15-provisioning-execution-shape-fable.md (archived in git).

## Reverified seams

Current runtime source inspected at resolver repair ec867fa4 (basebd8eaa95):
workspace_fs.read_regular_file_beneath already provides bounded no-follow
dirfd reads (line446); copy_regular_file_beneath provides the corresponding
exclusive-created destination (line493). Do not build another filesystem reader.
workspace.py::_check_provision_consent is still unused; checkout lines972-979
refuse provision after publication without attempting it. workspace_resolver
only stages/builds commands. node_sandbox::_bwrap_argv gives the workspace jail
no network, explicit environment, read-only system mounts and one held workspace
bind. All these seams exist; execution composition is the missing capability.

The old pip flags are fixed in PR3857, independently approved on exactec867fa4,
not deployed at this inspection. Its parser proofs do not establish a resolver
jail, package installation or browser runtime.

## Proposed execution boundary for review

1. Keep checkout's existing packet and typed per-connection/repo consent. After
   credentialed staging is destroyed, but BEFORE handing the new generation to
   user code, check provisioning authority. Consent refusal preserves the
   checkout with truthful refusal evidence; unavailable/read errors never
   implicitly authorize network. Existing full-access semantics need explicit
   review against the owner's accepted wording, not a new silent grant. Source
   access_mode exclusively from the resolved connection record, never the packet.
2. Read declared manifests through the held repo descriptor with existing
   bounded regular-file helpers; feed existing canonicalizing parsers. Refuse
   invalid paths, encoding, grammar and limits before any resolver connection.
   Resolve all required manifests before executing either package manager.
3. Reserve the existing workspace byte budget for maximum provision transfer
   and hold the existing job/lease lifecycle. Keep manifests/cache in fresh
   worker-owned per-attempt scratch under the lease root but OUTSIDE its content
   directory, never a shared mutable package cache or directory user code can
   influence. Lease wipe/reconciliation includes this scratch. The resolver sees only
   canonical manifests, an empty cache and read-only toolchain files.
4. Candidate network bridge: resolver remains in its own network namespace. A
   minimal local proxy inside that namespace forwards connection requests over
   a dedicated inherited socket to an uncredentialed parent broker. The broker
   accepts only HTTP CONNECT to HTTPS443 on pypi.org, files.pythonhosted.org and
   registry.npmjs.org. Refuse plaintext forwarding and other methods/hosts/ports.
   Validate EVERY DNS answer with
   the existing outbound address classifier and connects to a pinned address.
   The child cannot choose a host socket, arbitrary address, port or credential.
   TLS verification stays end-to-end in the package manager. No --share-net,
   Docker socket, host-loopback access, ambient proxy or generic host executor.
   Reuse workspace_git's pinned-address validation, not another classifier.
   Count actual bytes and connections at the broker boundary. This is a reviewed
   candidate, not a claimed ready-made or implemented egress mechanism.
5. Explicit pip/npm proxy options must work under their real isolated/config
   semantics. Add the fixed proxy option only to acquisition argv/builders and
   their fixed-flag admission; offline commands stay proxy-free. No arbitrary
   caller-supplied proxy. No build scripts run during acquisition: binary/hash-only Python,
   npm ignore-scripts. Keep transfer, connections, output, wall time and cache
   bytes bounded; record actual bytes without remote error bodies/secrets.
   npm ci may extract tarballs in its isolated staged prefix despite ignore-
   scripts; this prefix contains no checkout. Use the D4 workspace limit class,
   not the ordinary code-node16MiB file-size limit which cannot hold many wheels.
   September15 real npm proof identified a missing shipped runtime resource:
   Debian Node/OpenSSL requires /etc/ssl/certs/ca-certificates.crt. Acquisition
   binds only that fixed public bundle read-only, refusing path redirection;
   neither /etc nor private keys are mounted. Offline/ordinary jails gain no
   CA bind. TLS verification remains enabled. Exact-head execution review must
   include this resource and the pre-fix issuer-error/post-fix real npm proof.
6. After resolver exit and verified process-tree termination, close/revoke the
   bridge before offline installation. Bind acquired cache read-only inside
   the existing workspace jail. Installation, including permitted package code,
   has the checkout but no network, no credential or broker descriptor. Extend
   the jail with a second descriptor-validated typed mount: acquisition gets
   canonical manifests plus writable cache, installation gets /workspace plus
   read-only cache. No generic arbitrary extra-bind escape hatch. Only
   verified canonical manifests/digests cross from acquisition to installation.
   Offline npm uses two fixed read-only file overlays at /workspace/package.json
   and /workspace/package-lock.json instead of rewriting the user's originals.
   Their held regular-file descriptors must match the canonical manifest directory's
   named entries, have bounded size, and be included in the exact typed inheritance
   set. Checkout destinations must be regular files, not symlinks. Bind setup
   consumes those descriptors before package code, as with directory handles.
   No arbitrary file destination or additional network/credential access is added.
   Real npm proof must preserve both original files byte-for-byte and keep the
   original root lifecycle scripts unexecuted; dependency scripts remain permitted
   offline. This implementation detail remains subject to exact-head review.
7. Publish the workspace capability only with a truthful provisioning result.
   Exact typed failure must remain visible; partial installation is not success.
   Cleanup follows the lease/outbox lifecycle and preserves the existing
   unknown-transfer maximum charge. No retry hides a failed or uncertain stage.

September15 implementation clarification: each acquisition uses a fresh
server-generated operation id. The existing ledger may return an already-
reconciled smaller amount for a reused id; that is not authority to download
again. Python builders add interpreter `-I` as well as pip `--isolated`, since
pip's option alone does not prevent checkout module shadowing. Installation
uses an exclusively created `.venv`; a checkout-provided environment is retained
and the attempt fails, rather than executing or deleting it. Cancellation reaches
both stages through the root-owned callback and is rechecked before publication;
only that callback, not packet evidence, can classify graph failure as cancelled.
These details are part of the pending exact-head execution review.

## Browser capability is a separate required part of the outcome

Driver package, compatible browser binary and OS libraries are distinct. D3's
registry-only package download does not admit browser CDN assets or install OS
libraries. Need a reviewed runtime-library baseline plus bounded, digest-bound
artifact acquisition/installation through existing governed resource boundaries.
Do not silently expand the registry broker to arbitrary URLs or run browser
install scripts with both network and checkout. The first implementation slice
supplies system headless Chromium and required OS libraries as image toolchain
resources under existing read-only system binds; the driver remains a user-
declared dependency. Compatibility, sandboxing and captures must be proven.
This does NOT fulfill arbitrary user-chosen browser binary acquisition: digest-
bound governed per-run artifacts remain required follow-up in the full goal,
not silently removed scope. Neither path is implemented by this note.
The cloud user's own code can compose
capture through ws.run once the generic execution resources are available.

## Memory premise corrected by an actual Linux check

September15 07:31UTC, WSL Ubuntu/Python3.12.3, installed Node22.22.1:
`wsl -d Ubuntu -- python3 /mnt/c/Users/Jonathan/.codex/worktrees/0a7f/TinyAssets/output/workspace_v8_limit_probe.py`
ran a trivial Node command with a from-empty environment, cores disabled,
CPU/deadline bound, first with inherited address-space limit, then with D4's
1536MiB RLIMIT_AS. BOTH exited0; RSS about42MiB. Thus this installed Node is
not shown to require a limit increase. Browser-specific reservations remain
unverified. This is Linux subprocess evidence, NOT bubblewrap, cgroup or cloud
acceptance. Do not loosen memory isolation on an unverified V8 premise.

## Proof required before enabling this path

Real Linux jail: private/loopback/mixed DNS and direct-network attempts refuse;
canonical manifests/cache cannot expose checkout or credentials during download;
bridge revoked before package code; archive/path/digest errors refuse; tree
timeout and memory/cache bounds kill descendants; cancellation cleans through
the existing lifecycle; existing checkout/push remains valid. Real pip/npm
acquisition and offline execution must succeed using fixed public fixtures.
Then the app agent must use its OWN project to install the driver, obtain a
compatible browser, produce real desktop/phone captures and explain remaining
blocks through a rendered conversation. No operator-built user workflow,
fabricated screenshots, borrowed credential or online-host dependency.
