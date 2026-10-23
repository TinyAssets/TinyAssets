Adversarial delta review of branch conv/source-records, range d90a5e23b32..9ab39b4b886
(worktree C:\Users\Jonathan\Projects\wf-source-records; read with `git diff d90a5e23b32..9ab39b4b886 -- <path>`).
A previous review of d90a5e23b32 found: discovery stalling the shared broker; sign-in token to a cross-site host;
discovered sources assumed flat-rate; source records changing after acceptance. Check those are really fixed, then
BREAK the new hunks. Focus:
- Box image change installing subscription CLIs (7d55a06f589): integrity/lock verification at build, read-only path,
  no network or registry trust at runtime, nothing per-user written outside the owner's home.
- Disconnect (45569df8279 and 3a21c2dfa3f): path traversal or deleting outside the owner's home; deleting the shared
  image copy; one owner affecting another.
- Drain/sleep cancelling pending sign-ins (f17aad31519): can real work now be dropped or a box evicted mid-turn?
- Discovery bare-domain probing (1a8ea1a158a): SSRF to private addresses, probe budget still bounded.
- Live-hotfix folds (d542bb8a929): dropping "dead" sources/members — can a merely-transient failure silently remove an
  owner's accepted source or broaden access? Stale default fallback — can it pick a paid model?
- Page (main.js): XSS via source names/codes/answer titles (textContent vs innerHTML), copy button.
Report each finding with file:line, a concrete failure scenario and severity. End with exactly one line:
VERDICT: AGREE (no floor/correctness defect) or VERDICT: DISAGREE_EVIDENCE.
HARD CONSTRAINTS: no sub-agents, no peer_agent.py, no new worktree; run at most the test files you need; read-only,
do not edit or commit. Budget about 25 minutes.
