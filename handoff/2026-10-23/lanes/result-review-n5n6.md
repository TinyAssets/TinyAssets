I've read both specs and the C1/C2/C7/C8/C10 sections they rely on. Next I'm checking how spending approvals and restore authority work in the broker today.

Several parts hold up, but I found problems that need fixing before merge. N5 does extend the one existing check (`tinyassets/broker/egress.py:13`). Its policy has one writer, `PolicyStore.write` (`policy.py:197`), and the agent only gets the existing read projection (`OwnerPolicy.projection`, `policy.py:45`). N6 does go through the one existing restore path, `VolumeLifecycle.restore` (`volume_lifecycle.py:84`), and keeps platform authority current on restore.

**N6 (undo by box snapshot)**

1. **Critical. The per-turn trigger is a user-facing change nobody has approved, and its cost is left unsettled.** `owner-box-snapshot-undo/design.md:11-13, 26`.
   - Every turn on an awake box means: stop, fully detach, compress, upload, read back from off-host storage, wait for the next waker tick (`volume_lifecycle.py:105-118`), then a cold start. That happens before each message in a live conversation.
   - The founder accepted only "a box awake more than 24 hours pauses briefly between turns" (`owner-daemon-in-box/design.md:1848`). Per-turn pauses are a new user-facing question, and that design says those go to the founder.
   - The spec works out about 35 GiB of snapshots per owner. The founder's storage cap is $15/month across all owners (`design.md:1843`, C10), and every turn would also re-download a full image just to verify it.
   - Line 26 says "If frequent checkpoints do not fit, bring a trigger/retention revision for decision". That postpones the central decision to implementation time. The spec doesn't show it fits the 5 GiB free tier. It renames the free tier to mean only the live volume.
   - A simpler trigger that already exists in C2: reuse the latest sleep snapshot (line 11 already allows that). Add an owner-requested checkpoint or a per-session one, and send the per-turn idea to the founder.

2. **High. The spec doesn't say who is allowed to start a restore.** `design.md:15, 20`; `spec.md:27`.
   - It requires "protected owner confirmation" but also says "Turn/approval/app logic stays in-box". Today restore is only reachable from root administration in the broker (`design.md` C2, as built).
   - The spec never names the path by which an owner-confirmed restore reaches the broker. N5 names its path (N10's protected owner-session route); N6 doesn't.
   - If the confirmation lives in the box, an agent with shell access in the box can forge it and rewind the owner's data. AGENTS.md says authority must be settled in the spec first.

3. **Medium. The spec doesn't settle whether undo keeps the owner's current state.** `design.md:16` ("No automatic redo snapshot is promised"); `design.md:15`.
   - Restore has to stop the box, and C2 takes a snapshot on every completed sleep. But a newer intent replaces a pending snapshot and that snapshot is deleted (`volume_lifecycle.py:113-118`).
   - So whether undo permanently destroys the current state depends on timing. That's a data-loss outcome left to chance. The spec should say either "the pre-restore sleep snapshot is published first" or "current state is discarded" and show that in the confirmation text.

4. **Medium. Turns that wait on an open request aren't covered.** `design.md:12`.
   - The box exits in the middle of an interactive request: the queued turn is saved in the box, and the box stops for the checkpoint.
   - The spec doesn't say how the waiting MCP or app call gets its answer across the stop, the upload and the cold start, or whether the router holds the call. Checkpointing before scheduled turns is covered; interactive turns are not.

5. **Low. The pre-turn pin puts owner meaning into shared code.** `design.md:13`; `spec.md:15-19`.
   - The "latest pre-turn checkpoint" pin makes the shared lifecycle tell turn checkpoints apart from ordinary snapshots, and today's `prune` has no such concept.
   - With per-turn checkpoints, keeping only seven snapshots also shrinks the owner's backup depth from days to about seven turns.

**N5 (owner egress gate)**

6. **High. `ask` and `allowlist` break the agent itself.** `owner-network-egress-policy/design.md:12-13`; `spec.md:8`.
   - Model and connector HTTP go through the owner policy, and credentials never exempt a destination. So in `ask` mode every new connection to the model provider, and to each redirect hop, needs a person's approval and refuses until they give it.
   - In `allowlist` mode the owner has to list their own model host or the agent stops working. That makes the agent unusable without the policy saying so.
   - The registry's intent is "ask-before-internet". The spec needs to decide whether the owner's own connected model and credential services count as internet, without provider-specific code. One option: a destination with a bound credential satisfies `allowlist`/`ask`.

7. **Medium. "Uses the existing approval path" is only half true.** `design.md:14`.
   - The Needs-you card is existing. But the consumable retry grant is new broker state: bound to owner, policy generation, destination/port, challenge and volume generation, with a five-minute expiry and atomic use by the proxy.
   - The broker has no one-use approval store today. Spending passes an `approved` flag (`policy.py:277`), and C8 deleted the shared one-use approvals.
   - That's a second approval system, which is over-built. Simpler: approving writes a standing allowlist entry through the owner's protected policy write. That keeps one writer and no grant store. Otherwise the spec should say plainly that it adds a grant store and justify it.

8. **Low. The spec re-adds what C7 deleted without citing who decided that.** `design.md:3`.
   - C7 explicitly deleted the `open`/`allowlist`/`ask` modes and replaced the per-owner allowlist (founder decision of 2026-10-20). This spec re-adds them, "superseding" C7's rule.
   - Defaulting to `open` keeps the founder's open-web choice. But the spec should cite whatever says N5 (founder, 2026-10-11) still stands after the 2026-10-20 decision; the registry row alone isn't a decision record.

VERDICT: DISAGREE_EVIDENCE
