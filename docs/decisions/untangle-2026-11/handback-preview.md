# The handback check after routes, as built (2026-11-18)

Step 13 calls for reading the rollback plan while the new platform serves the routes.
On `f934871ad`, dispatching `handback` on main schedules the apply job as soon as the plan
succeeds. The production environment uses the founder's dispatch as approval.

The original routes run saves the pre-handover routes and its changes. A later public
handback plan reads that record and the current Cloudflare state. In an offline replay of
the deploy kit's entry point, the same saved record produced a plan after handover and
a refusal after the moved route pointed to another Worker. Both reads left the simulated
account untouched. The public job defers inspection of the actual E2B hook to apply.

The manual workflow now offers `plan_only` for the chosen mode. Selecting it keeps the
existing public plan job, its credential scope and its artifact. The pairing and apply
jobs both require the option to be cleared. The default preserves ordinary dispatches,
including an intentional rollback with its original record. The live Step 13 check still
belongs to the handover.

Each explicit preview uses a concurrency group named for its workflow run id. A preview
in the ordinary operator group could replace a queued founder dispatch while another run
is active. A concurrent preview can see intermediate state. It writes the usual summary
and artifact within its own GitHub run, in the same environment as the public plan.
Applying later still requires a separate ordinary dispatch, which rebuilds its own
run's plan from then-current state and checks it before the first change.
