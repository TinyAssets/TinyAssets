Finish branch conv/source-records (worktree C:\Users\Jonathan\Projects\wf-source-records, head 6c141d70b93).
A previous builder was interrupted mid-item E; its uncommitted edits (model_discovery.py, test_model_source_detector.py,
spec) are in the worktree. Keep and finish them. Do each item below with one focused test; commit each separately.

E. Discovery from a bare domain also tries https://api.<domain> and the /openai/v1 and /api/v1 paths (typing groq.com
   finds Groq's API). Remove the leftover "Connect any OpenAI-compatible endpoint" line from the connect screen.
F. Disconnect leaves nothing (founder rule). Commit 3a21c2dfa3f keeps the per-user CLI. Change it: disconnect removes the
   record's declared state_paths, the connection marker AND the owner's per-user install dir (home/.ta-cli/<id>) if one
   exists. The shared read-only image copy (from 7d55a06f589) is never touched.
G. A pending sign-in never blocks a box from sleeping or draining. Today an open subscription login (a Login thread waiting
   on the CLI) counts as active work, so a deploy drain waited and every wake was refused with "owner daemon is still
   draining" for 15 minutes. On drain or sleep, cancel pending logins (login.cancelled.set()) and do not count them as
   active. The page, on a poll that fails or returns cancelled, shows "Sign-in was interrupted. Tap Connect to try again."
H. Fold two live page hotfixes. The scripts are in
   C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\c7e322aa-d19e-5fbc-8417-0781220e091b\scratchpad\
   - patch_select_copy.py: the #view-chat pointerdown handler calls preventDefault only on empty background (text,
     fields and dialogs keep native selection and copy); a copyButton(value,label) helper; the sign-in code is shown as a
     selectable <code> with a Copy button, and the status line redraws only when its content changes.
   - patch_answer_line.py: a user-role message that is the platform's "The owner answered your request." relay renders
     as one line ("You allowed: <title>" / "You declined: <title>"); the agent still receives the JSON.

Report the head SHA and the files per running role (daemon container, broker container, box harness, page).

HARD CONSTRAINTS: no sub-agents, no new worktree, no production access, no git config, never --no-verify, no full suite
(targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff and node --check). No model identifiers in
commits. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Budget about 45 minutes.
