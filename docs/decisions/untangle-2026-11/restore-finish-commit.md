# Finish a restore once

R5r items 7 and 8 hold admission until the generation has passed its check and
the final fence has landed. A failure in the terminal SQL unit on main
`f934871ad` can leave the database partway through that fence. The outer write
gate commits the prefix of a unit that throws. Updating allocation floors,
recording the adopted generation in `owner_backups`, incrementing the pending
sweep count, restoring the admission column and marking the restore done are
separate statements.

An offline workerd Durable Object with all 18 migrations ran the original
restore piece and its SQL collaborators. A synthetic trigger interrupted the
last statement. The restore record stayed at clear, so the existing restore
gate still held ordinary work. The admission column had changed, a generation
was already recorded, the pending sweep count had risen from zero to one, and
an inbound binding's revision had risen from three to four. On retry it rose to
five and the file allocator rejected the attempt to set the same sequence again.

The corrected terminal unit uses the owner's synchronous savepoint around the
SQL collaborators. Source reads and the namespace operation finish before the
savepoint begins. With the identical interruption the generation count and the
pending sweep count stayed zero, the revision stayed three and admission stayed
pending. Retry reached done with one adopted generation, one sweep owed and
revision four. The existing file and generation checks still apply.

A terminal restore already partly committed on older code needs a verified
recovery disposition. The restore marker by itself cannot identify which
finalization statements landed. The existing wipe door takes a pinned or failed
restore. It cannot reset an active clear record solely on that evidence.
