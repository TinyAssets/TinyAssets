Lead approval of the C6 close-out branch (floor). Yardstick: openspec/changes/owner-daemon-in-box research.md and design.md T1-T10.

Review trail (cross-family, each told to break it):
- review of d90a5e23b32: DISAGREE (broker stall from discovery, cross-site token, flat-rate billing, record drift). Fixed.
- review of 9ab39b4b886: DISAGREE (subscription member dropped, transient pruning, registry trust in the image build, disconnect races). Root causes were shape drift; reshaped instead of patched: CLI logins into broker custody leased per run (T6), discovery moved into the box (T1/T2), runtime pruning deleted in favour of a one-shot offline repair, image built only from committed lockfiles.
- review of the reshape (c9ae0cac485): all six earlier findings resolved; 8 new findings, 7 fixed with failing-first tests.
- final fix-round review (xhigh): six of seven resolved; A-C fixed by the lead in 9f403417d1c (writeback retry on a dropped broker channel, disconnect on non-ready assignments, login ops off the event loop); D (non-JSON/TOML user-declared state files) accepted as a documented limit.
- router 503 and turn-reply branches: separate review, R1-R4 and B1-B2 fixed and checked by the lead.
- integration: image build caught a cross-lane break (installer lacked lease state), fixed in 9a6aa95c904; image built successfully.
- CI fix commit 6bfc74f1f57: product changes are only the removal of a provider-named discovery probe path (Direction 2) and inventory classifications; the rest is tests and proof declarations.

Validation: all required checks green (structural-guards 769, affected-tests 2,111 passed); targeted Linux oracle runs on every lane.
