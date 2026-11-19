# The cutover manifest reader as built (2026-11-18)

The manifest keeps its reader, guard and import-layer support until the founder ends
the hold. Step 15 retires the importer layer together with its entry in the shared
kernel checks. Removing the layer alone made the actual kernel checker stop before it
examined any source on both `f934871ad` and the joined tree `151c2a83f`.

Each phase can remove its own names and imports from a surviving file. The reader
accumulates those imports across the pending phases. The guard rejects two edits to a
file within a phase and rejects a later phase that repeats a removal already listed.
A surviving file must drop an import by the phase that deletes its target or containing
directory. Entries in one phase can appear in either order.
When an earlier phase lands, its entries leave the inventory with its changes. A
remaining import still has its own phase and a retired import loses its permission.

A projection of the manifest's whole-path deletions on main retained all three check
files for twelve pending end-of-hold entries. The projection on the joined tree retained
them for seventeen. Both preserved every previously listed live operation in its original
phase. These projections exercise the checker lifecycle. The full intermediate build,
the one-time deploy modes' inventory and the founder's live hold-end decision are
still owed.
