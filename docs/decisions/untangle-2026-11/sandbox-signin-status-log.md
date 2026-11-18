# Sandbox sign-in status logging as built (2026-11-18)

The runner checks the vendor's sign-in after a login and when reporting engine
presence. A status command can return a normal negative result. The runner can
also fail to run the check, for example when it cannot obtain a run user.
Previously both paths reported false and the latter lost the cause.

safeSignedIn now logs a thrown check's engine and error to the job's private
stderr, just as loginRun logs its own exceptions. The job launcher opens this
log with mode 0600. The presence post still contains only the engine and its
boolean status. A normal negative result writes no diagnostic. The protocol's
direct signin-check command has its own exit handling and needed no change.

The changed runner bundle changes its digest and therefore the probes built
beside it. The paid template build, live pair check and pin are still the
later coordinated step in the rebuild checklist.
