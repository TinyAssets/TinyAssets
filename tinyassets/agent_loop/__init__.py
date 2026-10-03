"""The thin agent loop: model turns in the platform, tools in the box.

Target architecture D6 (``openspec/changes/target-architecture/design.md``),
slice S7; change ``control-plane-agent-loop``.

A turn that speaks a standard HTTP model protocol is a coroutine in the
platform process (today the daemon; the execution owner of D11 once S8 lands),
not a subprocess. Each turn still has its own event loop: one shared loop
waits on a task-aware provider-assignment admission (that lock is keyed by
thread and held across the model call) and journal writes off the loop; see
the change's ``design.md``. The model call goes through the existing
credential broker (``ApiKeyHttpProvider`` -> ``resolve_exact_scoped_proxy``),
so no model credential is ever in the loop. The loop never executes model output: it
parses tool-call JSON and routes each call by NAME to exactly one place
(:mod:`.tool_session`):

* the four box tools (``read``/``write``/``edit``/``bash``) go to the turn's
  command-center box over a :class:`BoxHandle` bound once at turn start, each
  call carrying an ``op_id`` (:mod:`.box_tools`);
* the owner-door reads (``history``, ``activity``) are answered by the loop
  itself, read-only, and never reach the box (:mod:`.owner_reads`);
* every other served tool keeps its existing engine route, where its own gates
  (owner rules, auto-review, effect consent) already sit.

The per-round journal is the existing :class:`AgentTurnJournal`, unchanged.
An operation whose outcome is unknown is recorded as unknown and the turn
HOLDS; nothing replays.
"""
