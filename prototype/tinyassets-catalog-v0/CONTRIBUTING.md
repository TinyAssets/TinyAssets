# Contributing to TinyAssets-catalog/

Every artifact in this repo is **automatically exported from the
TinyAssets Postgres database** by `tinyassets-catalog-bot[bot]`. You
can also contribute edits via pull request — the bot validates,
a human reviews, and on merge the change rounds-trip into Postgres.

## License (CC0-1.0)

All content in this repository is under **CC0-1.0**. By opening a
pull request you affirm:

1. Your contribution is your own work OR is sourced from CC0-compatible
   material with proper attribution-notes (attribution is a courtesy,
   not a CC0 requirement).
2. You grant permanent, worldwide, royalty-free rights to everyone.
3. You're responsible for checking that your contribution doesn't
   contain anyone's private-instance data — file paths, credentials,
   company-specific values. Those belong on the owner's host, not in
   this repo.

## DCO sign-off

Every commit must carry a `Signed-off-by: Your Name <email>` trailer
per the [Developer Certificate of Origin](https://developercertificate.org/).
Use `git commit --signoff` or `git commit -s` to add it.

We use DCO instead of a CLA — it's lighter-weight and keeps
contributions permissionless.

## How to contribute a change

1. **Fork this repo** on GitHub.
2. **Edit the YAML** for the node / goal / branch you want to change.
   Files live under `catalog/<kind>/<id>.yaml`. Stick to the YAML shape
   you see — the validator Action checks for allowlisted fields only.
3. **Bump the `version:` field** by exactly 1. The ingest RPC checks
   `new_version == current_postgres_version + 1`. Stale or skipped
   versions get rejected.
4. **Commit with `-s`** for DCO sign-off.
5. **Open a PR** against `main`. The `validate-pr.yml` Action runs:
   - YAML schema valid?
   - License matches `CC0-1.0`?
   - No private-instance-looking fields?
   - Structural hash matches your base version?
   - Your GitHub handle is linkable to a TinyAssets user?
   - `version = base + 1`?
6. If green, a human reviewer (tier-3 OSS contributor) approves + merges.
7. The `ingest-on-merge.yml` Action calls the control-plane RPC. Within
   seconds, your change is live in Postgres + visible on `tinyassets.io`.

## Race conflicts

If someone else merged a change to the same artifact between your PR
open and merge, the ingest RPC returns `race_conflict`. A revert-bot
opens a counter-PR + comments with rebase instructions. Rebase and
resubmit.

## What NOT to PR

- Private-instance data (even if it's yours — use the chatbot or web
  editor for that, not this repo).
- Credentials, paths, any T2-taxonomy content (see
  `privacy-principles-and-data-leak-taxonomy.md (archived in git)` in
  the main repo).
- Non-CC0-compatible content.

## Questions

- Read `2026-04-18-export-sync-cross-repo.md (archived in git)` in the main
  repo for the full sync pipeline.
- Open an issue here for catalog-specific questions.
- Open an issue in the main repo for platform questions.
