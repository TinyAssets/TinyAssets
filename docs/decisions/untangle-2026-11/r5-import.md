# R5 P-import as built (2026-11-17)

R5's fifth slice (d3 §N7; `r5_research.md` §5.6, §6 slice 5, §7), on P-shape (#5531) and P-resume (#5675), with owner migration 18's pin. The research is the design; what follows is how it landed and where it departs.

1. **Migration 18's pin rode this branch, as its own commit.** `node ci/kit/locks.ts pin owner 8e4a1265a` (#5675's merge), as #5637 pinned 16; `digests owner` lists eighteen. tasks.md's line is ticked, naming this branch.
2. **Every old document maps; none is archived whole.** Until now every old document was refused by the new validator (it holds `entries` and `spend`), so every owner kept the default and its document went to `policy.refused.json`. `import/policy.ts` `mapPolicy` now reads the old shape (`broker/policy.py` `_validate`) and writes the new one, which the platform's own validator holds. The old document is kept verbatim at `/workspace/imported/policy.json` (the research's name), for every owner that had one.
3. **Each act decided as the old stack decided it.** Strictest wins over the main agent's rules, an act none covers taking the old default (`DEFAULT_RULES`). The main agent's rules decided every agent's call, and a configured agent's only its own, across every configured agent for `security.change`, `access.grant` and `money.move` (`authority_gate.py` `_agents`, `STRICTEST_AGENT`).
   - So each act's unnamed rule (every agent and outside client) is what the main agent's rules or the defaults made it.
   - A configured agent gets a rule of its own (`agent`) only where its rules, or the defaults, made the act stricter for it than for every agent.
   - An old agent name the new grammar can't hold (`AGENT_ID`, such as `*`) folds into the unnamed rule: stricter, never laxer.
   - `do` writes nothing, `do_if_preapproved` and `ask_first` write `ask`, `hand_off` writes `never`.
4. **The old defaults are written (d3 §N7, (a)).** An owner who never set a rule, and one with no document at all (the old bootstrap's document, `PolicyStore`), gets `people.message` and `share` ask, `security.change` and `access.grant` never. They are the owner's own settings, which Fact 15 allows, and the owner can change them. New owners start empty.
5. **What has no place is listed, never guessed** (the README's "Your rules"):
   - `no_act`: `commons.publish`, `app.read`, `app.write` and `shell.egress` have no act. Nothing in the old stack decided `shell.egress`; folding it into `workspace.shell` would hold every shell command, a rule the owner never wrote.
   - `unseen`: `browser.action`, `spend` and `money.move` exist as acts, but a rule on them that names no tools is refused at write (`seenUnnamed`). Their old defaults can't be written, so the owner is told to name the tools.
   - `connection`: a rule naming a connection or an operation, a standing grant (`record_kind: preapproval`), and the `approval_grants`, `operation_kinds`, `review_on` and `review_off` tables. All are keyed to the old app connections, which don't carry.
   - `host`: an egress destination the new validator refuses (an IP literal the old stack took). In `allowlist` mode that only narrows.
6. **Egress `ask` is `allowlist`.** The old `ask` never asked: it refused a host off the list and receipted `ask` (`broker/egress.py:83`). The list alone refuses the same hosts. The old allowlist also admitted each connection's own hosts; those go with the connections, which narrows. A legacy document with no `egress` takes the old provisioning upgrade's `open`.
7. **Departure: a spend cap refuses its owner (`policy_spend`), instead of a mapping into `models`.** The research maps caps into the `models` ceilings, unit-converted, "if the census finds any".
   - The old caps are minor units of no named currency (`policy.py:497`).
   - With any cap set, the old stack refused every metered model call (`broker/server.py:1440-1456`). So a ceiling would loosen what the owner had, and a zero would stop their API-key runs.
   - So the census counts it as a problem, loudly, and the mapping is built from its numbers if it finds any. An owner with a cap is held from the window until then, while the others go on. The live census is d3 §N9's (tasks.md Step 5).
8. **Departure: a document not in the old shape refuses its owner (`policy_unreadable`), instead of the default.** The old stack validated every write and checked its digest, so such a document is damage, and Fact 8 says fail loudly. A backup bundle (the excluded owners') is not held to it: its old document is kept verbatim all the same.
9. **The census** (read-only, as it always was) reports `policy`:
   - owners with no document;
   - who never set a rule (and so get the defaults written);
   - who set some (non-default `entries`);
   - egress `ask`;
   - the rules written;
   - what was not carried, by why.

   Spend caps and unreadable documents are in its `problems`. Per owner, the table gains the same counts. `rows.policies_archived` is gone with the archive-whole path.
10. **The notice's words are data, and the founder's** (`import/notice.json`; founder_batch item 6). The one notice joins its parts (the files, the rules, the archive when there is one, the README), and the README's "Your rules" lines are there too.
    - `{imported}` fills from `IMPORTED`. The old `NOTICE` constant spelled the path a second time.
    - A placeholder nothing fills fails loudly.
    - The reasons' words are keyed by the same names the mapping gives (`Why`), so a reason with no words is a type error.
    - The notice is held under `NOTICE_DETAIL` by its test.
    - The draft awaits the founder's approval: tasks.md's `live:` line. Nothing is sent.
11. **Every imported tree has a README now.** Every owner's rules came across (the defaults at least), and the notice points to the README for which ones.
12. **One home for "an act seen with no tools named"** (`contract/src/policy-document.ts` `seenUnnamed`). The validator's refusal and the importer's `unseen` read it, so the importer holds no copy of the rule.
13. **The old tables are copies, held to their source.** `DEFAULTS`, `BEHAVIOURS` and `STRICTEST` are the Python's (`policy.py`, `authority_gate.py`), which is retiring and takes fixes only. `guards/policy.test.ts` reads those files and holds the copies to them, as `old-tables.json` is held to the old DDL. They go with the importer at the cutover (`cutover.json`, the `platform/import` entry, now naming the mapping and the words).
