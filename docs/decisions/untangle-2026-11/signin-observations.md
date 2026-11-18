# Sign-in observations in source records (2026-11-18)

In the Step 6 review against `f934871ad`, the source validator accepted four records
whose declared observations the sandbox could never make. A Claude record could demand
a JSON value while omitting the command that prints it. A Codex record could declare
an empty command. A Claude or Gemini record could request completion on stored state
while supplying zero state checks.

`LoginSpec` in `platform/sources/src/schema.ts` defines each dependency.
`signedIn` in `platform/sandbox/runner/runner.ts` uses the status command's exit and,
when requested, compares its output. With no command, it delegates to `statesHold`
in `runner/login.ts`. That function returns false for zero checks. The login watcher
also uses it for completion on stored state. The source validator now rejects the four
invalid declarations before the records reach either runner path. A login can still
omit a status observation. A status command can succeed by exit alone.

All four served vendor sign-ins satisfy the dependencies. Claude checks command output,
Codex uses its command's exit, Kimi checks stored state, and Gemini checks stored state
and can complete its running login that way. Their records and the sandbox are unchanged.
A separate candidate shares the runner's command placeholders and dotted-field lookup.
The engine lock refresh, template pair check and paid build remain Step 6 dependencies.
