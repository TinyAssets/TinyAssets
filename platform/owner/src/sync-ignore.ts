/**
 * The sandbox sync's ignore list, read by both sides: the runner's sync agent
 * (`sandbox/runner/sync.ts`), which keeps what it names in the sandbox, and the owner's
 * object, which tells the owner what each change to it holds back (`owner-do.ts`). Pure: no
 * import, so the runner's bundle takes it whole.
 *
 * The sync-ignore file: what the sync never offers. Regenerable trees (dependencies, build
 * outputs, caches, derived indexes) are rebuilt in the sandbox, not kept as the owner's
 * files, so they never fill the owner's storage nor its history. Every agent may edit it
 * (the founder's every-agent-full-power rule), and nothing is hidden from the owner by it:
 * the file itself is never ignored, so every edit reaches the owner's files, where each
 * change is a receipt and a notice naming who made it, and saying which kept files a new
 * line holds back; a default pattern never stops syncing a path the owner already keeps,
 * only new ones, and each sync-out reports a kept file another line holds back. It is never
 * a wall: what it names stays in the sandbox, untouched.
 *
 * **Its lines mean what they mean in a `.gitignore` at `/workspace`** (the founder's ruling;
 * git's `dir.c` and `wildmatch.c`, with `git check-ignore` the test oracle): a line with no
 * `/` but a trailing one names a file or folder at any depth; one with a `/` is a path from
 * `/workspace`; a trailing `/` names folders only; `!` re-includes, the last line that
 * matches wins, and nothing under an ignored folder comes back; `*` and `?` never cross a
 * `/`, `**` does (`**\/`, `/**\/`, `/**`); `[...]` is a class; `\` escapes; matching is by
 * byte, as git's.
 *
 * **How it matches** (`matcher_research.md`): a line whose cost does not grow with the
 * number of lines goes into an index (literal sets; tries for a name's `*L` and `L*` and a
 * path's `L*`; Aho–Corasick for `**\/L`); every other line goes into one bit-parallel
 * automaton (Shift-And) whose cost per byte of text is ⌈S/32⌉ word steps whatever its lines'
 * shapes, S its states, bounded by `AUTOMATON_STATES`. The list's bytes are bounded by
 * `LIST_BYTES`. A line past either ceiling is not applied (its files go on syncing), and is
 * said, in file order.
 */
export const SYNC_IGNORE = "/workspace/.ta/sync-ignore";
/** The rule file from `/workspace`, which no line ever keeps out. */
const SYNC_IGNORE_REL = ".ta/sync-ignore";
export const DEFAULT_IGNORES: readonly string[] = Object.freeze([
  "node_modules", ".venv", "venv", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache",
  ".ruff_cache", ".tox", ".next", ".nuxt", ".turbo", ".parcel-cache", ".cache", "dist",
  "build", "target", ".gradle", "coverage", "graphify-out",
]);
const DEFAULT_NOTES: readonly string[] = Object.freeze([
  "# What the sync never keeps in your files: things your agents rebuild. Edit freely; the",
  "# lines mean what they mean in a .gitignore at /workspace (!, **, [ ] and / as git reads",
  "# them). A path already kept goes on syncing whatever a default line here says.",
]);

/** The sync-ignore file's lines as written when there is none: its notes, then the defaults. */
export function defaultSyncIgnore(): readonly string[] {
  return [...DEFAULT_NOTES, ...DEFAULT_IGNORES];
}

/**
 * The most states the automaton's lines take together (a line's: its tokens, each a byte, a
 * `?` or a class, plus one), each distinct line once, in file order: a technical ceiling,
 * the matcher's time. A byte of text costs ⌈S/32⌉ word steps, so at the files plane's own
 * ceilings a sync-out's matching stays well inside the sandbox's shortest session
 * (`limits-audit.md`: the arithmetic, with `STEP_NS` measured in the sandbox). Lines an
 * index takes cost none of it.
 */
export const AUTOMATON_STATES = 8_192;
/**
 * The most bytes of the list applied, in file order: a technical ceiling, the matcher's
 * memory in the sync agent's heap: (`SANDBOX_HEAP` − `AGENT_PEAK`) / `HEAP_PER_LIST_BYTE`,
 * measured in a sandbox of the template (`scripts/probes/matcherprobe/`), is 7.8 MiB with
 * the worst class's heads diverging, so 6 MiB (`limits-audit.md`).
 */
export const LIST_BYTES = 6 * 1024 * 1024;
/** The sync agent's heap limit in the sandbox (Node's own, the agent spawned with no flag). */
export const SANDBOX_HEAP = 460_587_008;
/** The sync agent's own heap at its peak over a sync-out of 500,000 entries, measured there. */
export const AGENT_PEAK = 70_027_000;
/**
 * The worst class's heap (and typed arrays) a list byte, its lines' heads diverging (round 7,
 * R7-3: a path's `L*` and a name's `*L`, 45.0 and 43.4; Aho–Corasick's `**\/L` 36.6).
 */
export const HEAP_PER_LIST_BYTE = 48;
/**
 * The most nanoseconds one automaton step (a byte of text, a 32-state word) may take in the
 * sandbox: the constant of `AUTOMATON_STATES`'s derivation, measured there by the template
 * build's standing check (`stepNanos`), which fails the build past it.
 */
export const STEP_NS = 6;

export type Kind = "custom" | "default";

// -- Bytes ------------------------------------------------------------------------------------

/** `text` as UTF-8 bytes (git matches by byte). */
function utf8(text: string): Uint8Array {
  const out = new Uint8Array(text.length * 3);
  let n = 0;
  for (let i = 0; i < text.length; i++) {
    let c = text.charCodeAt(i);
    if (c >= 0xD800 && c <= 0xDBFF && i + 1 < text.length) {
      const low = text.charCodeAt(i + 1);
      if (low >= 0xDC00 && low <= 0xDFFF) {
        c = 0x10000 + ((c - 0xD800) << 10) + (low - 0xDC00);
        i++;
      }
    }
    if (c < 0x80) {
      out[n++] = c;
    } else if (c < 0x800) {
      out[n++] = 0xC0 | (c >> 6);
      out[n++] = 0x80 | (c & 63);
    } else if (c < 0x10000) {
      out[n++] = 0xE0 | (c >> 12);
      out[n++] = 0x80 | ((c >> 6) & 63);
      out[n++] = 0x80 | (c & 63);
    } else {
      out[n++] = 0xF0 | (c >> 18);
      out[n++] = 0x80 | ((c >> 12) & 63);
      out[n++] = 0x80 | ((c >> 6) & 63);
      out[n++] = 0x80 | (c & 63);
    }
  }
  return out.subarray(0, n);
}

/** The UTF-8 length of `text`, without encoding it. */
function utf8Length(text: string): number {
  let n = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c < 0x80) {
      n += 1;
    } else if (c < 0x800) {
      n += 2;
    } else if (c >= 0xD800 && c <= 0xDBFF && i + 1 < text.length) {
      n += 4;
      i++;
    } else {
      n += 3;
    }
  }
  return n;
}

function decode(bytes: readonly number[]): string {
  return new TextDecoder().decode(Uint8Array.from(bytes));
}

const SLASH = 47;

// -- A line, as git reads it (dir.c) ----------------------------------------------------------

/** One rule line: its pattern's bytes for matching, and git's flags. */
interface Parsed {
  readonly bytes: Uint8Array;
  readonly negative: boolean;
  /** A trailing `/`: folders only. */
  readonly dirOnly: boolean;
  /** No `/` but a trailing one: a file's or folder's name at any depth (git's NODIR). */
  readonly name: boolean;
}

/** git's `trim_trailing_spaces`: trailing spaces go unless a backslash escapes them. */
function trimTrailingSpaces(line: string): string {
  let lastSpace = -1;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === " ") {
      if (lastSpace < 0) {
        lastSpace = i;
      }
    } else if (c === "\\") {
      i++;
      if (i >= line.length) {
        return line;
      }
      lastSpace = -1;
    } else {
      lastSpace = -1;
    }
  }
  return lastSpace < 0 ? line : line.slice(0, lastSpace);
}

/** A line as git reads it (`add_patterns_from_buffer`, `parse_path_pattern`), or null. */
function parse(raw: string): Parsed | null {
  let line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
  if (line === "" || line.startsWith("#")) {
    return null;
  }
  line = trimTrailingSpaces(line);
  let negative = false;
  if (line.startsWith("!")) {
    negative = true;
    line = line.slice(1);
  }
  let dirOnly = false;
  if (line.endsWith("/")) {
    dirOnly = true;
    line = line.slice(0, -1);
  }
  if (line === "") {
    return null;  // it matches nothing
  }
  const name = !line.includes("/");
  if (!name && line.startsWith("/")) {
    line = line.slice(1);  // `match_pathname` drops one leading `/`
  }
  return { bytes: utf8(line), negative, dirOnly, name };
}

/** The rule lines among `lines` (no comment, no blank), as the matcher reads each. */
export function ruleLines(lines: readonly string[]): string[] {
  return lines.map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));
}

// -- A pattern's tokens (wildmatch.c, by byte) ------------------------------------------------

/** 256 bits: the bytes a token takes. */
type ByteSet = Uint32Array;

/**
 * A pattern compiled to Shift-And tokens: each a byte set (`byte` its one byte, or -1);
 * `loop[i]` whether the state before token i (or the end) loops on bytes but `/` (a `*`);
 * `loopAll` the end looping on every byte (a trailing `/**`); `leading` the start re-armed
 * after every `/` (a leading `**\/`); `gadgets` states re-armed after a `/` once passed (a
 * `/**\/`).
 */
interface Tokens {
  readonly sets: ByteSet[];
  readonly bytes: number[];
  readonly loop: boolean[];
  loopAll: boolean;
  leading: boolean;
  readonly gadgets: Set<number>;
}

const POSIX: Readonly<Record<string, (c: number) => boolean>> = Object.freeze({
  alnum: (c: number) => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122),
  alpha: (c: number) => (c >= 65 && c <= 90) || (c >= 97 && c <= 122),
  blank: (c: number) => c === 32 || c === 9,
  cntrl: (c: number) => c < 32 || c === 127,
  digit: (c: number) => c >= 48 && c <= 57,
  graph: (c: number) => c > 32 && c < 127,
  lower: (c: number) => c >= 97 && c <= 122,
  print: (c: number) => c >= 32 && c < 127,
  punct: (c: number) => c > 32 && c < 127 && !((c >= 48 && c <= 57) || (c >= 65 && c <= 90)
                                               || (c >= 97 && c <= 122)),
  space: (c: number) => c === 32 || (c >= 9 && c <= 13),
  upper: (c: number) => c >= 65 && c <= 90,
  xdigit: (c: number) => (c >= 48 && c <= 57) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102),
});

/** The bytes `test` takes, never `/` (as `WM_PATHNAME` holds). */
function setOf(test: (c: number) => boolean): ByteSet {
  const set = new Uint32Array(8);
  for (let c = 0; c < 256; c++) {
    if (c !== SLASH && test(c)) {
      set[c >> 5]! |= 1 << (c & 31);
    }
  }
  return set;
}

/** The class at `p[at]` (a `[`) as `wildmatch.c` reads it, and its `]`; null: no match. */
function classAt(p: Uint8Array, at: number): { set: ByteSet; end: number } | null {
  let i = at + 1;
  let c = p[i];
  if (c === 94) {  // ^
    c = 33;
  }
  const negated = c === 33;  // !
  if (negated) {
    i++;
    c = p[i];
  }
  const member = new Uint8Array(256);
  let prev = 0;
  for (;;) {
    if (c === undefined) {
      return null;
    }
    if (c === 92) {  // \
      i++;
      c = p[i];
      if (c === undefined) {
        return null;
      }
      member[c] = 1;
    } else if (c === 45 && prev !== 0 && p[i + 1] !== undefined && p[i + 1] !== 93) {
      i++;
      c = p[i]!;
      if (c === 92) {
        i++;
        c = p[i];
        if (c === undefined) {
          return null;
        }
      }
      for (let b = prev; b <= c; b++) {
        member[b] = 1;
      }
      c = 0;  // so that `prev` becomes 0, as git's
    } else if (c === 91 && p[i + 1] === 58) {  // [:
      const start = i + 2;
      let j = start;
      while (p[j] !== undefined && p[j] !== 93) {
        j++;
      }
      if (p[j] === undefined) {
        return null;
      }
      if (j - start - 1 < 0 || p[j - 1] !== 58) {
        member[91] = 1;  // no `:]`: a plain `[`, the rest read on
      } else {
        const test = POSIX[String.fromCharCode(...p.subarray(start, j - 1))];
        if (test === undefined) {
          return null;  // a malformed [:class:]
        }
        for (let b = 0; b < 256; b++) {
          if (test(b)) {
            member[b] = 1;
          }
        }
        i = j;
        c = 0;
      }
    } else {
      member[c] = 1;
    }
    prev = c;
    i++;
    c = p[i];
    if (c === 93) {
      break;
    }
  }
  return { set: setOf((b) => (member[b] === 1) !== negated), end: i };
}

/** `p` as Shift-And tokens (`path`: as `WM_PATHNAME` reads `**`), or null: no match. */
function tokenize(p: Uint8Array, path: boolean): Tokens | null {
  const out: Tokens = { sets: [], bytes: [], loop: [false], loopAll: false, leading: false,
                        gadgets: new Set() };
  const push = (set: ByteSet, b: number) => {
    out.sets.push(set);
    out.bytes.push(b);
    out.loop.push(false);
  };
  const literal = (b: number) => {
    const set = new Uint32Array(8);
    set[b >> 5]! |= 1 << (b & 31);
    push(set, b);
  };
  // git compares a path pattern's literal head first and hands `wildmatch` the rest, so a
  // `**` right after that head reads as at the pattern's start (`match_pathname`).
  let head = 0;
  while (head < p.length && p[head] !== 42 && p[head] !== 63 && p[head] !== 91
         && p[head] !== 92) {
    head++;
  }
  for (let i = 0; i < p.length; i++) {
    const c = p[i]!;
    if (c === 92) {  // \
      i++;
      if (i >= p.length) {
        return null;  // a trailing backslash never matches
      }
      literal(p[i]!);
    } else if (c === 63) {  // ?
      push(setOf(() => true), -1);
    } else if (c === 91) {  // [
      const cls = classAt(p, i);
      if (cls === null) {
        return null;
      }
      push(cls.set, -1);
      i = cls.end;
    } else if (c === 42) {  // *
      let j = i;
      while (p[j + 1] === 42) {
        j++;
      }
      const state = out.sets.length;
      const before = i === 0 || p[i - 1] === SLASH || i === head;
      const after = j + 1 >= p.length || p[j + 1] === SLASH
        || (p[j + 1] === 92 && p[j + 2] === SLASH);
      if (path && j > i && before && after) {
        if (j + 1 >= p.length) {
          out.loopAll = true;  // a trailing `**`: everything below
          out.loop[state] = true;
        } else {
          // `**\/`: any folders, or none; the `/` after it is its own.
          j += p[j + 1] === 92 ? 2 : 1;
          if (state === 0) {
            out.leading = true;
          } else {
            out.gadgets.add(state);
          }
        }
      } else {
        out.loop[state] = true;
      }
      i = j;
    } else {
      literal(c);
    }
  }
  return out;
}

function statesOf(tokens: Tokens): number {
  return tokens.sets.length + 1;
}

// -- The classes -----------------------------------------------------------------------------

/** Tokens `from` to `to` as bytes, when each is one byte; or null. */
function literalOf(tokens: Tokens, from: number, to: number): number[] | null {
  const out: number[] = [];
  for (let i = from; i < to; i++) {
    if (tokens.bytes[i]! < 0) {
      return null;
    }
    out.push(tokens.bytes[i]!);
  }
  return out;
}

type Class =
  | { readonly kind: "literal" | "suffix" | "prefix" | "anywhere"; readonly bytes: number[] }
  | { readonly kind: "automaton" };

/** The structure that takes a line: an index, when its cost does not grow with the lines. */
function classOf(tokens: Tokens, name: boolean): Class {
  const n = tokens.sets.length;
  const loops = tokens.loop.flatMap((loop, i) => loop ? [i] : []);
  const plain = tokens.gadgets.size === 0 && !tokens.loopAll;
  const all = literalOf(tokens, 0, n);
  if (all === null || !plain) {
    return { kind: "automaton" };
  }
  if (!tokens.leading) {
    if (loops.length === 0) {
      return { kind: "literal", bytes: all };
    }
    if (loops.length === 1 && loops[0] === n) {
      return { kind: "prefix", bytes: all };  // `L*`
    }
    if (name && loops.length === 1 && loops[0] === 0) {
      return { kind: "suffix", bytes: all };  // a name's `*L`
    }
  } else if (!name && loops.length === 0) {
    return { kind: "anywhere", bytes: all };  // `**/L`
  }
  return { kind: "automaton" };
}

// -- The indexes -----------------------------------------------------------------------------

/** The last line (highest index) that matches, for a file and for a folder; -1: none. */
interface Best {
  any: number;
  all: number;
}

function better(best: Best, rule: number, dirOnly: boolean): void {
  if (!dirOnly && rule > best.any) {
    best.any = rule;
  }
  if (rule > best.all) {
    best.all = rule;
  }
}

/**
 * A trie over bytes: its edges a map a byte (from node to node, so every key and value is a
 * small integer V8 keeps unboxed, and each map grows apart), its nodes' bests in arrays.
 */
class Trie {
  readonly next: (Map<number, number> | undefined)[] = new Array(256).fill(undefined);
  any = new Int32Array(16).fill(-1);
  all = new Int32Array(16).fill(-1);
  nodes = 1;

  child(node: number, b: number): number {
    return this.next[b]?.get(node) ?? -1;
  }

  /** A new node, its bests none. */
  grow(): number {
    if (this.nodes === this.any.length) {
      const any = new Int32Array(this.nodes * 2).fill(-1);
      const all = new Int32Array(this.nodes * 2).fill(-1);
      any.set(this.any);
      all.set(this.all);
      this.any = any;
      this.all = all;
    }
    return this.nodes++;
  }

  /** The node `bytes` (from their end, `reverse`) lead to, made as needed. */
  insert(bytes: readonly number[], reverse: boolean): number {
    let node = 0;
    for (let i = 0; i < bytes.length; i++) {
      const b = bytes[reverse ? bytes.length - 1 - i : i]!;
      let to = this.child(node, b);
      if (to < 0) {
        to = this.grow();
        (this.next[b] ??= new Map()).set(node, to);
      }
      node = to;
    }
    return node;
  }

  mark(node: number, rule: number, dirOnly: boolean): void {
    if (!dirOnly && rule > this.any[node]!) {
      this.any[node] = rule;
    }
    if (rule > this.all[node]!) {
      this.all[node] = rule;
    }
  }

  /** Its arrays cut to its nodes, once made (their doubling's slack let go). */
  trim(): void {
    this.any = this.any.slice(0, this.nodes);
    this.all = this.all.slice(0, this.nodes);
  }

  collect(node: number, best: Best): void {
    best.any = Math.max(best.any, this.any[node]!);
    best.all = Math.max(best.all, this.all[node]!);
  }

  /** Every line whose bytes `text` starts with (or, `reverse`, ends with), into `best`. */
  walk(text: Uint8Array, reverse: boolean, best: Best): void {
    let node = 0;
    this.collect(0, best);
    for (let i = 0; i < text.length; i++) {
      node = this.child(node, text[reverse ? text.length - 1 - i : i]!);
      if (node < 0) {
        return;
      }
      this.collect(node, best);
    }
  }
}

/**
 * Aho–Corasick over `/` + each `**\/L` line's `L`, run over `/` + a path: a node's best is
 * its own and its failure chain's, so the lines whose `L` ends the path at a segment's start
 * are one lookup at its end.
 */
class Anywhere {
  readonly trie = new Trie();
  fail = new Int32Array(1);
  /** Each node's first child and next sibling, and the byte into it: the BFS's order. */
  first = new Int32Array(16).fill(-1);
  sibling = new Int32Array(16).fill(-1);
  into = new Uint8Array(16);

  add(bytes: readonly number[], rule: number, dirOnly: boolean): void {
    const t = this.trie;
    let node = 0;
    for (const b of [SLASH, ...bytes]) {
      let to = t.child(node, b);
      if (to < 0) {
        to = this.#edge(node, b);
      }
      node = to;
    }
    t.mark(node, rule, dirOnly);
  }

  /** A new child of `node` by `b`, its sibling links kept for the build's walk. */
  #edge(node: number, b: number): number {
    const t = this.trie;
    const to = t.grow();
    (t.next[b] ??= new Map()).set(node, to);
    if (to >= this.first.length) {
      const grow = (old: Int32Array) => {
        const out = new Int32Array(old.length * 2).fill(-1);
        out.set(old);
        return out;
      };
      this.first = grow(this.first);
      this.sibling = grow(this.sibling);
      const into = new Uint8Array(this.into.length * 2);
      into.set(this.into);
      this.into = into;
    }
    this.into[to] = b;
    this.sibling[to] = this.first[node]!;
    this.first[node] = to;
    return to;
  }

  build(): void {
    const t = this.trie;
    this.fail = new Int32Array(t.nodes);
    const queue = new Int32Array(t.nodes);
    let tail = 0;
    for (let c = this.first[0]!; c >= 0; c = this.sibling[c]!) {
      queue[tail++] = c;
    }
    for (let q = 0; q < tail; q++) {
      const u = queue[q]!;
      for (let v = this.first[u]!; v >= 0; v = this.sibling[v]!) {
        const b = this.into[v]!;
        let f = this.fail[u]!;
        while (f !== 0 && t.child(f, b) < 0) {
          f = this.fail[f]!;
        }
        const to = t.child(f, b);
        const fv = to >= 0 && to !== v ? to : 0;
        this.fail[v] = fv;
        // Its own best or its failure's (nearer the root, so done first).
        t.any[v] = Math.max(t.any[v]!, t.any[fv]!);
        t.all[v] = Math.max(t.all[v]!, t.all[fv]!);
        queue[tail++] = v;
      }
    }
    // The walk's links are the build's alone: let go, and the trie's arrays cut to size.
    this.first = new Int32Array(0);
    this.sibling = new Int32Array(0);
    this.into = new Uint8Array(0);
    t.trim();
  }

  /** The node after `bytes`, from `node`. */
  feed(node: number, bytes: Uint8Array): number {
    let at = node;
    for (const b of bytes) {
      let to = this.trie.child(at, b);
      while (to < 0 && at !== 0) {
        at = this.fail[at]!;
        to = this.trie.child(at, b);
      }
      at = to < 0 ? 0 : to;
    }
    return at;
  }
}

// -- The automaton ---------------------------------------------------------------------------

interface AutomatonLine {
  readonly tokens: Tokens;
  readonly rule: () => number;
  readonly dirOnly: boolean;
}

/** One bit-parallel Shift-And automaton over a set of lines, run by byte. */
class Automaton {
  readonly W: number;
  readonly states: number;
  readonly mask: Uint32Array;
  readonly loopAny: Uint32Array;
  readonly loopAll: Uint32Array;
  readonly init: Uint32Array;
  readonly rearm: Uint32Array;
  readonly gadget: Uint32Array;
  readonly acceptAny: Uint32Array;
  readonly acceptAll: Uint32Array;
  /** At an accepting state, the rule it accepts for. */
  readonly rule: Int32Array;
  readonly hasRearm: boolean;
  readonly hasGadget: boolean;

  constructor(lines: readonly AutomatonLine[], states: number) {
    this.states = states;
    const W = Math.max(1, Math.ceil(states / 32));
    this.W = W;
    this.mask = new Uint32Array(256 * W);
    this.loopAny = new Uint32Array(W);
    this.loopAll = new Uint32Array(W);
    this.init = new Uint32Array(W);
    this.rearm = new Uint32Array(W);
    this.gadget = new Uint32Array(W);
    this.acceptAny = new Uint32Array(W);
    this.acceptAll = new Uint32Array(W);
    this.rule = new Int32Array(32 * W).fill(-1);
    const bit = (array: Uint32Array, at: number, offset = 0) => {
      array[offset + (at >>> 5)]! |= 1 << (at & 31);
    };
    let at = 0;
    let rearm = false;
    for (const line of lines) {
      const { tokens } = line;
      bit(this.init, at);
      if (tokens.leading) {
        bit(this.rearm, at);
        rearm = true;
      }
      for (const state of tokens.gadgets) {
        bit(this.gadget, at + state);
      }
      tokens.loop.forEach((loop, i) => {
        if (loop) {
          bit(this.loopAny, at + i);
        }
      });
      const end = at + tokens.sets.length;
      if (tokens.loopAll) {
        bit(this.loopAll, end);
      }
      tokens.sets.forEach((set, i) => {
        for (let b = 0; b < 256; b++) {
          if ((set[b >> 5]! >>> (b & 31)) & 1) {
            bit(this.mask, at + i + 1, b * W);
          }
        }
      });
      bit(this.acceptAll, end);
      if (!line.dirOnly) {
        bit(this.acceptAny, end);
      }
      this.rule[end] = line.rule();
      at = end + 1;
    }
    this.hasRearm = rearm;
    this.hasGadget = this.gadget.some((word) => word !== 0);
  }

  /**
   * `bytes` from the state (D, I), in place (I: the `/**\/` lines passed); false once
   * nothing can match any more.
   */
  run(D: Uint32Array, I: Uint32Array, bytes: Uint8Array): boolean {
    const W = this.W;
    const { mask, loopAny, loopAll, rearm, gadget } = this;
    const gadgets = this.hasGadget;
    for (let k = 0; k < bytes.length; k++) {
      const b = bytes[k]!;
      const off = b * W;
      let carry = 0;
      let alive = 0;
      // In place: each word is read before it is written, its top bit carried to the next.
      if (b === SLASH) {
        for (let w = 0; w < W; w++) {
          const d = D[w]!;
          const inside = I[w]! | (d & gadget[w]!);
          const v = (((d << 1) | carry) & mask[off + w]!) | (d & loopAll[w]!) | rearm[w]!
            | inside;
          carry = d >>> 31;
          D[w] = v;
          I[w] = inside;
          alive |= v | inside;
        }
      } else if (gadgets) {
        for (let w = 0; w < W; w++) {
          const d = D[w]!;
          const inside = I[w]! | (d & gadget[w]!);
          const v = (((d << 1) | carry) & mask[off + w]!) | (d & loopAny[w]!);
          carry = d >>> 31;
          D[w] = v;
          I[w] = inside;
          alive |= v | inside;
        }
      } else {
        for (let w = 0; w < W; w++) {
          const d = D[w]!;
          const v = (((d << 1) | carry) & mask[off + w]!) | (d & loopAny[w]!);
          carry = d >>> 31;
          D[w] = v;
          alive |= v;
        }
      }
      if (alive === 0) {
        if (!this.hasRearm) {
          return false;
        }
        // Nothing alive: nothing starts again before the next `/`, after which leading ones
        // do, in this text or the next (a folder's child begins with one).
        const next = bytes.indexOf(SLASH, k + 1);
        if (next < 0) {
          return true;
        }
        D.set(rearm);
        k = next;
      }
    }
    return true;
  }

  /** The last rule the state accepts for, for a file and for a folder, into `best`. */
  accepted(D: Uint32Array, best: Best): void {
    best.any = Math.max(best.any, this.#last(D, this.acceptAny));
    best.all = Math.max(best.all, this.#last(D, this.acceptAll));
  }

  #last(D: Uint32Array, mask: Uint32Array): number {
    for (let w = this.W - 1; w >= 0; w--) {
      const hit = D[w]! & mask[w]!;
      if (hit !== 0) {
        return this.rule[w * 32 + 31 - Math.clz32(hit)]!;
      }
    }
    return -1;
  }
}

// -- The compiled list ------------------------------------------------------------------------

/** A line not applied, and why: past `AUTOMATON_STATES`, or past `LIST_BYTES`. */
export interface Unapplied {
  readonly line: string;
  readonly reason: "states" | "bytes";
}

interface Rule {
  readonly negative: boolean;
  readonly kind: Kind;
}

interface Compiled {
  readonly rules: Rule[];
  readonly names: Map<string, Best>;
  readonly paths: Map<string, Best>;
  readonly suffix: Trie;
  readonly prefix: Trie;
  readonly pathPrefix: Trie;
  readonly anywhere: Anywhere;
  readonly nameAutomaton: Automaton;
  readonly pathAutomaton: Automaton;
  readonly unapplied: Unapplied[];
}

/** `lines` compiled: each rule line into one structure, in file order, the ceilings held. */
function compile(lines: readonly string[]): Compiled {
  const defaults = new Set(DEFAULT_IGNORES);
  const rules: Rule[] = [];
  const names = new Map<string, Best>();
  const paths = new Map<string, Best>();
  const suffix = new Trie();
  const prefix = new Trie();
  const pathPrefix = new Trie();
  const anywhere = new Anywhere();
  const automata = { name: [] as AutomatonLine[], path: [] as AutomatonLine[] };
  const counted = { name: 0, path: 0 };
  // An automaton line met again takes its later place (the last match wins), and no states.
  const seen = new Map<string, { rule: number }>();
  const unapplied: Unapplied[] = [];
  let bytes = 0;
  let full = false;
  const literalInto = (into: Map<string, Best>, key: string, rule: number, dirOnly: boolean) => {
    const best = into.get(key) ?? { any: -1, all: -1 };
    better(best, rule, dirOnly);
    into.set(key, best);
  };
  for (const raw of lines) {
    bytes += utf8Length(raw) + 1;
    const parsed = parse(raw);
    if (parsed === null) {
      continue;
    }
    if (bytes - 1 > LIST_BYTES) {
      unapplied.push({ line: raw.trim(), reason: "bytes" });
      continue;
    }
    const record: Rule = { negative: parsed.negative,
                           kind: defaults.has(raw.trim()) ? "default" : "custom" };
    const tokens = tokenize(parsed.bytes, !parsed.name);
    if (tokens === null) {
      rules.push(record);  // a line git takes that matches nothing
      continue;
    }
    const cls = classOf(tokens, parsed.name);
    const rule = rules.length;
    if (cls.kind === "automaton") {
      const key = `${parsed.name ? "n" : "p"}${parsed.dirOnly ? "d" : "-"}${
        String.fromCharCode(...parsed.bytes)}`;
      const again = seen.get(key);
      if (again !== undefined) {
        rules.push(record);
        again.rule = rule;
        continue;
      }
      const cost = statesOf(tokens);
      if (full || counted.name + counted.path + cost > AUTOMATON_STATES) {
        // Past the ceiling: this and every later automaton line, in file order.
        full = true;
        unapplied.push({ line: raw.trim(), reason: "states" });
        continue;
      }
      rules.push(record);
      const entry = { rule };
      seen.set(key, entry);
      (parsed.name ? automata.name : automata.path)
        .push({ tokens, rule: () => entry.rule, dirOnly: parsed.dirOnly });
      counted[parsed.name ? "name" : "path"] += cost;
      continue;
    }
    rules.push(record);
    if (cls.kind === "literal") {
      literalInto(parsed.name ? names : paths, decode(cls.bytes), rule, parsed.dirOnly);
    } else if (cls.kind === "anywhere" && !cls.bytes.includes(SLASH)) {
      literalInto(names, decode(cls.bytes), rule, parsed.dirOnly);  // `**/name` is `name`
    } else if (cls.kind === "anywhere") {
      anywhere.add(cls.bytes, rule, parsed.dirOnly);
    } else if (cls.kind === "suffix") {
      suffix.mark(suffix.insert(cls.bytes, true), rule, parsed.dirOnly);
    } else {
      const trie = parsed.name ? prefix : pathPrefix;
      trie.mark(trie.insert(cls.bytes, false), rule, parsed.dirOnly);
    }
  }
  anywhere.build();
  suffix.trim();
  prefix.trim();
  pathPrefix.trim();
  return { rules, names, paths, suffix, prefix, pathPrefix, anywhere,
           nameAutomaton: new Automaton(automata.name, counted.name),
           pathAutomaton: new Automaton(automata.path, counted.path), unapplied };
}

/** The list's lines not applied, in file order, each with the ceiling it is past. */
export function unappliedLines(lines: readonly string[]): Unapplied[] {
  return compile(lines).unapplied;
}

/** What one line takes of `AUTOMATON_STATES`: its states, or 0 when an index takes it. */
export function automatonStates(line: string): number {
  const parsed = parse(line);
  const tokens = parsed === null ? null : tokenize(parsed.bytes, !parsed.name);
  return tokens === null || classOf(tokens, parsed!.name).kind !== "automaton" ? 0
    : statesOf(tokens);
}

/** Whether `text` matches `pattern` as git's `wildmatch` with `WM_PATHNAME` reads it. */
export function wildcard(pattern: string, text: string): boolean {
  const tokens = tokenize(utf8(pattern), true);
  if (tokens === null) {
    return false;
  }
  const automaton = new Automaton([{ tokens, rule: () => 0, dirOnly: false }],
                                  statesOf(tokens));
  const D = Uint32Array.from(automaton.init);
  if (!automaton.run(D, new Uint32Array(automaton.W), utf8(text))) {
    return false;
  }
  const best = { any: -1, all: -1 };
  automaton.accepted(D, best);
  return best.any === 0;
}

// -- Matching a path ---------------------------------------------------------------------------

/**
 * What a matcher remembers, so that a walk matches each folder and each repeated name once.
 *
 * - **Folders:** the folders of the path last asked, and nothing more. Every caller asks a
 *   folder's paths in one run: the sync's walk (`walkFiles`, a folder and then all under it)
 *   and its removal pass (the mirror's paths, sorted, so those under a folder are adjacent).
 *   A path's folders are what matching it computes anyway, so this memory grows with a
 *   path's depth, never with the tree. Asked out of order, a path costs its folders again,
 *   never a different answer.
 * - **Names:** at most `REMEMBERED_NAME_BYTES`, each name counted at what it really takes
 *   (`NAME_ENTRY_BYTES` beside its characters at two bytes each); past it the memory starts
 *   again. 1 MiB holds about 6,000 names, many more than a tree's repeated names (`index.ts`,
 *   `package.json`), so each is matched about once every 6,000 names met.
 *
 * Refuses nothing.
 */
export const REMEMBERED_NAME_BYTES = 1024 * 1024;
/**
 * The heap one remembered name takes beside its characters (its key's header, its best, the
 * map's slot at its doubling's worst). Measured with its characters: 93 to 107 bytes for a
 * name of 4 characters, 147 to 192 for 26, 205 to 261 for 120; counted, 168, 212 and 400.
 * The matcher's memory test (`sync-ignore.test.ts`) holds a tree of short names to it.
 */
export const NAME_ENTRY_BYTES = 160;

/**
 * A list's matcher: for a path and whether it is a folder, the kind of the line that
 * ignores it, or null; with the lines it did not apply (one compile serves both).
 */
export type Matcher = ((rel: string, isDir?: boolean) => Kind | null) &
  { readonly unapplied: readonly Unapplied[] };

/** A folder as matched: whether it is ignored, and each path structure's state after it. */
interface Folder {
  /** The ignoring line's kind when it (or a folder above it) is ignored, else null. */
  readonly ignored: Kind | null;
  readonly D: Uint32Array;
  readonly I: Uint32Array;
  readonly alive: boolean;
  readonly trie: number;
  readonly ac: number;
}

/**
 * The matcher of a sync-ignore's lines as git reads them: for a workspace path (relative to
 * `/workspace`) and whether it is a folder, `custom` when a line of the owner's own (or a
 * run's) ignores it, `default` when a default line does, or null. The sync-ignore file
 * itself, and the folders on its way, are never ignored.
 */
export function ignoredBy(lines: readonly string[]): Matcher {
  const c = compile(lines);
  const byName = new Map<string, Best>();
  let held = 0;
  const nameBest = (name: string, bytes: Uint8Array): Best => {
    const known = byName.get(name);
    if (known !== undefined) {
      return known;
    }
    const best = { any: -1, all: -1 };
    const literal = c.names.get(name);
    if (literal !== undefined) {
      best.any = literal.any;
      best.all = literal.all;
    }
    c.suffix.walk(bytes, true, best);
    c.prefix.walk(bytes, false, best);
    if (c.nameAutomaton.states > 0) {
      const D = Uint32Array.from(c.nameAutomaton.init);
      if (c.nameAutomaton.run(D, new Uint32Array(c.nameAutomaton.W), bytes)) {
        c.nameAutomaton.accepted(D, best);
      }
    }
    const cost = 2 * name.length + NAME_ENTRY_BYTES;
    if (held + cost > REMEMBERED_NAME_BYTES) {
      byName.clear();
      held = 0;
    }
    byName.set(name, best);
    held += cost;
    return best;
  };
  const decided = (best: Best, isDir: boolean): Kind | null => {
    const rule = isDir ? best.all : best.any;
    if (rule < 0) {
      return null;
    }
    const record = c.rules[rule]!;
    return record.negative ? null : record.kind;
  };
  /** A path's own best, and its path structures' states after it, from its folder's. */
  const step = (parent: Folder | null, name: string, full: string) => {
    const segment = utf8(name);
    const best = { ...nameBest(name, segment) };
    const literal = c.paths.get(full);
    if (literal !== undefined) {
      best.any = Math.max(best.any, literal.any);
      best.all = Math.max(best.all, literal.all);
    }
    const head = parent === null ? segment : Uint8Array.of(SLASH, ...segment);
    // A path's `L*`: walked on from its folder's node, its matches in this segment alone
    // (an `L` ending before it would leave a `/` for the `*`).
    let trie = parent === null ? 0 : parent.trie;
    if (parent === null) {
      c.pathPrefix.collect(0, best);  // `/*`: a top-level name
    }
    for (let i = 0; i < head.length && trie >= 0; i++) {
      trie = c.pathPrefix.child(trie, head[i]!);
      if (trie >= 0) {
        c.pathPrefix.collect(trie, best);
      }
    }
    const ac = c.anywhere.feed(parent === null ? c.anywhere.feed(0, Uint8Array.of(SLASH))
      : parent.ac, head);
    c.anywhere.trie.collect(ac, best);
    const auto = c.pathAutomaton;
    const D = parent === null ? Uint32Array.from(auto.init) : Uint32Array.from(parent.D);
    const I = parent === null ? new Uint32Array(auto.W) : Uint32Array.from(parent.I);
    let alive = parent === null || parent.alive;
    if (auto.states > 0 && alive) {
      alive = auto.run(D, I, head);
      if (alive) {
        auto.accepted(D, best);
      }
    }
    return { best, D, I, alive, trie, ac };
  };
  // The folders of the last path asked, outermost first: `chain[i]` is `last` up to
  // `ends[i]` (why these alone: `REMEMBERED_NAME_BYTES`).
  const chain: Folder[] = [];
  const ends: number[] = [];
  let last = "";
  const folder = (dir: string): Folder => {
    if (chain.length > 0 && dir === last) {
      return chain[chain.length - 1]!;
    }
    // Keep the folders `dir` shares with the last path: those it starts with, whole.
    let same = 0;
    const most = Math.min(dir.length, last.length);
    while (same < most && dir.charCodeAt(same) === last.charCodeAt(same)) {
      same++;
    }
    while (chain.length > 0) {
      const end = ends[ends.length - 1]!;
      if (end <= same && (end === dir.length || dir.charCodeAt(end) === SLASH)) {
        break;
      }
      chain.pop();
      ends.pop();
    }
    // Then each folder below them, on its way to `dir`.
    let parent = chain.length > 0 ? chain[chain.length - 1]! : null;
    let from = ends.length > 0 ? ends[ends.length - 1]! + 1 : 0;
    while (ends.length === 0 || ends[ends.length - 1]! < dir.length) {
      const cut = dir.indexOf("/", from);
      const end = cut < 0 ? dir.length : cut;
      const path = dir.slice(0, end);
      const made = step(parent, dir.slice(from, end), path);
      const ignored = parent?.ignored ?? (SYNC_IGNORE_REL.startsWith(`${path}/`) ? null
        : decided(made.best, true));
      parent = { ignored, D: made.D, I: made.I, alive: made.alive, trie: made.trie,
                 ac: made.ac };
      chain.push(parent);
      ends.push(end);
      from = end + 1;
    }
    last = dir;
    return parent!;
  };
  const match = (rel: string, isDir = false): Kind | null => {
    if (rel === SYNC_IGNORE_REL || SYNC_IGNORE_REL.startsWith(`${rel}/`)) {
      return null;  // the file itself, and the folders on its way, are never kept out
    }
    const cut = rel.lastIndexOf("/");
    const parent = cut < 0 ? null : folder(rel.slice(0, cut));
    if (parent !== null && parent.ignored !== null) {
      return parent.ignored;  // nothing under an ignored folder comes back
    }
    return decided(step(parent, cut < 0 ? rel : rel.slice(cut + 1), rel).best, isDir);
  };
  return Object.assign(match, { unapplied: c.unapplied });
}

// -- The constant, measured where it runs -------------------------------------------------------

/**
 * Nanoseconds per automaton step (a byte of text, a 32-state word) at `AUTOMATON_STATES`,
 * on the automaton's slowest branch (round 7, R7-1): lines with a `/**\/` each (so every byte
 * reads and writes its passed states too), and text of `/` bytes (the branch that re-arms
 * as well), with `*\/**\/…` and `**\/` lines keeping states alive throughout; the median of
 * five runs of `ms` each after a warm-up, so a neighbour's moment on the sandbox's CPU moves
 * it little; the slower of the two texts (`/` and none). The template build's standing check
 * runs it in the sandbox and holds it to `STEP_NS`.
 */
export function stepNanos(ms = 600): number {
  const lines: AutomatonLine[] = [];
  let states = 0;
  const add = (pattern: string) => {
    const tokens = tokenize(utf8(pattern), true)!;
    if (states + statesOf(tokens) > AUTOMATON_STATES) {
      return false;
    }
    lines.push({ tokens, rule: () => 0, dirOnly: false });
    states += statesOf(tokens);
    return true;
  };
  // A leading `**/` keeps a line alive across `/`; `*` loops keep the rest alive between.
  add(`**/*${"?".repeat(28)}*`);
  while (add(`*/**/${"?".repeat(27)}*`)) {
    // filled to the ceiling
  }
  const automaton = new Automaton(lines, AUTOMATON_STATES);
  const timed = (text: Uint8Array, span: number) => {
    let runs = 0;
    const began = Date.now();
    while (Date.now() - began < span) {
      const D = Uint32Array.from(automaton.init);
      automaton.run(D, new Uint32Array(automaton.W), text);
      runs++;
    }
    return ((Date.now() - began) * 1e6) / (runs * text.length * automaton.W);
  };
  const median = (text: Uint8Array) => {
    timed(text, ms);  // warm-up
    return Array.from({ length: 5 }, () => timed(text, ms)).sort((a, b) => a - b)[2]!;
  };
  return Math.max(median(utf8("ab-cd".repeat(51))), median(utf8("a/b/c".repeat(51))));
}
