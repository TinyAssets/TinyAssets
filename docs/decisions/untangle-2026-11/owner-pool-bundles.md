# Owner test Worker bundles as built (2026-11-18)

The owner test pool needs two Files keyrings and three Gateway keyrings. Each group uses one compilation of its config and entry. Names, bindings, outbound services and storage are still specified per Worker. Reuse is scoped to that construction. The other pools, browser tests and alternate entries continue to build the program they request.

During an actual capture on main, Wrangler reported success for the retired Gateway with an empty output directory. In a second capture, running the six original invocations serially produced a complete manifest. On the same checkout path, the three new invocations gave that same complete manifest, including the twelve auxiliary Workers, all thirteen scripts or modules, binding descriptions and content hashes. The comparison normalized the shared checkout path and the test credentials.

The bundle reader derives the emitted script name from the requested entry or the config, verifies that Wrangler wrote it and puts it first. The memory baseline imports that same returned entry. Owner and Gateway memory legs and auxiliary Workers all use fresh output directories. The shared CLI must also propagate a failed subprocess, even if a partial output directory already contains the entry. See the independent Wrangler command-completion change.

A passing capture describes the pool configuration. Each behavior recorder still needs its own successful run and comparison. These test-harness changes do not alter deployed Workers.
