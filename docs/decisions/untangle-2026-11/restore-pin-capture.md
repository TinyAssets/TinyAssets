# Restore pin and its fresh capture

The owner's pin holds ordinary work before it captures the generation used to undo a wipe. The generation's opener writes the backup state, starts the change log and inserts the writing generation. The restore record must name that same generation. The owner's WriteGate commits the prefix of a quiet unit that throws. The pin therefore uses a synchronous storage transaction around the opening and the pointer update, after determining that no earlier generation is still writing.

In a local Owner Worker on main, interrupting the pointer update left a writing generation and a pinned restore with no fresh pointer. A successful retry used another generation. The regression checks the held restore, its original capture state, the absence of an unreferenced writing generation and a subsequent successful repeat of the same pin.

A backup already writing before the hold is still allowed to finish. An old record with a missing pointer cannot distinguish that backup from an interrupted attempt. Keep the existing retry behavior for such records. This SQL change leaves the hold, termination of runs and sockets, outside backup writes and later admission as separate steps. Recovering interruptions at those steps remains open.
