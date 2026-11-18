# Sign-in command arguments as built (2026-11-18)

The source checker accepted a sign-in argument containing `{home}`. At launch the
runner passed those six characters to the vendor. The same mismatch allowed a
Kimi status check to reference its region, which neither the status check nor a
sign-out receives. Adding record-driven hooks would otherwise build on a checker
that accepted values its consumers could never fill.

The sign-in command takes its binary and the owner's region. Status and sign-out
take their binary and temporary home. Their vocabulary lives with the sign-in
contract in `sources/src/signin.ts`. The catalog uses it to refuse an unavailable
placeholder when it reads a record. The runner and the sign-in probe use it to
expand their commands. All four checked-in sign-in records keep their arguments.
A record requiring a region still refuses a sign-in made without one.

The approval hook's record shape, start check and deferred resume are separate
work (`r5-resume-hooks.md` items 6–8). This changes the runner bundle and therefore
the paired probes. The existing build and pin in step 6 take the pair together.

The checker also declares the status and stored-state fields as dotted paths.
A checked status record requiring `account.signed` previously refused a nested
true value and accepted a literal key containing the period. Both sign-in
readers now use the existing dotted-path reader for JSON object members.
The runner still checks the command's exit before interpreting its output.
