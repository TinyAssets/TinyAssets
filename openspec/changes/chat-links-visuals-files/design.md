## Context
Replies currently use textContent. The owner door serves UI assets but no generic file download. universe_file_reads already provides admin ACL checks and anchored link-free reads.
## Goals / Non-Goals
Deliver safe links, inline visuals and private folder files. Exclude request answering, workflow changes and public sharing.
## Decisions
Use DOM text nodes and HTTP(S)-only anchors. Render chart JSON as SVG with no agent code. Load a pinned Mermaid bundle under the existing nonce policy, strict mode; reject diagram configuration directives, display sanitized SVG as an inert image, and preserve code on failures. Add POST /app/api/file with graph_id and path, owner session required, calling the existing owner folder authorization, logical workspace mapping and anchored reader. Return opaque attachment bytes with no-store/nosniff/sandbox headers. Chips use a fenced file JSON object with path/name and capture owner/home/login epoch; no tokens in URLs. A missing file remains an explicit download error. Existing messages need no storage migration.
## Risks / Trade-offs
Files are read at click time, so later edits are reflected. Reads keep the existing folder byte bound. Mermaid load/render failure preserves source. Rollback reverts renderer and endpoint; stored reply text remains readable.

## Cross-user guard mutation table
| Mutation | Test that must fail |
| --- | --- |
| Remove owner ACL admission | test_owner_downloads_exact_workspace_bytes_and_other_user_cannot |
| Treat public visibility as folder permission | test_public_visibility_does_not_grant_file_access |
| Follow a file link | test_link_cannot_download_other_folder (Linux oracle) |
| Permit traversal | test_invalid_and_missing_paths_are_indistinguishable |
| Save after login changes during body read | test_account_change_during_body_read_prevents_download |

Mermaid 11.12.0 is vendored from https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js with its MIT license, served through the existing content-keyed app module route and checked by SHA-384 SRI. No external CDN request or CSP relaxation is needed. Source: https://mermaid.js.org/config/usage (strict mode and render API).
