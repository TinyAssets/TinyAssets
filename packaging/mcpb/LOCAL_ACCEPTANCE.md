# MCPB Local Acceptance Record

What the local MCPB product's automated proof actually covers, and what it
does not. The local bundle and hosted `https://tinyassets.io/mcp` are two
products: evidence from one never satisfies the other's gate.

Historical test receipt (the suite was retired under the 2026-10-21 founder rule): verified
2026-09-22 on Windows / Python 3.14.3 against this branch (36 passed; isolated test fixture,
not cloud or desktop-install acceptance).
Re-stamp this file when the covered behavior changes.

## Superseded: the off-cloud enumeration claim

This record previously stated, without qualification, that the staged
launcher boots over stdio from an isolated local data directory and
enumerates seven handles. **That claim is superseded.** The founder's
cloud-only rule (OpenSpec change `cloud-only-runtime-admission`) guards
`universe_server.main` for *every* transport, stdio included: a process that
cannot resolve cloud provenance refuses to serve. A local install is such a
process, so the boot it used to claim no longer happens and must not.

What replaces it is two independent kinds of proof, below: an unmodified
shipped-launcher refusal, and a catalog enumeration that runs only under
explicitly simulated, fixture-only admission evidence. Local development and
test fixtures confer no production serving authority.

## Proven

The historical checks covered:

- **Staged artifact, not source.** `python packaging/mcpb/build_bundle.py`
  stages the live `tinyassets/` package, import-probes it in a subprocess,
  and fails the build when the staged manifest and the middleware-applied
  staged runtime catalogs differ.
- **Exactly seven handles.** The staged manifest and the staged runtime both
  declare `read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`,
  `converse`, `get_status` — no hidden legacy fat tools.
- **Unadmitted local startup is refused (shipped artifact, no harness).**
  `test_staged_bundle_refuses_unadmitted_startup` spawns the staged
  `server.py` exactly as the manifest's `mcp_config` does, in a
  provider-free temporary data root with no deploy-recorded instance
  identity. It exits `78` (sysexits `EX_CONFIG`,
  `PLATFORM_NOT_CLOUD_EXIT_CODE`), publishes the stable `platform_not_cloud`
  refusal token on stderr, and writes no `initialize`/`tools` response to
  stdout. Nothing is skipped, mocked or stubbed: the refusal comes from the
  real launcher and the real staged runtime.
- **Real stdio catalog enumeration, under simulated admission only.**
  `test_staged_bundle_enumerates_seven_under_simulated_admission` runs the
  same unmodified staged `server.py` and the same newline-delimited JSON-RPC
  handshake, from a fixture-only child harness that injects one
  `ProcessProvenanceObservation` through the runtime's existing Python seam.
  `initialize` returns `serverInfo.name == "TinyAssets"` and `tools/list`
  enumerates exactly those seven. The harness asserts its own imports
  resolved inside the staged bundle (not the checkout), tripwires the
  metadata readers so the positive makes no metadata network call, lives in
  a temp directory outside the repo, and enters no production file and no
  environment flag — `test_admitted_process_harness_is_fixture_only` holds
  that. **This is simulated process evidence, not a cloud deployment and not
  a successful desktop installation.** It establishes that the artifact
  carries a working stdio transport and the canonical catalog; it
  establishes nothing about whether a user's off-cloud install serves — the
  refusal above is the answer to that.
- **Required data directory fails closed.** A blank, missing, or
  non-directory `TINYASSETS_DATA_DIR` raises an actionable error before any
  transport starts. Without that guard `storage.data_dir()` would resolve an
  unset value to the platform default (`%APPDATA%/TinyAssets`,
  `~/.tinyassets`) and serve a directory the user never selected.
- **Optional default universe is validated then exported.** A blank host
  substitution is treated as unset; separators, a leading dot, or an
  unsubstituted `${...}` template are rejected before transport.
- **Provider-free.** `build_bundle.py`'s import and catalog probes and the
  stdio probes all strip provider credentials (`PROVIDER_CREDENTIAL_ENV`)
  from the environment they hand a subprocess, and no probe calls a tool —
  enumeration only. A green packaging gate cannot spend maintainer quota.

## Observed local auth posture

The bundle configures no auth: its manifest declares only
`TINYASSETS_DATA_DIR` and `UNIVERSE_SERVER_DEFAULT_UNIVERSE`, and the wrapper
introduces no environment of its own. Auth provider selection reads
`UNIVERSE_SERVER_AUTH` from the inherited environment
(`tinyassets/auth/provider.py`, `create_provider`), so with nothing
configured the staged runtime selects the no-auth `DevAuthProvider` — proven
by probing the staged runtime under exactly the bundle's environment.
Requests then run as the runtime's anonymous local actor, and an
uncredentialed client completes `initialize` and enumerates the catalog
*under the simulated admission above*. Cloud admission and client
credentials are different facts: the credential-free observation says
nothing about admission, and admission is refused for a real local install.
The product's boundary is the local OS process plus the user-selected data
directory — nothing else.

Two honest qualifications. Enumeration is not an authorization check, so
per-call gating behavior is not established by that handshake. And a host
that exports `UNIVERSE_SERVER_AUTH` into the bundle's process would change
the selected provider; that is an environment posture the package neither
configures nor claims.

Sharing handle names with hosted `/mcp` is catalog parity.
It is **not** identity parity: no hosted isolation, no WorkOS subject, no
OAuth scopes, no founder grants. Any future local identity boundary requires
its own reviewed package change.

## Not proven

- **That a local installation serves at all.** It does not: serving requires
  cloud admission, and a local process is refused. The enumeration proof
  above buys its `initialize` with fixture-only simulated evidence and
  claims nothing beyond the artifact's contents.
- **Host install path.** The proof spawns `server.py` directly. Installation
  through a real MCPB host (`uv run` via the manifest's `mcp_config`,
  user-config prompts, `.mcpb` pack/validate through
  `npx @anthropic-ai/mcpb`) is separate evidence and is not asserted here.
- **Tool execution.** Nothing beyond enumeration runs. No `converse`,
  `run_graph`, or other result payload is exercised locally, so
  provider-backed behavior — and every per-call permission, visibility, and
  ownership check — is unproven by this record.
- **Actor-dependent behavior.** Because the local actor is anonymous with no
  founder identity, permission-gated, visibility-gated, ownership-gated, and
  founder-grant behavior is unproven locally and may legitimately differ from
  the hosted product. Do not infer hosted behavior from a local run, or local
  behavior from a hosted canary or rendered chatbot proof.
- **Real user use.** No dated evidence yet of an external user installing and
  using the bundle.
