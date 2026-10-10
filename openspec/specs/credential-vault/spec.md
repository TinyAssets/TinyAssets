# Credential Vault

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

Per-universe typed credential store (as-built: flat JSON guarded by best-effort file permissions) with daemon-side resolvers and a provider auth env overlay so a universe runs on its founder's assigned engine, not the host's.
## Requirements
### Requirement: Per-Universe Typed Credential Store

The system SHALL persist credentials in a per-universe vault file named `.credential-vault.json` inside the universe directory, written as a JSON object with `schema_version` 1 and a `credentials` list. Every credential record SHALL declare a `credential_type` that is one of `social`, `llm_subscription`, `llm_api_key`, or `vcs`; a record with any other type SHALL be rejected at write time. A Codex `llm_subscription` record that provides `auth_json_b64` SHALL contain a non-empty, strictly decodable base64 value whose decoded bytes are valid JSON; malformed values SHALL be rejected before the stored vault is replaced. The write helper (`tinyassets.credential_vault.write_credential_vault`) SHALL return a non-secret summary containing only the vault path, credential count, credential types, service names, collapsed-record count, and descriptors for any VCS purpose slots removed by a narrowing upsert, and SHALL never include secret material in that summary.

#### Scenario: Typed credentials round-trip and the summary carries no secret

- **WHEN** a caller writes a vault containing a `vcs`/github record with a token, a `social` record with a token, and an `llm_subscription` record
- **THEN** the returned summary reports `credential_count` 3 and the sorted credential types, and no secret token string appears anywhere in the summary
- **AND** loading the vault back returns the stored records including their secret values

#### Scenario: Unknown credential type is rejected

- **WHEN** a caller attempts to write a record whose `credential_type` is not one of the four allowed types
- **THEN** the write raises a `ValueError` identifying the unknown credential type and the vault is not populated with the invalid record

#### Scenario: Malformed Codex auth bundle is rejected before vault replacement

- **WHEN** a caller writes a Codex `llm_subscription` record whose `auth_json_b64` is not a non-empty strict-base64 encoding of valid JSON
- **THEN** the write raises `ValueError` before replacing the existing credential vault

### Requirement: Fail-Loud Load Semantics

The system SHALL treat a missing vault file as an empty credential set so an absent vault never blocks a daemon. A vault that exists but is not valid JSON, or that contains a non-object credential record or a record missing a `credential_type`, SHALL raise a `ValueError` rather than being silently skipped, so a daemon can never silently grant or lose authority because of a malformed secret file.

#### Scenario: Missing vault loads as empty

- **WHEN** `load_credential_vault` is called for a universe directory that has no `.credential-vault.json`
- **THEN** it returns an empty list without raising

#### Scenario: Malformed vault raises

- **WHEN** a vault file exists but is not valid JSON
- **THEN** `load_credential_vault` raises a `ValueError` describing the parse failure instead of returning partial or empty data

### Requirement: As-Built Storage Protection Is Filesystem Permissions Only

The vault file and any materialized credential artifacts (for example a Codex `auth.json` or a Claude config directory) SHALL be persisted as unencrypted content on disk. The only at-rest
protection SHALL be POSIX ownership, mode and ACL.

Where every role shares one uid, which is a developer host or a desktop install:
- the vault file and secret files SHALL be `0o600`;
- the `.credentials` artifact directory SHALL be `0o700`.

In the production container (`runtime-process-roles`):
- the daemon uid SHALL be the only writer;
- the vault file and secret files SHALL be `0o640`, with their directories `2750`;
- they SHALL be group-owned by the vault group, whose members are the daemon and broker uids,
  so the broker can read and never write;
- that group SHALL be set on the temporary file before the atomic replace, not inherited from
  the command-center root, and a failure to set it SHALL propagate.

Per-launch credential snapshots SHALL be daemon-owned:
- a descriptor-based access ACL SHALL grant read only to that center's dedicated owner
  identity;
- there SHALL be no group or other access and no default ACL;
- parents SHALL grant traverse without listing.

Every one of these modes SHALL come from the single declaration shared by the migration and
every runtime site that creates or re-modes these paths.

As-built limitation: there is no encryption at rest, no cipher and no key management. Base64
fields such as `token_b64` and `secret_b64` are an encoding, not encryption. Best-effort
`chmod` is inert on operating systems that do not honor POSIX modes.

#### Scenario: Secret is stored in cleartext under a restricted file mode
- **WHEN** a credential with a plaintext or base64-encoded secret is written to the vault
- **THEN** the on-disk `.credential-vault.json` contains that secret as recoverable cleartext
  (directly or base64-decodable) with no ciphertext layer
- **AND** on operating systems that honor POSIX permissions the file mode is restricted,
  while the content itself remains unencrypted

#### Scenario: The broker reads the vault and cannot write it
- **WHEN** the daemon writes a credential to the vault in the production container
- **THEN** the replacement file is `0o640` and group-owned by the vault group, set before
  the replace, so the broker can read it
- **AND** a broker attempt to modify that file or any materialized artifact fails with a
  permission error

#### Scenario: No owner cell can read the vault or another owner's snapshot
- **WHEN** an owner's cell of any class opens the vault, the `.credentials` directory, or a
  snapshot sealed for another owner
- **THEN** the open fails with a permission error
- **AND** the same cell can read its own sealed snapshot and cannot write it or list its
  siblings

### Requirement: Forge Credentials Are Ordinary Connections

The vault SHALL NOT provide a forge-specific token resolver (the former `resolve_github_token`, removed 2026-09-24). A universe reaches a code forge only through a connection its owner deposited through the ordinary `connect` request: the secret is stored under `vault://http/<key>` in that universe's own vault and resolved only inside the credential-blind broker, for the universe the connection grant is bound to. No platform or process-environment token SHALL be used for a universe's forge call.

#### Scenario: No connection means no forge call

- **WHEN** a universe's workflow fires an authenticated call naming a connection and grant it does not hold
- **THEN** the call is refused before the wire with the generic `unknown_grant` refusal

#### Scenario: Another user's connection is never used

- **WHEN** a universe's workflow names a grant bound to a different universe
- **THEN** the call is refused before the wire with `grant_not_for_universe`, and each universe's own call carries only its own owner's credential

### Requirement: Subscription-Home Materialization For CLI Writers

The system SHALL materialize per-universe subscription auth homes for the CLI-subprocess writers from `llm_subscription` records. For Codex it SHALL resolve or create a `CODEX_HOME`, writing an `auth.json` from a non-empty, strictly decoded, valid-JSON vault-provided `auth_json_b64` bundle when absent, and when a materialized file exists, ONLY when that file is not provably newer -- newer meaning its own `last_refresh` stamp is readable and strictly later than the vault side's (read from the stored document first, the record's `last_refresh`/`deposited_at` as a fallback). An unstamped file is never preferred, so an owner re-depositing always lands, and writing a minimal `config.toml` when absent, defaulting to a `.credentials/codex` artifact directory when no durable path is configured. A malformed bundle SHALL raise `ValueError` before any existing `auth.json` is replaced. For Claude it SHALL resolve or create a `CLAUDE_CONFIG_DIR`, defaulting to a `.credentials/claude` artifact directory. Availability probes (`codex_subscription_auth_available`, `claude_subscription_auth_available`) SHALL report whether the vault can provide the corresponding auth route.

#### Scenario: Codex auth bundle materializes from the vault

- **WHEN** the vault holds an `llm_subscription` record for `codex` with an `auth_json_b64` payload and no durable home is pre-configured
- **THEN** materialization writes `auth.json` and a `config.toml` under the `.credentials/codex` directory and `codex_subscription_auth_available` returns true

#### Scenario: Codex auth rotation updates a preserved materialization home

- **WHEN** a partial Codex subscription upsert changes `auth_json_b64` while preserving a configured home whose `auth.json` contains different bytes
- **THEN** the next vault-backed Codex materialization atomically replaces `auth.json` with the decoded incoming blob instead of retaining the stale file

#### Scenario: A CLI-refreshed materialized document is preserved over an older vault copy

- **WHEN** a materialized `auth.json` carries a `last_refresh` strictly later than the vault record's stored document and record stamps
- **THEN** materialization leaves it in place rather than overwriting it with the older vault copy, so a rotation the CLI performed is not replaced by a spent one

#### Scenario: An unstamped materialized document loses to the vault

- **WHEN** a materialized `auth.json` has no readable `last_refresh`
- **THEN** the vault's document is written over it, so an owner's re-deposit is never ignored in favour of whatever was left on disk

#### Scenario: Malformed Codex auth bundle preserves materialized auth

- **WHEN** vault-backed Codex materialization encounters an `auth_json_b64` value that cannot be strictly decoded to non-empty valid JSON
- **THEN** it raises `ValueError` before replacing an existing `auth.json`

#### Scenario: Claude config directory resolves from a configured path

- **WHEN** the vault holds an `llm_subscription` record for `claude` with a configured `claude_config_dir`
- **THEN** the resolver returns that directory, `claude_subscription_auth_available` returns true, and the claude-code provider overrides include `CLAUDE_CONFIG_DIR` set to that path

### Requirement: One Shared Single-Flight Credential Refresh

The system SHALL refresh every vault credential that rotates a single-use refresh token through ONE core (`tinyassets.credential_refresh.refresh_credential`), in this order: a per-credential in-process lock, a per-credential cross-process file lock, the vault's exclusive admission taken BEFORE the refresh token is spent, a re-read of the stored value inside those locks, a staleness decision re-made on that re-read value, the network refresh, and an atomic write-back while the vault is still held. The `oauth2` HTTP connection path SHALL supply only its own encoding and retain its existing observable behaviour.

For a deposited `llm_subscription` document the PLATFORM SHALL refresh before launch, ahead of credential custody resolution, and SHALL renew the accepted source afterwards so the provider binding follows the rotated record. It SHALL resolve the token endpoint from the credential itself -- the issuer named by the stored identity token, and that issuer's own RFC 8414 / OpenID metadata -- and SHALL verify the identity token's signature against that issuer's published keys, checking signature and issuer only and NOT expiry. It SHALL NOT refresh a document whose freshness cannot be read, whose issuer it cannot verify, or which is stored as a path rather than inline. A terminal refusal SHALL be reported as a sign-in failure rather than a provider outage, and SHALL be recorded durably OUTSIDE the credential record.

#### Scenario: Concurrent launches spend a single-use refresh token once

- **WHEN** two launches find the same stale stored document at the same time
- **THEN** the refresh token is spent exactly once and the second launch uses the rotation the first wrote

#### Scenario: The refresh is spent only where the credential says it was issued

- **WHEN** a stored document's identity token names an issuer whose published keys verify its signature
- **THEN** the refresh is sent to that issuer's advertised token endpoint with the client id the token names, and a document whose issuer cannot be verified is not refreshed at all

#### Scenario: An expired identity token still authorizes endpoint resolution

- **WHEN** the stored identity token's signature verifies but the token itself has expired
- **THEN** the endpoint is still resolved from it, because expiry is the ordinary condition of the credential being refreshed

#### Scenario: A terminal refusal is a sign-in failure, not an outage

- **WHEN** the token endpoint refuses the stored refresh token itself
- **THEN** the source is reported as needing a new sign-in rather than placed on a provider cooldown, and the refusal is stored so it survives a restart without altering the bytes credential custody is computed from

### Requirement: Per-Universe Provider Auth Env Overlay Without Cross-Universe Leakage

For a host-local call with no explicit or environment-resolved universe, the system SHALL preserve the ordinary host subprocess environment and SHALL NOT invoke a universe vault helper. For a universe-scoped CLI call, `subprocess_env_for_provider` SHALL accept only the exact canonical provider names `claude-code` and `codex`, SHALL classify any non-empty explicit `universe_dir` or `TINYASSETS_UNIVERSE` binding as universe scope before credential work, and SHALL construct the child environment from an empty dictionary rather than a copied host environment. An explicit universe SHALL override an environment-bound universe.

For a host-local call the system SHALL continue to apply the subscription-only API-key policy: when API-key providers are not explicitly enabled, `subprocess_env_without_api_keys` MUST remove `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `GEMINI_API_KEY`, `GROQ_API_KEY`, and `XAI_API_KEY`.

The universe child MAY inherit only required execution basics: `PATH`; Windows process bootstrap variables `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT`, and `SYSTEMDRIVE`; locale, timezone, and terminal variables `LANG`, `LANGUAGE`, `LC_ALL`, `LC_COLLATE`, `LC_CTYPE`, `LC_MESSAGES`, `LC_MONETARY`, `LC_NUMERIC`, `LC_TIME`, `LC_ADDRESS`, `LC_IDENTIFICATION`, `LC_MEASUREMENT`, `LC_NAME`, `LC_PAPER`, `LC_TELEPHONE`, `TZ`, `TERM`, `COLORTERM`, `NO_COLOR`, `PYTHONUTF8`, and `PYTHONIOENCODING`; and CA bundle variables `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, and `CODEX_CA_CERTIFICATE` only when their values identify absolute existing regular files. Environment names SHALL be matched case-insensitively on Windows and emitted with canonical names. Ambient proxy variables, `SSL_CERT_DIR`, `NODE_OPTIONS`, known provider/cloud credentials, cloud-route activation, any other `LC_*` name, and unknown future variables SHALL NOT enter the universe child. The child SHALL force `AWS_EC2_METADATA_DISABLED=true`.

The builder SHALL normalize a relative universe binding to an absolute canonical universe root. Before any public vault resolver or helper reads the selected source, it SHALL inspect `<universe>/.credential-vault.json` without following its final component. A missing source SHALL mean an empty vault. A present source SHALL be a physically contained, non-symlink regular file with exactly one hard link; a symlink, junction/reparse link, hardlinked or multi-link file, non-file, or physically outside source SHALL refuse provider launch before runtime or credential artifacts are created.

Before any provider-child directory creation or credential-helper side effect, the builder SHALL resolve every planned runtime target, the selected provider's existing/configured auth path returned by the public read-only vault resolver, and the selected provider's default `.credentials/<service>` materialization target. It SHALL refuse provider launch if any target physically resolves outside the canonical universe root, including through an existing symlink, junction, or reparse component.

After the complete preflight succeeds, the builder SHALL create private universe-owned home, profile, XDG, temporary, and runtime-only empty-auth roots beneath `<universe>/.runtime/provider-child/<provider>/`, with best-effort `0700` modes, and SHALL set `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_RUNTIME_DIR`, `TMPDIR`, `TMP`, and `TEMP` to absolute paths under those roots. It SHALL derive `HOMEDRIVE` and `HOMEPATH` only for a normal Windows drive path. Before credential overlay, it SHALL pin default `CLAUDE_CONFIG_DIR` and `CODEX_HOME` beneath the provider child's distinct `auth-empty` runtime root and SHALL NOT create `.credentials/*` for a universe with no credential record.

The selected universe's credential helper MAY overlay only `CODEX_HOME` and `OPENAI_API_KEY` for `codex`, or `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, and `ANTHROPIC_API_KEY` for `claude-code`. After the helper returns, the builder SHALL revalidate the complete overlay and SHALL refuse an auth-home path that physically resolves outside the canonical universe root. An unknown key, non-string value, malformed vault, outside-universe path, or unexpected helper failure SHALL refuse provider launch with a sanitized credential-resolution error that exposes neither secret values nor underlying exception text. A bring-your-own `llm_api_key` deposit SHALL be accepted only for a service that maps to a supported provider environment variable, and an unsupported service SHALL be rejected at deposit time.

This process-environment requirement does not claim network sandboxing or universal managed-identity isolation. It prevents ambient cloud-route activation and disables AWS EC2 metadata lookup for the child; other metadata-service and egress controls remain separate boundaries.

#### Scenario: Env overlay resolves the universe from the environment binding

- **WHEN** `TINYASSETS_UNIVERSE` binds a subprocess to a universe whose vault configures a Claude config directory
- **THEN** the child environment is first isolated to that universe and then gains the vault-selected `CLAUDE_CONFIG_DIR`

#### Scenario: Explicit universe directory wins over process binding

- **WHEN** the process environment points at universe A but a provider call explicitly supplies universe B
- **THEN** environment isolation and credential overlay use universe B and do not use universe A's vault

#### Scenario: Ambient direct and cloud authority cannot enter a universe child

- **GIVEN** the host carries direct Claude auth, Bedrock or Vertex activation, cloud keys, profiles, credential files, roles, or container credential endpoints
- **WHEN** a universe-scoped provider child is assembled
- **THEN** none of those ambient variables enters the child
- **AND** `AWS_EC2_METADATA_DISABLED` is exactly `true`

#### Scenario: Unknown future credential variables are default denied

- **GIVEN** the host carries a provider credential variable the current runtime does not recognize
- **WHEN** a universe-scoped provider child is assembled
- **THEN** that variable is absent because it was never explicitly admitted

#### Scenario: Safe runtime basics survive without ambient routing authority

- **GIVEN** the host carries required path, locale, terminal, timezone, a valid absolute CA bundle file, proxy variables, and arbitrary runtime injection variables
- **WHEN** a universe-scoped provider child is assembled
- **THEN** only the explicit safe runtime variables and valid CA bundle file enter the child
- **AND** proxy variables, `NODE_OPTIONS`, `SSL_CERT_DIR`, relative CA paths, directories, and missing CA files are absent
- **AND** an unrecognized name such as `LC_FUTURE_PROVIDER_MASTER_TOKEN` is absent

#### Scenario: Home profile and temporary discovery are universe owned

- **GIVEN** the host points home, profile, AppData, XDG, and temporary variables at host paths
- **WHEN** a universe-scoped provider child is assembled
- **THEN** every child discovery root points beneath that universe's private provider-child runtime root
- **AND** both default CLI auth homes point beneath that provider child's runtime-only `auth-empty` root
- **AND** no `.credentials/*` artifact is created and Claude subscription availability remains false

#### Scenario: Physical runtime path escape is rejected before writes

- **GIVEN** an existing symlink, junction, or reparse component sends a planned runtime root outside the canonical universe
- **WHEN** a universe-scoped provider child is assembled
- **THEN** provider launch is refused before any provider-child directory is created through that component

#### Scenario: Configured or default credential path escape is rejected before materialization

- **GIVEN** either the selected provider's configured auth path or default materialization target physically resolves outside the canonical universe
- **WHEN** a universe-scoped provider child is assembled
- **THEN** provider launch is refused before the credential helper creates or materializes anything at that outside path

#### Scenario: Linked vault source is rejected before credential work

- **GIVEN** `<universe>/.credential-vault.json` is a symlink, junction/reparse link, hardlink, or other multi-link source for credential data outside the universe's private vault file
- **WHEN** a universe-scoped provider child is assembled
- **THEN** provider launch is refused with a sanitized credential-resolution error before any public vault resolver reads that source
- **AND** no provider-child runtime or credential artifact is created

#### Scenario: Outside helper overlay path is rejected

- **WHEN** a provider helper returns a recognized auth-home key whose path physically resolves outside the canonical universe
- **THEN** provider launch is refused with a sanitized credential-resolution error

#### Scenario: Partial selected-universe overlay cannot retain alternate host authority

- **GIVEN** the host carries Claude and Codex authority
- **WHEN** the selected universe vault supplies only one recognized Claude or Codex overlay
- **THEN** that recognized universe-owned value survives
- **AND** no unrelated host authority enters through the empty-base environment

#### Scenario: Arbitrary helper output is rejected

- **WHEN** a universe credential helper returns any environment key not recognized for the selected provider
- **THEN** provider launch is refused with a sanitized credential-resolution error
- **AND** the arbitrary key and its value are not returned in a child environment or error

#### Scenario: Credential-resolution failure is fail-closed

- **WHEN** a universe-scoped vault import or resolver raises a malformed-data or unexpected error
- **THEN** provider launch is refused with an explicit sanitized credential-resolution error
- **AND** no environment containing inherited host authority is returned

#### Scenario: Host-local provider call keeps host authority

- **WHEN** a provider subprocess call has no explicit or environment-resolved universe
- **THEN** the environment builder returns the ordinary host environment under its normal API-key opt-in policy
- **AND** it does not invoke a universe vault helper

#### Scenario: Host-local API-key variables remain opt-in

- **GIVEN** the host carries `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `GEMINI_API_KEY`, `GROQ_API_KEY`, and `XAI_API_KEY`
- **WHEN** a host-local subprocess environment is built without explicit API-key-provider opt-in
- **THEN** all six variables are absent while host subscription authority remains available

#### Scenario: Noncanonical universe provider is rejected before credential work

- **WHEN** a universe-scoped caller requests `future-cli`, `gemini`, `CODEX`, or any provider name other than exact `claude-code` or `codex`
- **THEN** provider launch is refused before a vault helper is invoked

#### Scenario: Missing credential uses empty runtime auth

- **WHEN** a canonical universe provider has no credential record
- **THEN** environment construction succeeds with a universe-owned empty runtime auth home
- **AND** authentication failure occurs later at provider-call time without any maintainer authority

#### Scenario: Unsupported bring-your-own service is rejected at deposit

- **WHEN** a founder attempts to deposit an `llm_api_key` for a service that does not map to a supported provider environment variable
- **THEN** the deposit is rejected with an error naming the supported services and no unusable key is written to the vault

### Requirement: Credential alias selection and first-record secret extraction are exact
The system SHALL derive a credential record's effective service by taking non-empty `service` before non-empty `provider`, then trimming and lowercasing its string form. For `llm_api_key` records, `anthropic`, `claude`, and `claude-code` map to `ANTHROPIC_API_KEY`; `openai` and `codex` map to `OPENAI_API_KEY`; `gemini` and `google` map to `GEMINI_API_KEY`; `groq` maps to `GROQ_API_KEY`; and `xai` and `grok` map to `XAI_API_KEY`. A BYO-key lookup SHALL scan records in stored order and return from the first `llm_api_key` record whose normalized effective service maps to the requested environment variable. From that record it SHALL return the first non-empty string `api_key`, `key`, or `token`, otherwise the decoded string from a truthy `token_b64` before `secret_b64`; if the selected value is not a non-empty string or decodes empty, resolution SHALL return empty without scanning later matching records. Claude OAuth resolution SHALL likewise inspect only the first `llm_subscription` record whose normalized effective service is `claude`, returning its first non-empty string `oauth_token` or `claude_code_oauth_token`, otherwise its selected base64 field, and returning empty without scanning later matching records. Base64 resolution SHALL use the runtime's permissive standard decoder followed by UTF-8 decoding and whitespace trimming: ignored non-alphabet characters are not independently rejected, while an actual base64 or UTF-8 decoding exception SHALL be surfaced as `ValueError`.

#### Scenario: Normalized BYO aliases select their environment variable
- **WHEN** an `llm_api_key` record uses one of the ten supported effective-service aliases with any letter case or surrounding whitespace
- **THEN** it is eligible only for the environment variable named by the exact alias table

#### Scenario: Provider supplies the effective service when service is absent
- **WHEN** an `llm_api_key` record omits or empties `service` and names a supported alias in `provider`
- **THEN** BYO-key lookup uses that `provider` value as the effective service

#### Scenario: Empty first BYO match shadows later records
- **WHEN** the first `llm_api_key` record mapped to the requested environment variable has no supported string secret and a later mapped record does
- **THEN** BYO-key resolution returns empty without inspecting the later record

#### Scenario: First Claude subscription yields a direct or base64 secret
- **WHEN** the first effective-service `claude` subscription contains a direct OAuth field or a decodable `token_b64` or `secret_b64`
- **THEN** Claude OAuth resolution returns that record's first available secret in the specified order

#### Scenario: Empty first Claude subscription shadows later records
- **WHEN** the first effective-service `claude` subscription has no supported secret and a later matching subscription does
- **THEN** Claude OAuth resolution returns empty without inspecting the later record

#### Scenario: Selected base64 decoding exceptions fail loudly
- **WHEN** a selected BYO-key or first matching Claude subscription record has no supported direct secret and standard base64 or UTF-8 decoding of its selected `token_b64` or `secret_b64` raises
- **THEN** resolution raises `ValueError` without returning empty or scanning a later record

#### Scenario: Unknown effective service does not resolve
- **WHEN** an `llm_api_key` record's normalized effective service is absent or not in the exact alias table
- **THEN** that record does not satisfy any provider environment lookup

### Requirement: Credential vault replacement is process-local and unversioned

The system SHALL treat a validated one-record payload written to an existing vault as a logical-slot upsert and SHALL treat an empty or two-or-more-record payload as an exact ordered replacement. Every successful write SHALL pass through the fixed sibling path `.credential-vault.json.tmp` and replace `.credential-vault.json` directly from that path. Its non-secret summary SHALL report the number of redundant matching records collapsed and descriptors for any VCS purpose slots removed by a narrowing upsert. This boundary SHALL NOT claim cross-process locking, a unique temporary filename, compare-and-swap, or version conflict detection.

#### Scenario: Single record upserts into an existing vault

- **WHEN** a valid one-record payload is written while `.credential-vault.json` exists and is valid
- **THEN** the system reads the stored records, replaces all records matching the incoming logical slot with one result at the first matching position, preserves unmatched records in order, and appends the incoming record when no slot matches

#### Scenario: Logical slots follow resolver selectors

- **WHEN** the system matches a record for a one-record upsert
- **THEN** `llm_api_key` uses credential type plus the environment-variable slot selected by normalized effective-service aliases, `llm_subscription` and `social` use credential type plus normalized effective service, and `vcs` uses credential type plus normalized effective service plus exact destination plus an overlapping normalized purpose set

#### Scenario: VCS purpose selectors overlap

- **WHEN** an existing VCS record and an incoming VCS record have the same service and destination and their selectors share at least one purpose, including a stored `purposes` list that contains the incoming singular `purpose`
- **THEN** the records match one logical slot, the incoming whole record replaces all overlapping matches, and a first stored token cannot shadow the deposited rotation

#### Scenario: VCS narrowing reports removed purpose slots

- **WHEN** a one-record VCS upsert replaces an overlapping record whose normalized purpose set contains selectors absent from the incoming record
- **THEN** the write summary identifies the removed purposes with credential type, normalized service, exact destination, and sorted purpose names without including any secret value

#### Scenario: Subscription partial writes preserve sibling fields

- **WHEN** one `llm_subscription` record is upserted into one or more matching subscription records
- **THEN** stored fields are combined with first-record precedence, stored members of any Claude or Codex resolver-equivalent alias family named by the incoming record are removed, incoming fields are applied, unrelated sibling fields survive, and all matching records collapse to the combined record

#### Scenario: Single upsert cleans duplicate resolver slots

- **WHEN** exact bulk replacement has stored multiple matching BYO-key or Claude-subscription records whose first record shadows later records
- **THEN** their existing first-record resolution semantics remain in effect until a one-record upsert for that logical slot collapses every match to one record and reports the number of redundant records removed

#### Scenario: Bulk write replaces the vault exactly

- **WHEN** a valid payload contains two or more credential records
- **THEN** the stored list is replaced by that payload in order, including any duplicate logical slots, without merging it with prior records

#### Scenario: Empty write clears the vault

- **WHEN** a valid payload contains zero credential records
- **THEN** the stored list is replaced with an empty list

#### Scenario: Malformed existing vault blocks single-record upsert

- **WHEN** a one-record payload targets an existing vault whose JSON or credential records are malformed
- **THEN** the write raises `ValueError` before replacing the malformed vault

#### Scenario: Successful write replaces the vault through the fixed sibling

- **WHEN** a valid credential payload is written without an overlapping writer or filesystem error
- **THEN** `.credential-vault.json.tmp` is written and directly replaces `.credential-vault.json`

#### Scenario: Concurrent writers have no serialization guarantee

- **WHEN** two processes write the same universe vault concurrently, including overlapping one-record read-modify-write upserts
- **THEN** the boundary provides no lock, unique temporary path, compare-and-swap check, lost-update prevention, or deterministic winner guarantee

### Requirement: Credentialed git runs from an empty environment through an in-memory broker with the transport address pinned, never in a directory user code can write

The outbound worker SHALL run every credentialed git operation against a worker-private staging repository with the child's environment built from empty and the token supplied only by an in-memory credential broker that answers `get` for the grant's exact `(protocol, host, path)`.
The environment SHALL set `GIT_CONFIG_SYSTEM` and `GIT_CONFIG_GLOBAL` to
the null device, `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`,
`GIT_ASKPASS` to a false binary, an empty `HOME`, no `GIT_TRACE*`, and
`RLIMIT_CORE=0`; the command SHALL force `core.hooksPath` to the null
device, `core.fsmonitor=false`, an emptied then trusted
`credential.helper`, `credential.useHttpPath=true`,
`protocol.allow=never`, `protocol.https.allow=always`,
`http.followRedirects=false`, `submodule.recurse=false`,
`transfer.fsckObjects=true`, and `http.curloptResolve=<host>:443:<addresses>`
where the host is lower-cased, every address was validated public unicast
by the outbound driver's classification (a mixed answer is a refusal),
IPv6 addresses are bracketed, and all validated addresses are given in one
rule when the runtime libcurl is 7.59 or newer (checked once at worker
start, fail-loud) — otherwise one validated address per whole operation,
with a push retry reconciling the remote ref before sending again. The
command SHALL address the validated canonical URL, never a stored remote.
The broker SHALL answer `username` and `password` for as many `get`
requests as one operation issues (a 401 retry is legitimate), SHALL ignore
`store` and `erase`, SHALL refuse any other host or path, and SHALL be torn
down when the operation ends; the token SHALL never appear in argv, in the
environment of any process, in any file, in evidence or in an error
message. Raw git stderr SHALL be scrubbed by exact-secret detection and
mapped to fixed error classes.

#### Scenario: the token is not in argv, environment, files or evidence
- **WHEN** a checkout runs against a repository whose credential is `tok-…`
- **THEN** no process's `cmdline` or `environ` contains it, staging and the lease contain no file with it, and evidence and error messages contain none of it

#### Scenario: a retried authentication still succeeds
- **WHEN** the remote answers 401 once and git asks the broker a second time
- **THEN** the broker answers again for the same repository and the operation completes

#### Scenario: a broker request for another repository is refused
- **WHEN** git asks for a credential for a host or path other than the grant's repository
- **THEN** the broker answers nothing and the operation fails as `workspace_checkout_failed`

#### Scenario: a mixed DNS answer is a refusal, and IPv6 is pinned correctly
- **WHEN** the host resolves to one public IPv4 address and one private address, or to public IPv6 addresses only
- **THEN** the mixed answer is refused as `workspace_checkout_failed` before any connection, and the IPv6-only answer yields a resolve rule with bracketed addresses for exactly `<host>:443`

### Requirement: Realtime voice authority is universe-scoped and credential-blind
The voice capability declaration, capability check, and session broker SHALL use only the current provider's existing generic HTTP connection and grant bound to the authenticated owner's universe. Capability metadata SHALL NOT grant or widen credential authority. The voice route SHALL NOT resolve or receive the long-lived connection credential; credential resolution and request signing remain inside the existing credential-blind broker child. The response to the app SHALL contain only a validated SDP answer and bounded, non-secret session metadata.

Capability metadata SHALL be stored in a `connection_capabilities` table keyed by `(connection_id, capability_kind)` and linked to the owning connection. Connection deletion SHALL remove its capability rows in the same transaction before the deterministic connection id can be reused.

#### Scenario: Compatible current provider exchanges bounded signaling
- **GIVEN** generic outbound HTTP transport is enabled and the authenticated owner's current provider has a valid realtime capability on its active HTTP connection and grant
- **WHEN** the owner requests a voice session
- **THEN** the broker sends the provider-neutral session policy and bounded SDP offer through that exact credential-blind connection
- **AND** it returns only a validated, bounded SDP answer and session metadata
- **AND** the response contains no long-lived credential

#### Scenario: Capability declaration cannot widen a grant
- **WHEN** the owner declares realtime capability metadata for an existing provider connection
- **THEN** TinyAssets requires the session endpoint and `POST` method to be present in the connection's existing allowlist and scopes
- **AND** it does not mutate credential custody, endpoint authority, the universe grant, or serving selection

#### Scenario: Credential custody rotates without a new declaration
- **WHEN** the current connection or grant no longer passes the canonical serving-authority credential digest check
- **THEN** Voice status and session creation fail closed
- **AND** a stale capability row cannot authorize signaling with the rotated credential

#### Scenario: Ambient credential is present but current provider capability is absent
- **GIVEN** one or more process-global service credentials exist but the current provider has no valid realtime capability
- **WHEN** the owner requests a voice session
- **THEN** capability is reported as unavailable and the broker fails before a network request
- **AND** it does not use an ambient credential, platform connection, another universe's grant, or another provider

#### Scenario: Conversation engine authority exists without voice authority
- **GIVEN** a universe has a working assigned writer whose current provider exposes no compatible realtime capability
- **WHEN** the app checks Voice capability
- **THEN** Voice is reported as unavailable while the writer remains available
- **AND** TinyAssets does not request a second credential, substitute platform authority, or disturb writer routing

#### Scenario: Secret-bearing paths are non-observable
- **WHEN** capability configuration or session signaling succeeds or fails
- **THEN** application logs, traces, exceptions, capability rows, and conversation history contain no long-lived connection credential
- **AND** the HTTP response is marked not cacheable

### Requirement: A same-account rotation carries custody forward
The system SHALL keep a credential custody reference, and every authority record that pins it, unchanged when the platform itself refreshes the exact pinned subscription document, updating only the custody row's byte pin by compare-and-swap inside the same exclusive vault hold as the byte write; an owner deposit, an adopted on-disk rotation, a changed account identity, or a schema-version-1 custody row SHALL renew the accepted source instead.

#### Scenario: Platform refresh during an in-flight run
- **WHEN** a run holds a receipt and the platform rotates the same account's sign-in
- **THEN** the run's next launch succeeds on the rotated bytes and no binding, assignment or receipt is republished

#### Scenario: Different account
- **WHEN** the rotated document names a different account, or the rotation was adopted from a CLI home rather than performed by the platform
- **THEN** the accepted source renews as before and in-flight receipts are refused

#### Scenario: Bytes nobody pinned
- **WHEN** the stored bytes differ from the custody byte pin without a carry-forward
- **THEN** custody reads and launch snapshots refuse
