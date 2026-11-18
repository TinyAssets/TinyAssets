# Owner schedule during import discard (2026-11-18)

A fresh instance binds its first owner door and schedules a scan. A repeated import discard can wipe its SQLite tables while that scan awaits a job re-arm. On main a real workerd eviction followed by a second discard returned the expected null namespace, then raised an unhandled SQLite error reading the deleted jobs table. A job completion or error can also reach the queue after a wipe.

The schedule checks the owner's halt condition inside the write gate before inspecting any jobs. It scans unchanged lanes synchronously. A push or cancellation ends that gate unit and the scan waits for Lifecycle to finish re-arming the alarm before inspecting another lane. A completed or failed pass uses the same gate check before rescheduling.

An independent SQLite and WriteGate replay reproduced the three post-wipe reads on main. The changed schedule attempted zero in all three cases. For eight unchanged lanes, both versions performed three halt queries and eight job lookups. The changed version used one transaction, down from eight. A mixed scan kept the same push, cancel and reschedule order, honored a due time changed during a delayed re-arm, and stopped at the first failed re-arm. Each of the eight production lane inspections uses synchronous reads and calculations.

The eviction regression runs entirely in local workerd. Elapsed eviction time during the dark rehearsal and the import consumer's durable namespace receipt remain separate work.
