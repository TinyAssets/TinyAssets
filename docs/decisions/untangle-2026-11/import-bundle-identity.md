# Import bundle identity as built (2026-11-18)

The backup survey derives a full SHA-256 from the original principal. Its first 16 hex digits name the bundle folder. A rehearsal uses the substituted principal for the emitted index and its folder. Excluded owners keep their original names in the private backup. The format-1 writer already produces these layouts.

The local stage, the doors' consumer and the all-runs close now read identity through the same check. The format, embedded key and hash of the index owner must agree with the folder. The consumer continues to check the signed digest and the message owner. Close could previously count a misplaced admitted owner's index twice and missed a different staged owner entirely.

The regression uses the builder's real indexes for original and substituted names. It checks an intact directory, one missing admission, a misplaced index, and an index whose key field was corrected without correcting the owner. Stage rejects inconsistent identity before uploading. The consumer's refusal is exercised through the import queue.

When the deletion comparison in #5688 and this change meet, its private original digest and emitted owner key use the same `bundleIdentity` helper. This change does not settle the separate discard-generation or late-producer questions.
