## Context

PR #4574 already accepts an exact release-critical count in the trusted Drain-Review receipt. The founder's token is shared by automated actors, so GitHub attribution is not owner consent. Existing pending requests, immutable consent pins, the protected owner cookie, and the broker UID boundary supply the required primitives.

## Goals / Non-Goals

Require founder architectural agreement for over-cap PRs while retaining every existing guard rule. Reuse app approvals and the provider-neutral broker. Do not introduce a feature editor, an agent approval capability, or a GitHub-comment trust exception.

## Decisions

1. `architecture_approval` is a fieldless consent action frozen in the existing protected pending-request store (outside the owner cell). It contains repository, positive PR number, head SHA, Drain-Review diff key, sorted unique release-critical file list and exact count, plus four briefing fields: changes, direction, risks, rollback. Platform-written text displays all of these and states that approval means the owner was briefed and agrees this advances README Direction. Arbitrary agent titles cannot substitute for that disclosure. The existing displayed-row digest rejects changes after capture. No second package-pin store or migration is needed.
2. The existing protected `/app/approvals/answer` route supplies owner proof. Bearers, MCP, agents and other apps cannot supply it. The action additionally compares the authenticated session owner with the founder configured in broker-private state. Decline never signs. There is no standing approval.
3. A typed, fenced owner-channel broker operation signs canonical UTF-8 JSON with Ed25519. A broker-private key/config file holds the signing key, repository and founder owner ID. It is never returned or inherited by agents. Broker SQLite stores issued results by request ID for idempotence; retries do not extend expiry. Missing configuration refuses approval rather than creating a disposable key.
4. The signed payload has a versioned purpose, repository, PR, original head SHA, diff key, count, file-list SHA-256 digest, approver owner ID, approved_at and expiry (24 hours). The nonempty diff key is authoritative so unchanged rebases survive; any changed diff or file set voids approval. Canonical JSON and file-list hashing are fully specified in the verifier tests.
5. GET `/app/attestations/architecture/{pr}` returns the latest signed result for the configured repository, without briefing or credentials. This uses the existing apex app proxy route without a DNS/Worker change. Only the protected approval executor can issue one. CI fetches from a fixed platform URL and verifies with the committed trusted-base public key, never a response-provided key. Repo, PR, purpose, binding, count, digest, owner, timestamps and signature must all match. Fetch/parsing/config/signature failures deny.
6. The default cap remains eight. Above it both the existing cited exact-count receipt and signature are required. The API cap, forbidden paths, labels, review and hygiene remain unchanged.

## Risks / Trade-offs

- Availability of the app/broker is required for new over-cap approvals; missing or expired proof denies with instructions to raise the ask.
- The first signing key and founder identity must be provisioned in broker custody and its public trust record committed. Rotation requires a reviewed trusted-base update; a PR cannot substitute its own key.
- Approvals survive only unchanged diff keys, not arbitrary history rewrites. The original SHA remains audit information when the diff key matches.
- The public result discloses the approving owner ID and scope digest, as explicitly required. Briefing and file list remain in protected requests.

## Migration Plan

Provision broker-private signing configuration, commit the public trust record, deploy the app/broker, then activate the trusted-base guard on merge. No existing table migration. Rollback the issuer independently; the guard fails closed until issuance is restored. This builder pushes for review and does not stamp a Drain-Review receipt or merge/deploy the PR.

Key provisioned 2026-10-10 directly in the production broker's `/data/.broker/state/architecture-signing.json` as UID 1002, mode 0600, inside the UID-1002-only mode-0700 state directory. Only the public key was returned, recorded in `.github/architecture-approval-key.json`; the private key never left broker custody. Founder ID matches the established authenticated founder in `docs/ops/2026-08-04-cloud-drain-handoff.md`. No manual founder setup remains. Preserve the broker directory in backups; rotation requires generating a replacement there and updating the trusted-base public trust record together. Provisioning issued no attestation and changed no running service.

## Cross-family review

Claude reviewed the implementation and new files on 2026-10-10, including owner-session gating, protected request storage, complete briefing rendering, typed broker RPC, signing custody, verifier binding and trusted-base workflow. Verdict: APPROVE; no floor/correctness findings. AGREE: the existing protected store provides the frozen consent record, only the authenticated protected owner route can reach issuance, and the broker independently restricts the configured founder/repository. Public proof includes the required approver owner ID; it excludes briefing and key material. Generated plugin copies are included. Post-deploy public fetch availability remains an operational check; unavailable/challenged reads fail closed. This review is not a Drain-Review receipt.

## Raising the request

Above eight release-critical files, the cited approval comment must declare
`Drain-Review-Release-Critical: <exact count>` as its third nonblank line, and
the founder must approve the architecture in the TinyAssets app. Raise
`write_graph target="pending_request" operation="ask"` with this action:

```json
{
  "action": {
    "type": "architecture_approval",
    "repo": "TinyAssets/TinyAssets",
    "pr": 4574,
    "head_sha": "<40-character head SHA>",
    "diff_key": "<python scripts/drain_review_gate.py --print-diff-key origin/main HEAD>",
    "release_critical_count": 9,
    "release_critical_files": ["<all nine exact paths counted by the guard>"],
    "briefing": {
      "changes": "What changes",
      "direction": "Why this advances the core architecture in README Direction",
      "risks": "Concrete risks",
      "rollback": "How to recover"
    }
  }
}
```

The platform supplies the sheet text and requires the protected founder session;
chat replies, bearer tokens and GitHub comments cannot approve. CI reads the
Ed25519 proof at `https://tinyassets.io/app/attestations/architecture/<pr>` and
verifies against `.github/architecture-approval-key.json` from the trusted base.
Proof expires after 24 hours; changed diff keys or file sets need a new ask.
Unchanged rebases retain agreement. After approval, rerun the scope check.
