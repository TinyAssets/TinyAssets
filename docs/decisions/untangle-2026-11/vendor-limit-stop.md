# vendor-limit-stop as built (2026-11-21)

ADR-034 decision 7: on a subscription, "the vendor's own limits apply. A vendor's rate-limit or billing error stops the agent's scheduled runs and notifies the owner." Fact 15 makes the vendor's limit one of the only limits; Fact 8 asks that it fail loudly. Before this change no record said how a CLI reports its vendor's limit, a limited run settled `exit` or `engine_error` like any other, and its agent's cron fired again into the same limit each time.

## Research (2026-11-21, each pinned CLI's own source, the versions the records install)

- **Claude Code 2.1.309.**
  - `{type: "rate_limit_event", rate_limit_info: {status, resetsAt, rateLimitType, ...}}`. `status` is `allowed`, `allowed_warning` or `rejected`; `resetsAt` is Unix epoch seconds (the SDK's own schema text).
  - An assistant message carries a top-level `error` from a closed list. Of its values, `rate_limit` ("rate limited, wait and retry"), `billing_error` ("usage limit reached, check plan") and `account_on_hold` are the vendor's limit.
  - `{type: "system", subtype: "api_retry", error: "rate_limit", ...}` is the CLI's own retry of one call, which can still succeed. The record read it as the run's error, so a run whose call recovered failed `engine_error`. That was a bug; its matcher is gone.
- **Codex 0.166.0** (`codex-rs` `protocol/src/error.rs`, `exec` jsonl). A failed turn is `{type: "turn.failed", error: {message}}`, and its limit is only in the message:
  - `UsageLimitReachedError`: "You’ve hit your usage limit…", "Your workspace is out of credits…", "You hit your spend cap…";
  - `QuotaExceeded`: "Quota exceeded. Check your plan and billing details.";
  - `UsageNotIncluded`: "To use Codex with your ChatGPT plan, upgrade to Plus";
  - `RetryLimit` after a 429: "exceeded retry limit, last status: 429…".
  - The reset is in the message text only ("try again at …"), so it is not read.
- **Gemini CLI 0.66.0.** A fatal error is `{type: "result", status: "error", error: {type, message}}`, and `type` is the error's `name`. `TerminalQuotaError` and `RetryableQuotaError` are its quota classes. Their `retryDelayMs` is not in the event.
- **Kimi Code 2.4.1.** In print mode, a failed turn throws `"<code>: <message>"` to stderr. The runner passes it on as `ta.stderr` when the CLI exits non-zero. A 429 is `provider.rate_limit`. A quota stop (`APIProviderQuotaExhaustedError`) is coded `provider.api_error`, and only its message tells it apart, by Kimi's own patterns ("exceeded your current (token) quota", "check your account balance", "insufficient balance", "recharge your account", "account (is) in arrears").
- **pi 1.4.0** (pi-ai). A failed call ends `{type: "message_end", message: {stopReason: "error", errorMessage}}`. `errorMessage` is the provider's own body, `"<prefix> (<status>): <body>"` where pi-ai adds the status. pi speaks to many providers, so the record matches the common words for a quota, billing or rate stop, and a 429 status.

## Decided

1. **How each CLI reports its limit is data: `engine.events.vendor_limit`** (`contract/src/run-events.ts`), a list of matchers like the record's other kinds.
   - Its `path` reads the reset the vendor states, in seconds. A path that reads no number states none.
   - A matcher may now also carry `pattern`: dotted paths to regular expressions, read case-insensitively (`PATTERN_FLAGS`, shared by the reader and the record check). This serves engines that report the limit only in prose (Codex, Kimi, pi). The record check refuses an empty, uncompiling or over-long pattern, and the login patterns now go through the same `compiles` check.
   - No provider-specific code: each engine's way is in its record (Direction 2).
2. **The run settles `vendor_limit`** (`owner/src/runs.ts` `VENDOR_LIMIT`). This happens when it failed and any of its batches brought the vendor's refusal. The code is kept on the row from that batch, and a cancel's `cancelled` still wins. The vendor's own words stay in the run's `error`, from its first error event.
3. **What the hold covers: the run's agent's schedules under the run's own authority** (`client IS` the run's). Those run in the same sandbox, on the sign-in or key the vendor refused.
   - **Considered: every schedule that would run on the source.** No schedule knows its source until its run is admitted (`choose` reads the agent's file), so this would need a new table of holds (a migration, export and restore) read at admit. It would also let one agent's sandbox stop every other agent's schedules with one forged event, since the sandbox is untrusted.
   - **What this choice costs:** every agent on the same plan meets the limit once and is then held. That is one refused call per agent per window, not a retry storm. The ADR's own words are "the agent's scheduled runs".
4. **How long the hold lasts** (`Runs.#hold`, from the batch that brings the limit).
   - **A reset stated:** each cron moves to its first time after the reset, and a one-shot to the reset at the earliest.
   - **No reset stated:** each cron stops (no next time). A one-shot keeps its time: it can fire once more at most.
   - **The mark:** each held schedule's `last_error` is `VENDOR_HELD`, a fixed text. The owner reads why the schedule waits, and `#lift` finds what it held.
   - **The lift:** a successful run of the same agent and authority means the vendor served it again. `#lift` then gives each held cron its next time from now.
   - **Why the owner runs it:** with no reset, nothing guesses a back-off window. A guessed retry is the storm the ADR forbids, and a guessed number is a limit Fact 15 does not allow. So the notice tells the owner to run the agent once the limit or billing is cleared.
5. **The notice** (`vendor_limit`, once a run) is in the object's own words, never the event's text, which comes from the untrusted sandbox. It names the agent, the engine, the credential (its plan's sign-in, or its API key), and the reset as an ISO time, or that the runs wait until one is served.
6. **Left as they are, with the reason for each.**
   - **Mail-started runs are not held.** Agents mailing each other end their loop at the first refused run, since it answers no mail (spec, "Agents mailing each other are bounded…").
   - **An attended run is never held.** A person is there, and the vendor's own error reaches them.
   - **A run that met a limit and then recovered keeps its notice.** For example, Claude's window rejected, then the model fell back. Its success lifts the hold.

## Owed

- [ ] live: at the dark redeploy's first run that meets a vendor's limit, each engine's record reads it as `vendor_limit`. The matchers come from each CLI's source, never from a live limit, which no session here can provoke.
