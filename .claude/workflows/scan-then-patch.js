export const meta = {
  name: 'scan-then-patch',
  description: 'Codex-first sweep: Haiku scans each file, the other model family verifies each finding, Sol patches each verified finding in its own worktree, the other family checks each diff',
  whenToUse: 'A sweep where each fix is independent. args: { objective: string, files: string[] }. Returns diffs only; nothing is committed or pushed.',
  phases: [
    { title: 'Scan', detail: 'one read-only Haiku agent per file', model: 'haiku' },
    { title: 'Verify', detail: 'Codex Sol refutes each finding (Sonnet if Codex is unavailable)' },
    { title: 'Patch', detail: 'Codex Sol edits in an isolated worktree (Sonnet if Codex is unavailable)' },
    { title: 'Check', detail: 'the other model family checks each diff' },
  ],
}

// Models are CLASSES, never versions: Claude stages use the family aliases
// (haiku, sonnet, fable), which Claude Code maps to the newest model of each
// family; Codex stages pass a class (luna, sol, astra) that scripts/peer_agent.py
// resolves to the newest listed model of that class on every run.
// Routing (which model, fall back, retry or stop) is plain script logic: no model
// call and no paid router. A Codex stage runs through a thin low-effort Haiku agent
// that launches scripts/peer_agent.py and returns its result.

const FINDINGS = { type: 'object', required: ['findings'], properties: { findings: { type: 'array', items: {
  type: 'object', required: ['file', 'claim', 'evidence'],
  properties: { file: { type: 'string' }, line: { type: 'integer' }, claim: { type: 'string' }, evidence: { type: 'string' } } } } } }
const VERDICT = { type: 'object', required: ['refuted', 'reason'], properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } } }
const PATCH = { type: 'object', required: ['diff'], properties: { diff: { type: 'string' }, tests: { type: 'string' } } }
const CHECK = { type: 'object', required: ['problems'], properties: { problems: { type: 'array', items: { type: 'string' } } } }
const CODEX_RUN = { type: 'object', required: ['exit_code', 'output'], properties: {
  exit_code: { type: 'integer' }, output: { type: 'string' }, diff: { type: 'string' }, model: { type: 'string' } } }

const objective = args && args.objective
const files = (args && args.files) || []
if (!objective || !files.length) throw new Error('scan-then-patch needs args { objective, files }')
const READ_ONLY = 'Read only: do not create, edit or delete repository files, and do not change git state.'
const MAX_ROUNDS = 3

// One Codex job on a model class. exit_code != 0 means "use the Claude fallback".
async function codex(task, { label, phase, modelClass, effort, write = false, schema }) {
  const run = await agent(`Run one Codex job and report its result. Do not do the task yourself.
1. Write the task below, verbatim, to a new file under the OS temp directory (Python tempfile.gettempdir()) named ${label.replace(/[^a-z0-9-]/gi, '_')}.md.
2. Run with Bash from the repository root (timeout 2400000 ms):
   python scripts/peer_agent.py codex --model ${modelClass} --effort ${effort} --timeout 2300 ${write ? '--write --cwd "$(pwd)" ' : ''}--prompt-file <that file> --out <that file>.out
3. Return exit_code (the command's exit status), output (the full contents of the .out file) and model (the resolved model the wrapper printed).${write ? '\n4. Also return diff: the output of `git diff` in this working directory.' : ''}
TASK:
${task}${schema ? `\n\nEnd your answer with ONE JSON object matching: ${JSON.stringify(schema)}` : ''}`,
    { label: `codex ${modelClass} ${label}`, phase, model: 'haiku', effort: 'low', schema: CODEX_RUN, ...(write ? { isolation: 'worktree' } : {}) })
  if (!run || run.exit_code !== 0) return null
  if (!schema) return run
  const tail = run.output.slice(run.output.lastIndexOf('{'))
  try { return { ...JSON.parse(tail), diff: run.diff } } catch { return null }
}

// Haiku scanned, so the verifier is the other family: Codex Sol, else Sonnet.
async function verify(f) {
  const task = `${READ_ONLY}\nYou did not write this finding. Try to refute it; default to refuted=true if unsure.\n${JSON.stringify(f)}`
  const v = (await codex(task, { label: `verify-${f.file}`, phase: 'Verify', modelClass: 'sol', effort: 'medium', schema: VERDICT }))
    || (await agent(task, { label: `verify ${f.file}`, phase: 'Verify', model: 'sonnet', schema: VERDICT }))
  return v && !v.refuted ? f : null
}

async function patch(f, rejected, { escalated = false } = {}) {
  const task = `Fix only this verified finding. Run the tests you touch. Do not commit, push or change git config.\n${JSON.stringify(f)}${rejected ? `\nA previous attempt was rejected for: ${rejected.join('; ')}` : ''}`
  const viaCodex = await codex(task, { label: `patch-${f.file}`, phase: 'Patch', write: true,
    modelClass: escalated ? 'astra' : 'sol', effort: escalated ? 'xhigh' : 'high' })
  if (viaCodex && viaCodex.diff) return { diff: viaCodex.diff, by: 'codex' }
  const p = await agent(task, { label: `patch ${f.file}`, phase: 'Patch', model: escalated ? 'fable' : 'sonnet', isolation: 'worktree', schema: PATCH })
  return p ? { diff: p.diff, by: 'claude' } : null
}

// The check always comes from the other family than the patch.
async function check(f, p) {
  const task = `${READ_ONLY}\nCheck that this diff fixes the finding and breaks nothing. List every problem; an empty list means it is correct.\nFinding: ${JSON.stringify(f)}\nDiff:\n${p.diff}`
  if (p.by === 'codex') return agent(task, { label: `check ${f.file}`, phase: 'Check', model: 'sonnet', schema: CHECK })
  return (await codex(task, { label: `check-${f.file}`, phase: 'Check', modelClass: 'sol', effort: 'medium', schema: CHECK }))
    || agent(task, { label: `check ${f.file}`, phase: 'Check', model: 'haiku', schema: CHECK })
}

// Stop when clean, after two rounds in a row without fewer problems, or at round 3;
// then one escalated round (Astra xhigh, else Fable) before reporting escalate.
async function patchLoop(f) {
  let best = Infinity, stalled = 0, rounds = 0, last = null
  while (stalled < 2 && rounds < MAX_ROUNDS) {
    rounds++
    const p = await patch(f, last && last.problems)
    if (!p) return { finding: f, rounds, diff: null, problems: ['patch failed on Codex and Claude'], escalate: true }
    const c = await check(f, p)
    last = { diff: p.diff, by: p.by, problems: c ? c.problems : ['check failed'] }
    if (!last.problems.length) return { finding: f, rounds, diff: p.diff, by: p.by, problems: [], escalate: false }
    if (last.problems.length < best) { best = last.problems.length; stalled = 0 } else stalled++
  }
  const e = await patch(f, last.problems, { escalated: true })
  if (e) {
    const c = await check(f, e)
    if (c && !c.problems.length) return { finding: f, rounds: rounds + 1, diff: e.diff, by: e.by, problems: [], escalate: false }
  }
  log(`stopped ${f.file} after ${rounds + 1} rounds; escalate to the lead for a simplicity check`)
  return { finding: f, rounds: rounds + 1, diff: last.diff, by: last.by, problems: last.problems, escalate: true }
}

const perFile = await pipeline(files,
  file => agent(`${READ_ONLY}\nObjective: ${objective}\nFile: ${file}\nReport every candidate in this file, quoting the evidence.`,
    { phase: 'Scan', label: `scan ${file}`, model: 'haiku', effort: 'low', schema: FINDINGS }),
  scan => parallel(((scan && scan.findings) || []).map(f => () => verify(f))),
  verified => parallel(verified.filter(Boolean).map(f => () => patchLoop(f))),
)

return perFile.filter(Boolean).flat()
