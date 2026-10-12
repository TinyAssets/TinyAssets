# Cross-family review

Claude peer reviewed runtime commit 57dc894eb4. Final follow-up changes only verification records.

Overlay logic matches the jail's mount rules; now checking that `query` is already a param on each read_graph door and its types.

**Review of PR #4493 at 57dc894eb4, against merge-base 29fa5b4686.** I found no floor or correctness defects. I read the code and tests and did not run anything.

**Owner and session isolation.** All three doors pass `query` through to the same `read_conversation_page`, and none of them adds a session or store selector:
- the engine door: `tinyassets/engine_mcp_server.py:670`, bound by `require_founder_home` to `principal:{_ACTOR_ID}`
- the public door: `tinyassets/api/graph_reads.py:139`, bound by `memory_session(actor, …)`
- the owner door: `tinyassets/agent_loop/owner_reads.py:79`, bound to `principal:{owner}`, with `query` added to the argument allowlist

The search is added with `AND` after `session_id = ?`, so it cannot reach rows from another session. The `principal:other` row in the test confirms this. The engine door still wraps the payload in `_untrusted`, so the new previews get the same untrusted-evidence treatment as full reads.

**No-follow workspace inventory.** The inventory reads entries through `list_universe_entries`, which opens each path component with no-follow. Linked or reparse-point entries are skipped. A linked `.agent-workspace` raises `UniverseFileError` (a subclass of `OSError`), which fails closed.

**Same visible path as the tool jail.** The overlay rule in `universe_tools.py:1266-1277` matches `_jail_mounts` (`universe_tools.py:372-386`). The jail overlays every root entry that is not hidden, not a symlink, and is a directory or regular file. The inventory hides the matching workspace entry using `lstat` and the same three conditions. Hidden workspace entries are not overlaid by the jail and are shown, which also matches. Paths are shown as logical `/u` paths, and names are still escaped with `unicode_escape`.

**Search paging and exact reads.** Paging stays keyset (`id < offset`), and the query is applied on every page. `next_offset` is the last kept id, and the tests check ids 27→2 with no gaps. Matching is literal: `instr` on casefolded text, with no `LIKE`, so `%` and `_` are plain characters. The test confirms `%` returns nothing. `content` is `TEXT NOT NULL` (`conversation_store.py:85`), so the casefold function never receives NULL. Exact reads are unchanged and the test reassembles the full emoji/Greek text losslessly from chunks.

**Bounded output.**
- Queries are limited to 1000 characters.
- Each preview is at most 240 characters, and a page holds at most 20 messages, so previews add at most about 4.8K characters per page.
- The inventory shares a 200-entry scan budget with the platform folders, shows at most 40 lines, and goes three levels deep.
- The prompt says outright that the preview is not exhaustive and points to `find /u`.

**Plugin mirrors.** All six runtime files are byte-identical to `tinyassets/`.

**One non-blocking nit (not floor):** at `universe_tools.py:1297-1300`, a linked or non-directory `.agent-workspace` raises an `OSError` that the outer `except` catches. That blanks the whole folder section, including the platform notes, prompts and workflows that used to be listed. It still fails closed. If you want, the minimal fix is to catch `OSError` around `read(WORKSPACE_DIR)` instead of only `FileNotFoundError`.

**Diagnosis — AGREE.** The evidence supports two causes for the denial:
1. The mortgage exchange (rows 2130/2131) had dropped out of context by the chart turn. The loader took the last 20 messages (2137–2156), and the 7,000-character cap then kept only 2149–2156.
2. The inventory listed only the platform-root folders, never `.agent-workspace/exports`, even though the files survived on the persistent volume.

The design doc correctly avoids claiming a particular search command ran, or that files were deleted.

**Approach — AGREE.** The approach adds no new store or selector. It reuses the owner-bound route, widens the inventory to the workspace through the same no-follow reader, and tells the agent that history is a window and how to retrieve the rest. That fixes both causes with no storage changes.

**Lane collision:** none seen. The PR touches only the listed files plus a new openspec change, and it makes no deployment claim, which is consistent with no shipped claim yet.

VERDICT: APPROVE
