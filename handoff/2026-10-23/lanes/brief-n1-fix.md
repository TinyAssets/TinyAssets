Fix round on the N1 inbound-door spec, PR #5228 (worktree C:\Users\Jonathan\Projects\wf-n1-spec, branch
spec/n1-inbound-door). An independent review returned DISAGREE_EVIDENCE; full text:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-n1.md
Resolve every finding in the spec (decide, don't list options):
- HIGH: close the routing decision. Choose the option where the shared door never parses payloads: it maps the
  endpoint (an opaque per-binding route id in the URL) to one owner's box, verifies only what the channel's data
  profile declares without reading business content, then wakes and forwards the raw request; parsing and routing to
  an agent happen in the box. If verification needs a secret, say where it runs so T1 (router forwards with the
  control plane down for an awake box) still holds, or state the exact exception and why.
- Unadmitted owner and maintenance: match today's statuses (503 during maintenance/cutover windows; a stable refusal
  otherwise) and say which.
- Legacy generic webhooks without an event id: decide (derive an id from a payload digest within a window, or
  require the sender's id) and say what happens to existing hooks.
- Make the "one route, every event lands as session.start" test real: name the test file and assertions.
- Bound receipt retention by the channel's freshness window; state that a credential reference resolves only within
  the binding owner's broker custody.
- Trim any shared surface the review called over-built.
Push to the same branch. Keep it short.
