---
severity: note
title: Served tool capability parity gaps
filed: '2026-09-08'
summary: source audit finds missing stop/trigger/dependency controls and discovery contradictions; secret custody and person-only approval remain deliberate boundaries
---

# Served tool capability parity gaps

**Filed:** 2026-09-08
**Verified:** 2026-09-08 local source at c97208f5 (runtime differs from the last app-reported deploy only in workspace receipt instrumentation); not a live exercise of the missing controls.

## Source (verbatim)

Owner, during the checklist deployment loop:

> while your at it check for any other missing tools that should be exposed

## Finding

The in-app engine surface is a manually narrowed sibling of the canonical
connector, not merely the same tools with a pinned universe. Provider-family
allowlists share `SERVED_ENGINE_MCP_TOOLS`, so the two integrated CLI families
agree with each other, but that does not establish capability parity with the
connector. An operation may be implemented, documented elsewhere, and still be
unreachable in the app. User-confirmed rail actions are a legitimate alternative
route; absence of a direct tool alone is not proof of a gap.

Update 2026-09-08 19:59 UTC: the automation row below describes the audited
pre-fix surface. Its platform route is now deployed in `4a1877f0044a` (PR #3447);
the exact retest sent at 20:19 UTC through an owner-authorized new tab confirms
automation inspection and paused-future-trigger readback on that deploy.
Lifecycle mutations were not exercised by that rendered response. Organic owner
follow-up at 18:00 PDT later confirmed retirement, workflow deletion and a
terminal last run, resolving the attached-heartbeat blocker. See
[rendered acceptance](../reviews/2026-09-08-workflow-checklist-live-acceptance.md).
This does not establish every lifecycle combination. The other inventory rows remain
unresolved. This does not establish provider portability or all-tool parity.
Cancellation and legacy schedule controls below are missing from both inspected
canonical surfaces, not just from the app's narrower wrapper.

## Source-verified inventory

| User capability | Existing implementation / route | Served status and disposition |
|---|---|---|
| Inspect, create, pause, resume, retire automations | `api/automations.py:474`; connector `universe_server.py:569,1097` | Historical audit: targets were excluded. PR #3447 exposes owner-scoped controls; organic owner follow-up at 18:00 PDT confirms retirement and dependent branch cleanup. That blocker is resolved; other lifecycle combinations are not all live-tested. |
| Stop a run the user started | `api/runs.py:1518`, `_RUN_ACTIONS` in the legacy extensions dispatcher | Served `run_graph` at `engine_mcp_server.py:395` only starts runs; no cancel operation, nor one in canonical `run_graph`. Expose an owner-scoped cancellation request with accurate pending/terminal readback; underlying cooperative cancellation is not an immediate-stop guarantee. |
| Inspect and revoke inbound webhooks / sources | Connector `run_graph` at `universe_server.py:1458` has `webhook_op` and `source_op` | **Webhooks: served as of change `served-inbound-webhook-triggers` (2026-09-24, C16).** `write_graph target=webhook` create/revoke and `read_graph target=webhooks` reuse the connector handlers with the universe and owner pinned. The URL goes once to the owner's own agent, which is the same custody the connector already gives the owner's chatbot. Revoke takes the listed prefix, so a lost token no longer blocks branch deletion. Sources (`source_op`) are still connector-only. |
| Inspect, pause and remove existing schedules / event subscriptions | `api/runtime_ops.py:884` scheduler action map | Not reachable from the served handles, and not routed by the canonical coarse-grained handles inspected here. Branch deletion names these dependencies (`api/branches.py:839`). Review migration-era authority and universe confinement before mounting existing handlers; automations and scheduler bindings already coexist, so do not add another scheduling system. |
| Inspect or change own goal/loop dependencies | Connector goal reads and `write_graph target=goal operation=set_canonical`, `target=universe operation=declare_loop` (`universe_server.py:542,948,1011`) | App can browse shared goals but lacks own binding controls. These are also branch-deletion dependencies. Empty branch ID already clears a loop (`api/universe.py:6618`); setting one authorizes work on incoming requests. Distinguish private unbinding/repointing from global goal creation/publication. |
| Use a discovered public workflow as an owned remix | `engine_mcp_server.py:1857` defines `remix_shape`; connector branch remix exists | Tool is deliberately excluded from `served_tools.py`, while `browse_commons` at `engine_mcp_server.py:1732` tells the model to use it. Confirmed discoverability contradiction. Needs the deferred cross-author safety review, not merely adding its name to the allowlist. |
| Inspect private agent bindings and select an existing provider | Connector reads at `universe_server.py:614`; writes at `:1254` | Served compute registration is available, but binding/read/update/set-serving controls are not. Provider selection must be reachable by an owner-confirmed, secret-free path. Arbitrary-provider compatibility and connection-local model defaults are deeper existing gaps, not solved by mounting this handler. |
| ~~Read channel policy and change owner-controlled policy~~ | RESOLVED 2026-09-25 | `get_policy`/`set_policy` and their store are DELETED, not served: they wrote an approval mode no gate read. There is no per-channel policy left to inspect or change beyond the consent row, which change `agent-access-controls` serves as `approve`/`revoke` with a readback through `read_graph target=access`. |
| Search/read broader pages and file platform issues | Connector `read_page` / `write_page` at `universe_server.py:1559,1628` | No served wrappers. `read_brain` / `write_brain` cover governed grounding sections, not arbitrary page search or commons issue filing. Existing deferred note (`universe_intelligence.py:153`) cites home-vs-graph scope and global publication. Add explicit scoped paths only after review; do not expose unrestricted global page writes. |
| Fully edit an owned workflow in place | Canonical node update fields at `api/branches.py:2273`, served restrictions at `engine_mcp_server.py:753` | Served updates permit prompt/source/display name only: provider policy, input/output keys, enabled state and retry policy are excluded. Skills can be removed but not added/updated; effect nodes cannot be added by patch. Some restrictions cite the retired execution-approval gate or graph-size ceiling. Reverify their safety premise; replacing an entire workflow is not equivalent to maintaining the user's existing workflow. Never perform the private edit on the user's behalf to hide this gap. |

This inventory identifies missing user capabilities and dependencies, not a
blanket authorization to mount every historical extension action. The underlying
handlers' existence is not proof that every one is safe, current, or sufficient.

## Live addition: withdraw an obsolete agent-authored request

Rendered app response timestamped 2026-09-08 13:42 PDT: after being asked to
limit its receiver repair access, the agent created a narrower request but
could not withdraw its obsolete broader one because it has no request-edit or
cancellation operation. Codex, acting through the normal user UI under the
owner's clarified delegation, cleared the obsolete request. This is a missing
authoring lifecycle, distinct from accepting or declining a request as the owner.

Source reverified at the current 9d65ae56-based working tree using `rg`:
`engine_mcp_server.write_graph target=pending_request` admits `ask` only; the
canonical `api.pending_requests.answer_request` offers the person's dismissal
route. Do not expose that broad owner-answer route to the requesting agent.
A future withdraw/supersede operation needs requester provenance, universe
confinement, pending-only semantics, revisions and an honest receipt, and must
not let an agent clear a person-required sticky precondition or manufacture
approval. This eleventh inventory item is not covered by the earlier review.

## Already reachable / deliberate boundaries

- Branch create/patch/delete, run start/read, own brain, commons shape reads,
  compute registration and outbound consent already exist on the served surface.
- Credential deposit, grant extension, credential removal and typed workspace
  consent are reachable through `pending_request` actions; `_validated_action`
  (`api/pending_requests.py:156`) supports these. Do not falsely classify them as
  missing merely because direct connection writes are excluded.
- Raw secrets must stay out of model tool arguments/results. `answer_request`
  and `unmute_request` belong to the person answering, not the proposing agent.
- `converse` must not be mounted for the universe to recursively call itself.
- Public publishing, account/universe birth, money and platform administration
  are not ordinary private-workflow edits. Require specific authority and design,
  not a broad connector pass-through under the founder identity.

## Durable next work

The attached-heartbeat lifecycle blocker now has organic stopped/retired/deleted
evidence. Close the other stop/cleanup gaps before expanding discovery and
authoring. For each route, record direct-agent versus owner-confirmed semantics,
resource selectors, scope/ACL checks, and receipt/readback behavior. Add relational
tests that every operation taught by served tool descriptions is reachable and
every intentionally excluded operation has a real alternative or explicit caveat.
Do not replace the shared coarse-grained handles with per-provider or per-workflow
tools. A common operation contract may remove repeated routing/documentation
drift, but is a proposal requiring review, not an approved architecture change.

Verification method: `rg -n` of handler definitions/dispatch targets in
`tinyassets/universe_server.py`, `tinyassets/engine_mcp_server.py`,
`tinyassets/served_tools.py`, `tinyassets/api/{automations,runs,runtime_ops,
pending_requests,source_channel}.py`; bounded `docview.py lines` reads of
the corresponding implementations; provider imports checked in
`tinyassets/providers/codex_provider.py` and `tinyassets/universe_intelligence.py`.
No live missing capability was invoked and no private workflow was modified.

Supporting Windows baseline on 2026-09-08: `python -m pytest -q
tests/test_engine_mcp_server.py tests/test_engine_mcp_write_graph_patch.py
tests/test_removal_is_reachable_from_the_served_surface.py` => 108 passed,
3 skipped in 6.75 seconds. This validates existing contracts, not the absent
capabilities; their omission despite green tests is the coverage gap.

Additional read-only local probe: `python -m output.check_served_edit_reachability`
on 2026-09-08 directly exercised the sanitizer with synthetic dictionaries only.
It refused `update_node.llm_policy`, `update_node.input_keys`, and `set_skills`.
No branch/store was created or changed. This confirms the edit restrictions,
not that lifting every restriction without validation would be safe.

Independent Claude review completed 2026-09-08, wrapper exit 0 after 353 seconds.
VERDICT: ADAPT on framing, not rejection of the confirmed gaps. Corrections above
distinguish both-surface omissions and the existing loop-clear primitive. Review
recommends direct graph/actor-pinned calls to existing automation lifecycle
handlers, preserving ACL/revisions and charging create through admission. It
also recommends relational coverage of refusal text and target/operation pairs,
not only documented standalone tool verbs. The additional in-place editing row
was added after dispatch and is not covered by that review.
