## Context

Initial provider: codex. Worktree `wf-dirlist`, branch `feat/connector-directory-listings`, base main. The public endpoint exposes seven multiplexed handles; directory review evaluates the whole handle, not only its safest operation.

## Goals / Non-Goals

Prepare truthful directory submissions and fix metadata. Do not change tool names, owner isolation, authorization, or accept platform terms for the founder.

## Decisions

- Mark destructive-capable writes destructive; delegated execution and public commons writes open-world. Keep annotations conservative across every routed operation.
- Use existing ordinary sign-in with separate reviewer owners and synthetic data. No founder account, shared founder credentials, special demo bypass or fake successful execution.
- Keep unpublished requirements and unverified company/security claims explicit in the packet. Existing draft terms and plaintext-at-rest disclosures are readiness gaps, not facts to conceal.
- Existing auth metadata advertises WorkOS DCR and S256; do not invent CIMD support or replace the identity provider merely for directory listing.

## Risks / Trade-offs

Broader hints may increase confirmation prompts; truthful consent outweighs convenience. Metadata does not enforce authorization. Reviewers may reject the general-purpose delegated surface or require narrower tools; that needs a separate spec decision, not hidden changes to this seven-handle contract.

## Verification and handoff

Metadata and affected MCP tests, ruff, structural guards, plugin rebuild, hygiene gate and public canary. The packet owns remaining live review prerequisites. Founder performs company verification and accepts terms. This lane prepares a PR, not a deployment or directory approval.
