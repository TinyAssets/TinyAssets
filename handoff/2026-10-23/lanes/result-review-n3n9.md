Still reviewing. I found old voice requirements in other specs that this PR doesn't touch, and I'm pinning down their exact lines before writing this up.

I'm disagreeing with this PR, based on what's in the repo. Its overall shape is right. Realtime is a streaming mode of the one `model_call` driven by the S3 loop, not a second loop. The broker is the only way out and the only holder of credentials. Media bytes live in the owner's box, and there are no provider names. But two things fail the yardstick: the old voice path survives in four other specs and the code, and the spending rules for realtime minutes and media aren't stated.

**High**

1. **The old voice path is only partly removed, so it would stay alongside the new one.** The PR removes all 8 requirements from `realtime-voice-conversation`, but four other specs still require the old bridge, and the PR doesn't touch them:
   - `openspec/specs/provider-routing/spec.md:878`: every spoken turn goes through `converse`, and the voice bridge stays as auxiliary transport. That directly contradicts N3.
   - `openspec/specs/provider-capability-negotiation/spec.md:9`, `:34` and `:51`: these define the `realtime_voice` capability, its `tinyassets.voice.v1` protocol and how capabilities are resolved.
   - `openspec/specs/credential-vault/spec.md:429`: "Realtime voice authority is universe-scoped".
   - `openspec/specs/identity-auth-and-access-control/spec.md:171`: "Voice-session signaling requires the authenticated founder identity".

   The code also has old-path pieces that the tasks don't name: the `realtime_voice` capability kind in `tinyassets/broker/capabilities.py:15`, the protocol, validator and capability entry in `tinyassets/storage/outbound_connections.py:2706`, `:2731`, `:2946` and `:5490`, the `realtime_voice` connection-tool example in `tinyassets/universe_server.py:1241` and `:1321`, and `public_voice_config` in `tinyassets/onboarding/__init__.py:142` and `:166`. Task 2.2 (`n3…/tasks.md:9`) lists only `realtime_voice.py`, `/voice/session`, the browser speech turn queue and the `converse` callback. Under "Modified Capabilities", `n3…/proposal.md:17-19` lists only `realtime-voice-conversation`. It also matters for capabilities: the new "declared or discovered contract" (design.md:15) would sit next to the old `realtime_voice` declaration with nothing saying which one wins.

2. **No spending or cap rules for realtime minutes or media.** N3 mentions only "parent allocation and receipt" (design.md:13) and revoking a grant or admission (spec.md:21-23). Nothing says how an open-ended duplex stream is reserved and charged against caps when its length is unknown. Nothing says a cap running out mid-stream stops upstream traffic and settles the call. N9 has a requirement that every call settles through C3 with one receipt (spec.md:10), but nothing says media output counts against the owner's caps. "No media-part subcall or separate spend path" (design.md:14) rules out a second path, but it doesn't make media output count toward the cap. T8 makes spending policy the broker's job, so each spec needs a requirement and a scenario for running out of budget mid-stream (cap reached during a stream → upstream stopped → receipt settled at its true usage).

**Medium**

3. **N3 and N9 disagree about whether streamed media is stored first, and N3 doesn't declare it depends on N9.** N9 `design.md:15` and `spec.md:18` say media bytes are written to the box's private store before references are published. N3 `design.md:16` and `spec.md:26` say partial frames and raw capture stay ephemeral, and realtime playback can't wait for a store write. Neither spec says whether realtime audio output is an N9 part (stored with a digest) or ephemeral frames. N3 emits "ordered output parts" (design.md:13) but its dependencies (proposal.md:23) list C3, C9 and the broker streaming contract, not N9.

4. **It's ambiguous whether committed audio input is kept.** `n3…/spec.md:26` stores "committed inputs" in the box but keeps "raw capture" ephemeral. For speech, the committed input is the captured audio. The spec should say whether it keeps a transcript, the audio, or both.

5. **What one call covers is left open.** "Each model exchange is `model_call`" (design.md:13) doesn't say whether a duplex session that lasts many turns is one call or one call per turn. That decides how receipts are counted, what gets allocated, and what interruption cancels. The interruption scenario (spec.md:6-8) assumes one call per reply, but the transport is one long stream.

6. **Source selection conflicts with Direction 4.** `design.md:15` picks "the strongest *eligible* connected source", which could quietly drop to a weaker source that supports realtime. `design.md:25` says a source without a compatible contract "reports unavailable", and the same line rules out "hidden voice fallback". The spec should pick one behavior.

**Low**

7. Owner-elected saving of raw capture (`n3…/design.md:16`, `spec.md:26`) is an extra feature, and nothing in N3 needs it. It's mild over-building; I'd drop it or defer it.
8. Both `.openspec.yaml` files say `created: 2026-10-23`, but today is 2026-10-22.
9. Task 2.1 checks with `test_realtime_uses_s3_model_call` and task 2.2 with `test_no_realtime_loop_beside_s3`, but neither test exists yet. The planned tests are fine; the tasks should just say they're new.

VERDICT: DISAGREE_EVIDENCE
