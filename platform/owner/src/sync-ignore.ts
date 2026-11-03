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
  "# What the sync never keeps in your files: things your agents rebuild. Edit freely;",
  "# a line is a file or folder name (* and ? match), or a path from /workspace with a /.",
  "# A path already kept goes on syncing whatever a default line here says.",
]);

/** The sync-ignore file's lines as written when there is none: its notes, then the defaults. */
export function defaultSyncIgnore(): readonly string[] {
  return [...DEFAULT_NOTES, ...DEFAULT_IGNORES];
}

/**
 * Whether `text` matches `pattern` (`*` any run of characters, `?` any one), in time linear
 * in the text for a pattern: its pieces between the stars matched in order, leftmost first
 * (`?` any one character within a piece), the first piece at the start and the last at the
 * end. Text or a pattern past the basic plane (a `?` must take a whole code point) walks the
 * pattern's positions together instead (a set of states, no backtracking).
 */
export function wildcard(pattern: string, text: string): boolean {
  return WIDE.test(pattern) || WIDE.test(text) ? states(pattern, text)
    : pieces(pattern.split("*"), text);
}

/** A surrogate: a code point past the basic plane, two string units. */
const WIDE = /[\uD800-\uDFFF]/;

/** Where `piece` (`?` any one unit) first matches `text` at or after `from`, or -1. */
function find(piece: string, text: string, from: number): number {
  if (!piece.includes("?")) {
    return text.indexOf(piece, from);
  }
  for (let at = from; at + piece.length <= text.length; at++) {
    if (fits(piece, text, at)) {
      return at;
    }
  }
  return -1;
}

function fits(piece: string, text: string, at: number): boolean {
  for (let i = 0; i < piece.length; i++) {
    if (piece[i] !== "?" && piece[i] !== text[at + i]) {
      return false;
    }
  }
  return true;
}

function pieces(parts: readonly string[], text: string): boolean {
  if (parts.length === 1) {
    return parts[0]!.length === text.length && fits(parts[0]!, text, 0);
  }
  const first = parts[0]!;
  const last = parts.at(-1)!;
  const end = text.length - last.length;
  if (end < first.length || !fits(first, text, 0) || !fits(last, text, end)) {
    return false;
  }
  let at = first.length;
  for (let i = 1; i < parts.length - 1; i++) {
    const piece = parts[i]!;
    const found = piece === "" ? at : find(piece, text, at);
    if (found < 0 || found + piece.length > end) {
      return false;
    }
    at = found + piece.length;
  }
  return true;
}

function states(pattern: string, text: string): boolean {
  const p = [...pattern];
  let now = new Uint8Array(p.length + 1);
  const close = (from: Uint8Array) => {
    for (let i = 0; i < p.length; i++) {
      if (from[i] === 1 && p[i] === "*") {
        from[i + 1] = 1;
      }
    }
  };
  now[0] = 1;
  close(now);
  for (const ch of text) {
    const next = new Uint8Array(p.length + 1);
    for (let i = 0; i < p.length; i++) {
      if (now[i] !== 1) {
        continue;
      }
      if (p[i] === "*") {
        next[i] = 1;
      } else if (p[i] === "?" || p[i] === ch) {
        next[i + 1] = 1;
      }
    }
    close(next);
    now = next;
  }
  return now[p.length] === 1;
}

type Kind = "custom" | "default";

/** The characters a text holds, folded into 32 bits: a pattern's must all be among them. */
function charsOf(text: string): number {
  let mask = 0;
  for (let i = 0; i < text.length; i++) {
    mask |= 1 << (text.charCodeAt(i) & 31);
  }
  return mask;
}

/**
 * One pattern line, with what any text it matches must have: at least `min` units (and
 * exactly that with no star), and every literal character of it (`chars`). Most texts fail
 * one of those in a comparison or two, so a long list costs a pass over cheap checks, and
 * only the few left are matched.
 */
interface Pattern {
  readonly text: string;
  readonly parts: readonly string[];
  readonly kind: Kind;
  readonly min: number;
  readonly exact: boolean;
  readonly chars: number;
}

function patternOf(text: string, kind: Kind): Pattern {
  const literal = text.replace(/[*?]/g, "");
  return { text, parts: text.split("*"), kind, min: text.replace(/\*/g, "").length,
           exact: !text.includes("*"), chars: charsOf(literal) };
}

/** The kind of the first of `patterns` (custom lines first) matching `text`, or null. */
function matchAny(patterns: readonly Pattern[], text: string): Kind | null {
  if (patterns.length === 0) {
    return null;
  }
  const chars = charsOf(text);
  const wide = WIDE.test(text);
  for (const pattern of patterns) {
    if ((pattern.chars & chars) !== pattern.chars
        || (!wide && (pattern.exact ? text.length !== pattern.min : text.length < pattern.min))) {
      continue;
    }
    if (wide ? states(pattern.text, text) : pieces(pattern.parts, text)) {
      return pattern.kind;  // the custom lines come first (`compiled`)
    }
  }
  return null;
}

/**
 * The most characters the list's pattern lines (those with `*` or `?`, each distinct line
 * once) take together: a technical ceiling. A pattern's match costs its length times the
 * text's in the worst case (a run of `?` before a letter tries every position), so this
 * bounds a sync-out's matching at the files plane's own ceilings, well inside the sandbox's
 * shortest session (`limits-audit.md` gives the arithmetic: about 280 s of an hour at the
 * highest `max_entries`, measured). Literal lines are set lookups and take none of it. A
 * pattern line past it is not applied, the safe way: the files it names go on syncing, and
 * the sync's report and the owner's notice say which lines.
 */
export const PATTERN_CHARS = 384;

/**
 * The sync-ignore's lines compiled once: each a name anywhere or a path from the workspace;
 * a literal line a set member (a lookup per path segment, however many lines), a line with
 * `*` or `?` a pattern, in the file's order until `PATTERN_CHARS`; duplicates dropped.
 * Whether a line is a default one is by its text.
 */
function compiled(lines: readonly string[]) {
  const defaults = new Set(DEFAULT_IGNORES);
  const names = { custom: new Set<string>(), default: new Set<string>() };
  const paths = { custom: new Set<string>(), default: new Set<string>() };
  const patterns = { names: new Map<string, Pattern>(), paths: new Map<string, Pattern>() };
  const unapplied: string[] = [];
  let used = 0;
  for (const line of lines) {
    const text = line.trim().replace(/\/+$/, "");
    if (text === "" || text.startsWith("#")) {
      continue;
    }
    const kind: Kind = defaults.has(line.trim()) ? "default" : "custom";
    const path = text.includes("/");
    const anchored = path ? text.replace(/^\/+/, "") : text;
    if (/[*?]/.test(anchored)) {
      const into = path ? patterns.paths : patterns.names;
      const key = `${kind}\n${anchored}`;
      if (into.has(key)) {
        continue;
      }
      if (used + anchored.length > PATTERN_CHARS) {
        unapplied.push(line.trim());
        continue;
      }
      used += anchored.length;
      into.set(key, patternOf(anchored, kind));
    } else {
      (path ? paths : names)[kind].add(anchored);
    }
  }
  const ordered = (all: Map<string, Pattern>) =>
    [...all.values()].sort((a, b) => Number(a.kind === "default") - Number(b.kind === "default"));
  // A custom line first, so a pass can stop at it.
  return { names, paths, namePatterns: ordered(patterns.names),
           pathPatterns: ordered(patterns.paths), unapplied };
}

/** The list's pattern lines past `PATTERN_CHARS`, in its order: lines not applied. */
export function unappliedLines(lines: readonly string[]): string[] {
  return compiled(lines).unapplied;
}

/**
 * The most a matcher's memory of names and path prefixes holds, in their characters
 * (two bytes each): a tree's directories repeat in every path under them, so each is
 * matched once; past it the memory starts again, so it holds at most 16 MiB of keys (and
 * their entries) however large the tree or long its paths. Refuses nothing.
 */
const REMEMBERED_CHARS = 8 * 1024 * 1024;

/**
 * The matcher of a sync-ignore's lines: for a workspace path (relative to `/workspace`),
 * `custom` when a line of the owner's own (or a run's) names it, `default` when only a
 * default line does, or null. The sync-ignore file itself is never matched. A path costs a
 * set lookup a segment and prefix, and the pattern lines' cheap checks for each name and
 * prefix not met before (a directory's once, for every path under it).
 */
export function ignoredBy(lines: readonly string[]): (rel: string) => Kind | null {
  const { names, paths, namePatterns, pathPatterns } = compiled(lines);
  const byName = new Map<string, Kind | null>();
  const byPrefix = new Map<string, Kind | null>();
  let held = 0;
  const remembered = (memory: Map<string, Kind | null>, key: string,
                      work: () => Kind | null): Kind | null => {
    const known = memory.get(key);
    if (known !== undefined) {
      return known;
    }
    if (held + key.length > REMEMBERED_CHARS) {
      byName.clear();
      byPrefix.clear();
      held = 0;
    }
    const kind = work();
    memory.set(key, kind);
    held += key.length;
    return kind;
  };
  const nameKind = (name: string) => remembered(byName, name, () =>
    names.custom.has(name) ? "custom"
      : matchAny(namePatterns, name) ?? (names.default.has(name) ? "default" : null));
  const prefixKind = (prefix: string) => remembered(byPrefix, prefix, () =>
    paths.custom.has(prefix) ? "custom"
      : matchAny(pathPatterns, prefix) ?? (paths.default.has(prefix) ? "default" : null));
  return (rel) => {
    if (rel === SYNC_IGNORE_REL || SYNC_IGNORE_REL.startsWith(`${rel}/`)) {
      return null;  // the file itself, and the folders on its way, are never kept out
    }
    const parts = rel.split("/");
    let found: Kind | null = null;
    let prefix = "";
    for (let i = 0; i < parts.length; i++) {
      prefix = i === 0 ? parts[0]! : `${prefix}/${parts[i]!}`;
      const name = nameKind(parts[i]!);
      const path = prefixKind(prefix);
      if (name === "custom" || path === "custom") {
        return "custom";
      }
      found = found ?? name ?? path;
    }
    return found;
  };
}

/** The rule lines among `lines` (no comment, no blank), as the matcher reads each. */
export function ruleLines(lines: readonly string[]): string[] {
  return lines.map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));
}
