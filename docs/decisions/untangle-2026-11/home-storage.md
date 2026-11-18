# Owner app storage and account doors as built (2026-11-18)

On main `f934871ad` and combined `533733c40`, the owner's Home page reconstructed
usage from chunk and inline file bytes, dropping inline collection and mail bodies. It also
used the plan's full allowance when the owner had set a lower cap. Your data repeated the
sum and called all of it file bytes. Chunks can also store placed mail or collection bodies.

The existing `/app/api/account` response returns the owner's `fileUsage` result. Both pages
now read its `stored` field. Home reads `usage.limits.max_bytes` for the limit in force and
still shows usage when no limit is known. The plan table continues to show tier allowances.
Your data presents total storage and the file and folder count as separate figures.
The owner counts entries across both path roots, including temporary folders. No extra
request is needed. The storage detail door reads the same owner values and adds its breakdown
and `deleted_content`. Bytes held for deleted-content recovery are outside the used total.
The retention horizon is a maximum. An owner lowering the limit can shorten recovery.

Offline Chromium executed the actual app modules with local account and notice responses.
Mixed-content bytes, an owner cap below the plan and missing tier numbers each exposed a
display error on the original Home. The candidate passed those three and three data-page
renders. The regression in the app suite writes an inline collection row and caps a test owner
at exactly the measured usage. Both values shown on Home must match the amount shown on
Your data. It restores the policy afterwards.

## Remaining Step 19 consumers

- **Export.** The app previously said its export door was being built. The door returns NDJSON.
  The screen now describes the action still missing from the app.
  `doors/app/api.js` only parses JSON and otherwise returns null, so pointing an existing
  `call` at `export` would drop a successful response. The client needs to preserve its
  authentication/refresh handling when reading a stream. Continue from the recorded cursor
  and export ID through pending pages and interruptions. Verify the terminal record, fetch
  the named chunks while the point-in-time generation is pinned, and end the export through
  `export/end`. See `doors/src/app.ts` and `owner/src/export.ts`. The owner's export
  is credential-free by design. The site and app should describe only the export they
  actually deliver.
- **API keys.** The screen currently lists active grants and turns them off with
  `grants/revoke`. This does invalidate the grant and delete the directory key. It hides
  revoked keys and does not let the owner forget one. The owner's `keys/list` is the
  source for the key ID, name, creation, last use and revocation. Use `keys/delete` for
  permanent removal: the job revokes, removes the directory entry and then forgets the
  grant. When directory deletion fails, it keeps the revoked record for retry. Revocation
  frees the active-key slot. See `doors/src/api-keys.ts`, `revoke.ts`,
  `owner/src/keys.ts` and `grants.ts`.
- **Model sources.** A source card renders `connections/list`. `ConnectionView` records
  creation and revocation, with no last-use timestamp. The key store's coarse last-use
  applies to a caller's platform key. The model-call receipts in `owner/src/usage.ts`
  are bounded, dated reservations; a retained reservation does not establish the last
  successful use of a source across all paths. Define whether the requested date means
  attempted use or successful use and its credential scope before choosing a recorder.
  An owner connecting before that definition may have an unknown historical date.
