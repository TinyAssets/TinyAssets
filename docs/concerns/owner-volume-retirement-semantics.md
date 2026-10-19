# Lane a1: clarify retirement before wiring volume deletion

Inspected `conv/c6-research-shape` at `126d2b0661b`. Runtime implementation
stopped under the lane brief's rule 4; no runtime or test changes were made.

Build item 2 says both destroy and retire stop the runtime and call
`volumes.delete(uid)`. The existing `retire` control operation also stops a box
when just one command center is retired:

- `tinyassets/broker/server.py:608-614`: `RETIRE_CENTER` retires that center's
  admission, then calls `_stop_owner_box`.
- `tinyassets/broker/server.py:602-605`: `_stop_owner_box` sends the `retire`
  lifecycle operation. The fenced `CENTER_ADMISSION` path does the same at
  lines 819-822.
- `tinyassets/boxes/host_service.py:179-182` dispatches that operation to
  `OwnerDaemons.retire`; `tinyassets/boxes/owner_daemons.py:312-328` stops the
  runtime. `destroy` at lines 330-342 separately performs account destruction.
- `tinyassets/broker/owner_identities.py:150-198` retires only the named center;
  other centers can remain admitted to the same owner UID.

Consequently, replacing the current retire implementation with volume deletion
would erase all centers belonging to that owner when only one is retired.
Research lines 55 and 61 identify the durable volume with the owner; line 63
puts that owner's files, database and pending work inside it. Line 117 makes
the isolation unit one box per account. C1 describes whole-volume destruction,
and C6 lines 148-154 places it in account deletion. The delta spec's destruction
scenario at lines 13-16 likewise starts with the owner's deletion job.

Clarification needed: does the brief's destructive "retire" mean account
retirement only? If so, specify that existing center-retirement callers retain
a stop-only operation, and whether moving those callers belongs to a1 or b.
No shared exclusion guard or alternative storage path has been added.

All a1 build items, deletion ranges, `.locks` registration, integrated proofs,
and C1/C2 as-built updates remain pending. No production, remote backup storage,
credentials or Git configuration were accessed or changed.
