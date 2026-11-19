# Progress across bounded manifest sweeps

R7 keeps generations that the owner can still use. Files lists at most 1,000 manifest keys per page and deletes only objects outside the current retained set. A kept generation can contain 16,384 parts and segments. The owner can therefore encounter several pages consisting entirely of keys that must stay.

At f934871ad and combined c3b33a4e1, Backups.run begins with a prune and an owed sweep. Its sweep keeps the listing cursor in the current invocation. If the 45-second lane budget expires before the next page, the pass returns with the sweep still owed. The next pass starts listing at the beginning. When the retained prefix takes the whole budget to read, the first orphan beyond it survives every retry. A nightly generation already due behind the sweep also fails to start.

## Measured cases

Each row ran on main and the combined tree under Node 22.23.5 and 24.22.3. The fixture used the eighteen owner migrations in SQLite, WriteGate, ChangeLog, Backups.run, and FilesService.sweepManifests. A synthetic bucket implemented ordered prefix listing, cursor and deletion. The clock advanced by a chosen interval per list response. These intervals are fixture inputs. Production prevalence and latency are unknown.

| Retained keys preceding one orphan root | Seconds per list | Passes | Lists | Result |
| ---: | ---: | ---: | ---: | --- |
| 3,000 | 16 | 5 | 15 | The same three pages were read five times. |
| 3,000 | 1 | 1 | 4 | The orphan was removed and the nightly generation opened. |
| 15,000 | 3 | 5 | 75 | The same fifteen pages were read five times. |
| 15,000 | 2 | 1 | 16 | The orphan was removed and the nightly generation opened. |

The retained keys were a root and its parts, within the generation limit. The owner adopted the generation two days before the test, and the write gate recorded a later change. Clearing only the sweep count in a rolled-back savepoint confirmed the generation itself was due. In each repeated pass, the first list started at the beginning, the cursor sequence repeated exactly, the orphan remained, the sweep count stayed at one, and the next scheduled time was immediately due. The fast controls verified deletion through the Files method and entry into writing state. Subsequent capture storage was deliberately omitted. Completing a generation is outside this measurement.

## Dependency for the correction

The completion evidence must account for every page and survive yielding to the lane budget or owner eviction. Each page still needs the current retained set. If another prune owes a sweep during a listing, keys before its cursor may newly be disposable, so that new obligation must survive completion of the older traversal. Recheck namespace changes on restore, erasure, older schema and backups, hold/release, and an interrupted delete or cursor update. A capture that is independently due also needs a bounded opportunity to proceed. The separate cadence finding at 85017e278 concerns a sweep starting a generation early. Fixing that admission test alone leaves this traversal starvation possible.
