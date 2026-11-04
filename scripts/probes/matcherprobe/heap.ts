/**
 * The sync-ignore matcher's memory, measured where the sync agent runs (`LIST_BYTES`'s
 * derivation, `limits-audit.md`): bundled by `probe.py`, run in a throwaway sandbox of the
 * pinned template with the sandbox's own Node.
 *
 *   node heap.js limit            the heap limit a sync agent has (no flags, as it is spawned)
 *   node heap.js peak <entries>   the agent's own peak over a sync-out of that many entries
 *   node --expose-gc heap.js classes <class> <bytes>   that class's heap a list byte
 *   node heap.js step             the automaton's ns a step (`STEP_NS`)
 *   node heap.js fit <class> <bytes> <ballast>   a list of that class compiles beside the peak
 */
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getHeapStatistics } from "node:v8";

import { ignoredBy, stepNanos } from "../../../platform/owner/src/sync-ignore";
import { openMirror, SyncClient, syncOut } from "../../../platform/sandbox/runner/sync";

const [mode, arg] = [process.argv[2], Number(process.argv[3] ?? 0)];
/** A head no two lines share (a trie's worst: every line its own branch from the root). */
const head = (i: number) => (Math.imul(i + 1, 2654435761) >>> 0).toString(36);
/**
 * Each class's lines, two ways: heads the lines share (`*.ext-…`), and heads that diverge
 * (round 7: a trie's or Aho–Corasick's worst): literal names and paths, a name's `*L` and
 * `L*`, a path's `L*` and `**\/L`, automaton lines (past its states, the rest held to be
 * said), negations.
 */
const SHAPES: Record<string, (i: number) => string> = {
  literalName: (i) => `name-${i.toString(36)}-xxxxxxxx`,
  literalPath: (i) => `dir/${i.toString(36)}/name-xxxx`,
  suffix: (i) => `*.ext-${i.toString(36)}-xxxx`,
  prefix: (i) => `pre-${i.toString(36)}-xxxx*`,
  pathPrefix: (i) => `dir/${i.toString(36)}/x*`,
  anywhere: (i) => `**/dir/${i.toString(36)}/x`,
  automaton: (i) => `*${i.toString(36)}?x*y`,
  negated: (i) => `!name-${i.toString(36)}-xxxx`,
  literalNameDiverging: (i) => `${head(i)}${i.toString(36)}`,
  literalPathDiverging: (i) => `${head(i)}/${i.toString(36)}`,
  suffixDiverging: (i) => `*${head(i)}${i.toString(36)}`,
  prefixDiverging: (i) => `${head(i)}${i.toString(36)}*`,
  pathPrefixDiverging: (i) => `${head(i)}/${i.toString(36)}*`,
  anywhereDiverging: (i) => `**/${head(i)}/${i.toString(36)}`,
};
const say = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`);
/** The heap and the typed arrays' backing stores: both hold the matcher. */
const used = () => process.memoryUsage().heapUsed + process.memoryUsage().arrayBuffers;

if (mode === "step") {
  say({ step_ns: stepNanos(3_000) });
} else if (mode === "limit") {
  say({ heap_size_limit: getHeapStatistics().heap_size_limit });
} else if (mode === "peak") {
  const root = mkdtempSync(join(tmpdir(), "heap-"));
  const local = join(root, "ws");
  // `arg` entries: folders of 499 files each, every one an entry.
  let made = 0;
  for (let d = 0; made < arg; d++) {
    const dir = join(local, `module-${d}`, "src");
    mkdirSync(dir, { recursive: true });
    made += 2;
    for (let f = 0; f < 498 && made < arg; f++, made++) {
      writeFileSync(join(dir, `component-file-number-${d}-${f}.ts`), `export const v = ${f};\n`);
    }
  }
  let version = 1;
  let peak = 0;
  const sample = () => {
    peak = Math.max(peak, process.memoryUsage().heapUsed);
  };
  const fetcher = (async (_url: string, init: { body: string }) => {
    sample();  // at each commit, when a batch's ops are held at their most
    const body = JSON.parse(init.body) as { ops?: { path: string; op: string }[] };
    return new Response(JSON.stringify({ outcomes: (body.ops ?? []).map((op) => ({
      path: op.path, outcome: "committed",
      entry: op.op === "remove" ? null : { version: version++ } })) }), { status: 200 });
  }) as unknown as typeof fetch;
  const timer = setInterval(sample, 10);
  const base = process.memoryUsage().heapUsed;
  const mirror = openMirror(join(root, "mirror.db"));
  const began = Date.now();
  const report = await syncOut(new SyncClient("https://egress.test", "t", 1, 2, fetcher),
                               { local, sessionsIn: join(root, "in"), sessionsOut: join(root, "out"),
                                 authority: "owner", records: new Map() }, mirror,
                               { lease_id: "l", epoch: 1 }, []);
  sample();
  clearInterval(timer);
  mirror.close();
  say({ entries: made, committed: report.committed, heap_base: base, heap_peak: peak,
        ms: Date.now() - began, heap_size_limit: getHeapStatistics().heap_size_limit });
} else if (mode === "fit") {
  // The ceiling's proof: with the agent's own peak held (`ballast` bytes of strings), a list
  // of `bytes` of one class compiles in the heap the agent is given (no flags).
  const [label, size, ballast] = [process.argv[3]!, Number(process.argv[4]),
                                  Number(process.argv[5])];
  const held: string[] = [];
  for (let n = 0; n < ballast; n += 1024) {
    held.push(`${n}`.padEnd(1000, "b") + `${Math.random()}`);
  }
  const shape = SHAPES[label]!;
  const lines: string[] = [];
  let bytes = 0;
  for (let i = 0; bytes < size; i++) {
    lines.push(shape(i));
    bytes += shape(i).length + 1;
  }
  const matcher = ignoredBy(lines);
  const kind = matcher("module-1/src/component-file-number-1.ts");
  say({ label, list_bytes: bytes, ballast, heap_used: process.memoryUsage().heapUsed,
        array_buffers: process.memoryUsage().arrayBuffers,
        heap_size_limit: getHeapStatistics().heap_size_limit, kind, held: held.length });
} else if (mode === "classes") {
  // One class a process, so nothing of another's is left in the heap: its list's lines and
  // the matcher compiled from them, held as the sync agent holds them, a list byte.
  const gc = (globalThis as { gc?: () => void }).gc!;
  const label = process.argv[3]!;
  const size = Number(process.argv[4]);
  const shape = SHAPES[label]!;
  gc();
  const base = used();
  const lines: string[] = [];
  let bytes = 0;
  for (let i = 0; bytes < size; i++) {
    const line = shape(i);
    lines.push(line);
    bytes += line.length + 1;
  }
  const matcher = ignoredBy(lines);
  matcher("module-1/src/component-file-number-1.ts");
  gc();
  const held = used() - base;
  say({ label, list_bytes: bytes, held, heap_per_list_byte: held / bytes,
        still: typeof matcher === "function" && lines.length > 0 });
}
