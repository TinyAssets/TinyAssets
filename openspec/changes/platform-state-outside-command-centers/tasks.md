## 1. Decided

- [x] 1.1 **Carry nothing** (lead, 2026-10-03; the founder may override). A row
      carried forward is indistinguishable from the forgery this change exists
      to prevent, and the standing preference while early is a clean cutover
      over a compatibility shim. I had recommended the opposite; the decision is
      the stronger reading. The cost -- every consent re-granted -- is paid down
      by the re-ask being the ordinary one-click ask at first use with a reason
      attached (design D3), not a migration step or a batch.

## 2. Move the consent database (closes the live authority hole)

- [ ] 2.1 `storage/effector_consents.py`: `consents_db_path` returns
      `<data>/.universe-sidecars/<cc>/.effector_consents.db`. Nothing else in
      the module changes.
- [ ] 2.2 Migration in `storage_layout.py`, under the exclusive lock, with the
      marker refusing a pre-move image: idempotent, resumable, and refusing a
      command center that has both copies (design D3). The in-folder file is
      renamed aside, not deleted.
- [ ] 2.3 The re-ask wording: the ordinary consent ask, raised at first use of
      each effect, saying it is a one-time re-confirmation after a security
      move. No new surface, no batch, no pre-emptive prompt -- and check that an
      unattended automation's wait is visible rather than silent.
- [ ] 2.4 Tests: a command center that pre-creates the in-folder database gains
      no consent; a consent granted before the move does not survive it and the
      old file is renamed aside; the first use raises the ordinary ask for the
      same sink and destination; a link planted at the old name changes no
      answer; the migration run twice is a no-op; both-copies refuses by name;
      killed mid-way it resumes. Linux oracle, since the link cases are
      POSIX-only.

## 3. Enumerate and follow (the cleanup, and what stops a recurrence)

- [ ] 3.1 The enumeration test (design D4): no platform path helper resolves
      inside a command-center folder, allowlisting the `.runtime` snapshots a
      provider child reads through its jail. This is the task that produces the
      real inventory -- `.runs.db` is already confirmed
      (`api/resource_usage.py`, `api/storage_observations.py:135`).
- [ ] 3.2 Move each store the enumeration finds, one commit per store, each
      with its own migration step and its own test.
- [ ] 3.3 Followers, derived from the same enumeration rather than listed:
      account deletion / `scoped_reset.py`, `storage_accounting.py`,
      `deploy/backup.sh`'s set. Assert deletion leaves no sidecar.
- [ ] 3.4 Cross-family refute of the migration specifically -- the path change
      is small, the one-way move over every command center is not.

## 4. Land

- [ ] 4.1 Prod: `deployed_sha.py --assert-contains`, plus a read-only check
      that the sidecar directory holds the consent database and the
      command-center folders no longer do.
- [ ] 4.2 Delete `docs/concerns/2026-10-03-a-universe-database-name-steers-the-
      daemon.md` and `docs/concerns/2026-10-01-platform-state-inside-the-
      universe-dir.md`, which this change resolves; spec sync and archive.
- [ ] 4.3 Follow-up, not part of this change: once
      `openspec/changes/per-role-uid-split` lands, a sticky bit on the parent
      stops a child uid renaming a daemon-owned entry. Useless before it
      (command-center processes share the daemon's uid), cheap after.
