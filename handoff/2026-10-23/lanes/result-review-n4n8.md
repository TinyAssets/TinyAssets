I've read the diff and the yardsticks; now checking how N4 overlaps the existing browser-login-custody live view.

I found five problems, two of them high. The router and stream boundary for N4 is sound. The rest leaves decisions open or makes a second copy of something that already exists.

**Where the specs hold up**
- **Router (N4):** the router only authenticates, picks the caller's admitted box from a positive owner→box mapping and forwards opaque frames. The box checks the channel itself, and swapped owner/box IDs or withdrawn admission are refused (`live-box-takeover-view/spec.md:4`, `:11-13`). This matches T1 and T5. Waking a stopped box is left to the normal lifecycle (T1).
- **Shared storage (N4):** no shared component reads the stream or stores capture state (T4).
- **No computer online (N8):** every hosted surface keeps working, offline computers report unavailable, and a dropped action reports an unknown outcome instead of replaying (`optional-personal-computer-link/spec.md:15-25`). That satisfies Direction 8 and the `host-paths` guard.
- **Scope:** nothing is over-built in either spec.

**Findings**

1. **HIGH — N4 never says what keeps the agent out of the view inside the box.** `live-box-takeover-view/design.md:14` says "the box gates agent interaction", and `spec.md:16` says private input stays out of agent observations. But after ADR-022 P3 the agent loop runs inside the same box with bash. The spec doesn't decide whether capture, input injection and the browser's debug channel are reachable from the agent's bash and tools. `browser-login-custody` already requires "no agent-accessible profile or control channel"; N4 doesn't carry that over.
   - There is also no scenario showing that an agent trying to open, watch or drive the view is refused. "The owner starts and ends takeover" appears only in design.md, not in the spec.
   - **Fix:** have the spec state that the view server and browser run outside the agent's process reach, with no socket the agent can use. Add an "agent requests view, frames or input → refused" scenario.

2. **HIGH — N4 makes a second live view next to the one `browser-login-custody` already has.** `live-box-takeover-view/proposal.md:17-19` lists no modified capabilities. Yet `openspec/specs/browser-login-custody/spec.md:33-38` already defines a "protected live owner view", which receives login frames and input in "the owner's isolated cell" (cells were deleted in P2b).
   - The result is two view contracts. N4's non-goal (`design.md:9`) rules out a second *custody* path but not a second *view* path.
   - **Fix:** list `browser-login-custody` as modified, so its protected view becomes the `BoxProvider.view` takeover and "cell" becomes the box.

3. **MEDIUM-HIGH — N8 doesn't decide who holds the pairing credential or how the companion's link enters and leaves the box.** `optional-personal-computer-link/design.md:13` keeps the broker "scoped to its existing duties", and `:14` only says "outbound, owner-bound connection".
   - Nothing names the door the companion's link comes in through (Fact 11 lists only `/mcp`, `/api`, `/hooks/inbound`).
   - Nothing says who issues, checks and revokes the device credential (T6 says box-ingress credentials are per box). The yardstick asks for this in broker custody.
   - Nothing says whether box→companion traffic rides the router channel or goes through the egress proxy. T6 refuses private destinations, so a direct box→PC path is impossible.
   - `tasks.md:3` "using existing device identity" doesn't settle any of this.

4. **MEDIUM — N8 leaves the overlap with the open `companion-local-mcp` change undecided.** `design.md:14` says it "can reuse `companion-local-mcp`", and `proposal.md:19` says that work "may supply a connector package". Two open changes now cover the same desktop companion, against S21 (one queue, the `superseded-open` guard).
   - **Fix:** N8 should fold or supersede `companion-local-mcp`, including that change's credential decision (an OS-keystore device credential bound to the owner, device and connection incarnation). That would also settle finding 3.

5. **MEDIUM — N8 says nothing about the existing tray host.** Registry row N8 (`docs/architecture.md:189`) lists "tray host `desktop/` (S15)" as current code, and `tinyassets/desktop/tray.py` and `desktop-app/` both exist. N8 only rules out a *required* tray (`design.md:9`). It doesn't say whether the tray is deleted or becomes the companion. Directions 5 and 6 want that decided and deleted in the same change.

6. **LOW — No N4 task builds the guard or updates the registry.** `live-box-takeover-view/tasks.md:9` runs the `box-view` guard, but the registry still lists it as planned and no task creates it. Neither change has a task to update the N4/N8 registry rows. (Both `.openspec.yaml` files also say `created: 2026-10-23`, a day after today.)

VERDICT: DISAGREE_EVIDENCE
