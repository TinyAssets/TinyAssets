# network-narrow-bound as built (2026-11-22)

Owed by #5761 (`network-answer.md`, found by its review): when the owner narrows the egress
policy while a sandbox runs, the `runs` lane re-applies the network to the live sandbox
(`launch.ts` `reapply`, `owner/src/sandbox.ts` `networkOutcome`). While re-applies keep
failing (lost, malformed, refused or unanswered), the sandbox keeps its old network: a host
the owner dropped stays reachable by a signed-in CLI, git and every tool's direct call (only
the egress door, an API-key run's model calls, fails closed). The retry runs every
`NETWORK_RETRY_S` (30 s), bounded only by the lease's life, which each runner batch renews;
the policy write answers `ok`, and nothing tells the owner the narrowing is not in force.

## Research (before the build)

1. **The egress design** (G4, E1 to E3: `untangle-2026-11-remaining-build.md` §M,
   `/home/user/ta-work/untangle/e2_research.md`, the owner-object spec's "A change ends the
   egress streams it covers" and its lease requirement, `owner/src/network.ts`'s header,
   `network-answer.md`).
   - The sandbox's network is E2B's, outside the VM: `denyOut` all traffic, `allowOut` `*`
     in `open` mode or the policy's destinations in `allowlist` mode, the egress door always
     last, one transform on the door setting the lease's egress token and the generation.
   - A write that changes the egress table moves the egress generation. The door then refuses
     a call under the old generation (`egress_policy_stale`) and one under the new generation
     until a re-apply is out (`egress_network_stale`). That is the only fail-closed part, and
     it holds only calls through the door.
   - The re-apply (`SandboxService.network`) replaces the live sandbox's network whole, on the
     Worker's serial chain. A paused sandbox keeps the plan and applies it as it resumes
     (`at_resume`). The owner records the send (`network_pending`) and the answer
     (`networkAnswered`): `applied` confirms it (`network_generation`), anything else clears
     the send, and the lane sends again `NETWORK_RETRY_S` on. A late answer (none within
     `LANE_CALL_MS`) records nothing.
   - E2's research chose "retry, stop nothing" because a re-apply has no job of its own and
     a resend is safe. It weighed liveness and the door, and never weighed the hosts a
     narrowing drops. No decision bounds the time a narrowing may stay out of force, and
     none tells the owner. `network-answer.md` §3 kept that rule, and its review found this
     gap.
   - What the platform already accepts as "unconfirmed for a while" is one lease's TTL
     (`LEASE_TTL_S`, 60 s). A run whose runner is unheard for that long is settled lost
     (`Runs.lost`), a lapsed lease's runs go with it, and the Worker's idle reap pauses a
     sandbox with no live run a TTL on.
2. **What E2B offers, and how fast** (the repo's E2B wire, `sandbox/e2b/transport.ts` and
   `sandbox/src/e2b.ts`, which use raw HTTP in the field names of E2B's OpenAPI document
   read 2026-11-03; no SDK is pinned in `platform/`, and `network.ts` cites the `e2b` SDK
   2.62.0's `SandboxNetworkUpdate`; the probes in `sandbox-probe.md`). docs.e2b.dev is not
   reachable from this session, so E2B's pages were read through search results (sources
   below).
   - `PUT /sandboxes/{id}/network` (the SDK's `updateNetwork`) replaces the running
     sandbox's egress rules whole, with no merge. The probe found it "applies at once to the
     running sandbox". E2B documents it for a running sandbox only: whether it takes a
     paused one is unprobed, which is why the Worker keeps the plan for the resume.
   - Stopping: `POST /sandboxes/{id}/pause` (measured 462 to 705 ms; 7.0 s once, after 6 GB
     were written) and `DELETE /sandboxes/{id}` (kill). Both are calls on the same API, from
     the same Worker, as the re-apply. So a fault that keeps a re-apply from landing (the
     Worker unreachable or overloaded, E2B's API failing) can keep a pause or kill from
     landing too. A stop that relies only on them is no stronger than the re-apply.
   - A stop that needs neither: the in-VM runner stops each CLI once its event door refuses
     it (`runner.ts` `Poster`: a 401, 403 or 404 stops the CLI, SIGTERM then a kill after
     its grace, and every process of the run's user is killed at the run's end). It posts at
     least every `HEARTBEAT_MS` (20 s). A run the owner settled, or a lease it gave back, is
     refused at its next post, so the workload stops within a heartbeat whatever the Worker
     or E2B's API do. A sign-in's runner is refused the same way once its row is gone
     (`Signins.frames`, `NO_SIGNIN`). The Worker's idle reap then pauses the sandbox (no live
     run at its look), and E2B's own timeout pauses one no renewal reaches.
   - Whether E2B's update cuts a connection already open to a host it drops is unprobed. In
     AWS's comparable firewall, a security group's tracked connections outlive a rule change
     (below). An applied narrowing may therefore leave an open stream alive, which only
     stopping the workload ends. That stays a live probe (owed below).
3. **What market leaders do when a narrowing cannot be confirmed.**
   - Cilium (Kubernetes' eBPF network policy) keeps a desired and a realized policy for
     each endpoint, as a revision. `cilium policy wait <revision>` blocks until every
     endpoint has realized it. An endpoint whose regeneration fails is `invalid`/not ready.
     The remedy its troubleshooting guides give for an endpoint left stale is to delete and
     recreate the pod: stop the workload, then start it under the realized policy.
   - AWS EC2 security groups: a rule change does not interrupt tracked connections. AWS's
     own example narrows an SSH rule and the open session survives. Cutting a flow means
     ending it (or, in AWS's isolation guidance, the instance), not only changing the rule.
   - OpenAI Codex cloud applies an environment's internet access setting when it sets up a
     task's container, after the setup script. A change is a new network for a new
     container, never an in-place edit of a running one (option b below).
   - The shared rule: a policy is not "in force" until it is realized, the realization is
     visible, and a workload that cannot be brought under it is stopped, never left on the
     wider policy.
4. **The options.**
   - **(a) Bound, then stop.** A narrowing not confirmed within a bound gives the lease
     back: its runs settled with a cause, its sign-ins ended, its tokens dead, its CLIs
     stopped (through the Worker, and by the runner at its next refused post, which needs
     neither the Worker nor E2B's API), its sandbox paused, and the owner told. It costs
     nothing while re-applies land, which is nearly always (one round trip), and a stop
     only in the failure this gap is about.
   - **(b) Stop before every narrowing.** The sandbox is stopped and restarted under the new
     network, with no in-place re-apply for a narrowing. Every narrowing would kill every
     live run even when the update would have landed in a second, and the stop goes
     through the same Worker and API, so a stop that fails is the same gap again. It
     trades a rare stop for a certain one and closes nothing (a) does not.
   - **(c) The write answers "pending".** This tells the owner, but bounds nothing: the
     dropped host stays reachable for as long as the re-applies fail, now in plain sight.
     It also adds a field to the policy door's contract and the app for a state that lasts
     one round trip when all is well.
   - **Chosen: (a).** It alone makes "a dropped host stays reachable silently" impossible:
     reachable at most for the bound, and the owner told when it is crossed. The window
     before the bound is the one the platform already accepts for an unheard runner. (c)'s
     owner-facing fact is kept where it matters, in the notice and the runs' cause, not in
     every write's answer.
5. **The bound: one lease's TTL (`LEASE_TTL_S`, 60 s).**
   - It is the platform's existing measure of how long a sandbox may run unconfirmed (point
     1), so this change adds no new number.
   - It fits two whole tries: the one the write's lane pass sends at once, and the retry
     `NETWORK_RETRY_S` (30 s) on, each answered within `LANE_CALL_MS` (15 s). That is 45 s
     before the bound, and a test holds it.
   - A re-apply waiting behind a renewal (`RENEWAL_MS`, about 10 s) fits inside the first
     try.
   - A sandbox still paused at the bound (`at_resume`) is treated the same: nothing runs in
     it, but a lease that has not posted for a TTL has lapsed anyway.
6. **Only a narrowing.** A widening leaves the old network narrower than the policy, so no
   host the owner dropped is reachable. It keeps the plain retry: API-key calls refused
   until it lands, nothing stopped.
   - A write narrows when the old network lets out a name the new one does not (an
     `allowOut` entry gone; `*` lets every name out). Comparing the entries as strings
     never misses a narrowing. It may call a change narrowing that a wider entry covers,
     and that is the safe side.
   - Across several writes, a host is dropped only if some write between the lease's
     confirmed network and the current one drops it. So marking each narrowing write is
     exact enough. The mark goes on each live lease, `sandbox_leases` being out of the
     export, so no exported table, capture or restore changes.

Sources: [E2B: update sandbox network](https://e2b.dev/docs/api-reference/sandboxes/update-sandbox-network),
[E2B: internet access](https://e2b.dev/docs/network/internet-access),
[Cilium: endpoint lifecycle](https://github.com/cilium/cilium/blob/ae8661f14532c9738ef5e78885ba6a9a31e79f99/Documentation/policy/lifecycle.rst),
[Cilium endpoint health](https://oneuptime.com/blog/post/2026-03-13-cilium-endpoint-health/view),
[AWS: security group connection tracking](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-connection-tracking.html),
[OpenAI Codex: cloud environments](https://developers.openai.com/codex/cloud/environments),
[OpenAI Codex: agent internet access](https://developers.openai.com/codex/cloud/agent-internet).
