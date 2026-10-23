## 8. After the cutover (live 2026-10-22): close the gap between live and the shape

The cutover is live, but several fixes went live as hotfixes and some of them
drifted from the target shape. Every item below names the rule (T1-T10) it
serves. An item that would add a check to shared code is reshaped or dropped:
the router, broker and waker get no new business logic.

- [ ] 8.1 Subscription CLI logins in broker custody, leased into a per-run tmpfs,
      refresh written back, nothing persistent in the box (T6; ADR-022 P2b;
      closes `docs/concerns/c6-cli-vault-box-copy.md`). Branch `conv/source-custody`.
      Existing logins move into custody on first use, so nobody signs in again.
- [ ] 8.2 Address discovery runs in the owner's box through its egress path; the
      broker keeps only the OAuth code exchange, client registration and deposit.
      Delete broker discovery and its time-budget guard (T1, T2).
- [ ] 8.3 Delete the runtime "dead member" pruning in the box (source_connect,
      connection_uses, model_access_requests). Instead, the lead runs a one-shot
      repair that removes assignment members whose grants the migration dropped,
      then the repair is deleted with the other one-shot migrations (6a part 2).
- [ ] 8.4 Subscription CLIs ship once in the shared box image, built only from
      committed per-record lockfiles (T4: no per-owner installs; the supply-chain
      review finding).
- [ ] 8.5 Deploy `conv/source-records` plus `conv/source-custody` through the
      normal deploy path, after one review scoped to the router, broker and waker.
      Assert the deployed sha, run the 8.3 repair, then make the hotfix ledger
      (`/var/lib/tinyassets-deploy/hotfixes/`) empty: every live hotfix is either
      in the deployed sha or deleted. Live-only diagnostics are deleted, not folded.
- [ ] 8.6 Deploy carries its own config: the broker's outbound-HTTP flag, the box's
      public URL, and the liveness directory's group mode come from the repo's
      compose/env/migration, never from host edits.
- [ ] 8.7 A failed or sleeping box gives the router a plain "unavailable" (503), never a
      500; the router only maps and forwards (T1).
- [ ] 8.8 Box drain: pending sign-ins never hold a box awake; cancelled login
      processes exit before the box reports idle (T7/T9 in-box, done on branch).
- [ ] 8.9 Generic schema drift: a store whose table layout differs from the code's
      is rebuilt by the store itself, once, everywhere the migration copied old
      layouts (the stale-layout class behind three cutover breaks), not per table.
- [ ] 8.10 Model turns: a free-model turn that spends its attempts on tool steps
      still returns a reply or a clear stop; Automatic skips models that fail before
      reaching the provider (in-box planning, T8).
- [ ] 8.11 One durable send (shape-convergence C11): the page's self-reload never
      drops a send or repeats "Still waiting to be sent" notices.
- [ ] 8.12 Sign-in in the user's own context: the same tab on the web, an in-app
      sheet on phones, the system browser in the matching profile on desktop with
      return to the app; no waiting tab for installs (8.4 removes the install wait).
- [ ] 8.13 Naive-user pass per surface (app, MCP, a scheduled run) on both test
      accounts, through the agent, after 8.5 (task 7).

Open PRs: each is merged, folded into `conv/source-records`, reshaped, or closed
against this list (triage 2026-10-23, below).
