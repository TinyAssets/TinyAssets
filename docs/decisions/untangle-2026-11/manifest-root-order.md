# Manifest roots across a retention change

R7 requires Files to remove a generation's root before deleting any object it names. This lets recovery inspect each visible root. The owner reads its current retained generations again on every page. These rules interact when an export expires between pages. An earlier page may have kept a root whose later parts the next page now deletes.

## What the owner and Files did

The comparison used main `f934871ad` and the local combined tree `eff491215` under Node 22.23.5 and 24.22.3. Each tree supplied its eighteen migrations, SQLite WriteGate, ChangeLog, Backups.run and FilesService.sweepManifests. An ordered in-memory bucket held the two generation roots and returned up to the contract's 1,000 keys per page. The earlier finished generation had 1,003 parts, the newest had a later finish time. The clock advanced two seconds during each list response.

| Export on the earlier generation | Earlier root after the first pass | Earlier parts remaining | Deleted while that root was visible |
| --- | --- | ---: | ---: |
| Expires between the two pages | Present | 999 | 4 |
| Remains open | Present | 1,003 | 0 |
| Expires before the first page | Absent | 0 | 0 |
| No export | Absent | 0 | 0 |

All sixteen executions produced these results. In the first case the retained set changed on the second page, after the first page had passed the root. The newer sweep obligation survived the first pass. A subsequent pass removed the root and remaining parts. This demonstrates eventual deletion under this fixture. The root still named missing parts throughout the interval between passes. The established Files regression supplies the same retained set to every page.

## What the off-account copy observed

A separate fixture used both commits on both Node versions. Files wrote and opened actual sealed roots and parts with a synthetic key. The newer generation also had a readable header in part zero. The copy started from a complete prior set of 1,006 objects. The source reflected the interval above.

The original nightly `platform/deploy/offsite.ts` copy ran eight actual local rclone commands per commit and Node version. Synthetic responses supplied the required bucket configuration. Each complete object name mapped reversibly to one local filename because POSIX cannot represent a file and a directory at the same path. Rclone performed the original listing, sync, size and byte checks on the stored bytes. All four invocations completed with 1,002 objects and 157,535 bytes on each side, four prior objects absent, and sixteen sampled objects byte-equal. The copied older root opened and named 1,003 parts. Reading parts 996 through 999 through Files gave the missing-object error in all four cases. The actual namespace locator read the newer generation's header and returned that generation. This comparison exercised copying and reading a partial older generation. No owner restore or live service ran.

## Correction and remaining proof

The owner owns retention. Files owns key validation, listing and deletion. A root passed and kept on an earlier page must be gone before a later deletion removes any of its parts or segments. Prove the ordering at deletion time on a changing retained set, with an interrupted deletion and a lost result. Also preserve rootless orphan cleanup, bounded batches and the retained current generation.

Coordinate this with the durable cursor design in `manifest-sweep-progress.md`. Keeping a newer obligation outstanding provides another sweep, but another sweep comes too late to preserve the earlier root's meaning. A cursor resumed after eviction must preserve the same ordering. The restore chunk sweep stores a last returned chunk name in its namespace-scoped restore record. Chunks have no root-to-part order, so adopting that cursor convention alone would miss this condition.

## Candidate and interrupted deletion

On `180e98f58` a continued page beginning inside a now-disposable generation also retires that generation's root before deleting its listed children. In the same sealed-object fixture the original nightly copy ran twice under each Node version, eight real local rclone commands per run. In all four copies the old root was absent and the newest generation's part zero decrypted and located its owner.

- In the uninterrupted case the first pass removed the old root and four parts. The current copy held 1,001 objects, including 999 remaining old parts. The next pass cleared those parts.
- In the interruption case the injected parts deletion failed after the root deletion succeeded. The current copy held 1,005 objects and the old generation's 1,003 parts. An immediate retry issued no list. At the stored retry time a pass removed all 1,003 old parts and settled the obligation.

All four runs ended with the newest root and its part zero. The successful continuation reported four deleted objects from its list. Five distinct keys left the source because the operation also removed the root it had passed earlier. Backups.run consumes only the cursor. Any new caller using the count needs its page-local meaning.
