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
 * Whether `text` matches `pattern` (`*` any run of characters, `?` any one code point): its
 * pieces between the stars matched in order, leftmost first, the first piece at the start
 * and the last at the end. A piece with no `?` is found by `indexOf` and the anchored pieces
 * are compared once, so a pattern costs time linear in the text but for its middle pieces
 * that hold a `?`, each tried at every position (`QUESTION_PIECE_CHARS` bounds those). Text
 * past the basic plane takes the same way: a piece begins on a whole code point, so a
 * literal one can only match on a boundary, and only a `?` takes care to step a whole one.
 */
export function wildcard(pattern: string, text: string): boolean {
  return pieces(pattern.split("*"), text, WIDE.test(text));
}

/** A surrogate: a code point past the basic plane, two string units. */
const WIDE = /[\uD800-\uDFFF]/;

/** The string units of the code point at `at`: two for a surrogate pair, else one. */
function unitsAt(text: string, at: number): number {
  const code = text.charCodeAt(at);
  return code >= 0xD800 && code <= 0xDBFF && at + 1 < text.length ? 2 : 1;
}

/**
 * Where `piece` matched at `at` ends, or -1: each `?` one code point (one unit when the
 * text is all of the basic plane), each other unit itself.
 */
function fitsAt(piece: string, text: string, at: number, wide: boolean): number {
  let to = at;
  for (let i = 0; i < piece.length; i++) {
    if (to >= text.length) {
      return -1;
    }
    if (piece[i] === "?") {
      to += wide ? unitsAt(text, to) : 1;
    } else if (piece[i] === text[to]) {
      to += 1;
    } else {
      return -1;
    }
  }
  return to;
}

/** The first match of `piece` at or after `from`: its start and end, or null. */
function find(piece: string, text: string, from: number, wide: boolean):
    { start: number; end: number } | null {
  if (!piece.includes("?")) {
    const start = text.indexOf(piece, from);
    return start < 0 ? null : { start, end: start + piece.length };
  }
  for (let at = from; at < text.length; at += wide ? unitsAt(text, at) : 1) {
    const end = fitsAt(piece, text, at, wide);
    if (end >= 0) {
      return { start: at, end };
    }
  }
  return null;
}

/** Where `piece`, anchored at the text's end, would start, or -1 when the text is short. */
function startFromEnd(piece: string, text: string, wide: boolean): number {
  if (!wide || !piece.includes("?")) {
    return text.length - piece.length;
  }
  // Each `?` takes one code point, each other unit itself: walk back as many.
  let at = text.length;
  for (let i = piece.length - 1; i >= 0; i--) {
    if (at <= 0) {
      return -1;
    }
    const low = text.charCodeAt(at - 1);
    at -= piece[i] === "?" && low >= 0xDC00 && low <= 0xDFFF && at >= 2 ? 2 : 1;
  }
  return at;
}

function pieces(parts: readonly string[], text: string, wide: boolean): boolean {
  if (parts.length === 1) {
    return fitsAt(parts[0]!, text, 0, wide) === text.length;
  }
  const first = parts[0]!;
  const last = parts.at(-1)!;
  const head = fitsAt(first, text, 0, wide);
  const tail = startFromEnd(last, text, wide);
  if (head < 0 || tail < head || fitsAt(last, text, tail, wide) !== text.length) {
    return false;
  }
  let at = head;
  for (let i = 1; i < parts.length - 1; i++) {
    const piece = parts[i]!;
    if (piece === "") {
      continue;
    }
    const found = find(piece, text, at, wide);
    if (found === null || found.end > tail) {
      return false;
    }
    at = found.end;
  }
  return true;
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
    // Each pattern unit takes at least one text unit; with text past the basic plane, a `?`
    // may take two, so only the bound below holds there.
    if ((pattern.chars & chars) !== pattern.chars || text.length < pattern.min
        || (!wide && pattern.exact && text.length !== pattern.min)) {
      continue;
    }
    if (pieces(pattern.parts, text, wide)) {
      return pattern.kind;  // the custom lines come first (`compiled`)
    }
  }
  return null;
}

/**
 * What a pattern line costs to match beyond time linear in the text: the characters of its
 * middle pieces (between two stars) that hold a `?`, each tried at every position of the
 * text. A line with none (`*.log`, `dist/**`, `node_modules/*`, every line of GitHub's
 * gitignore templates) costs nothing here.
 */
export function questionPieceChars(pattern: string): number {
  return pattern.split("*").slice(1, -1).filter((piece) => piece.includes("?"))
    .reduce((sum, piece) => sum + piece.length, 0);
}

/**
 * The most `questionPieceChars` the list's pattern lines take together, each distinct line
 * once: a technical ceiling, the only term of a match's cost past linear. It bounds a
 * sync-out's matching at the files plane's own ceilings well inside the sandbox's shortest
 * session (`limits-audit.md` gives the measured worst case and the arithmetic). Literal
 * lines, and pattern lines with no `?` piece between stars, take none of it. A line past it
 * is not applied, the safe way: the files it names go on syncing, and the sync's report and
 * the owner's notice say which lines.
 */
export const QUESTION_PIECE_CHARS = 384;

/**
 * The sync-ignore's lines compiled once: each a name anywhere or a path from the workspace;
 * a literal line a set member (a lookup per path segment, however many lines), a line with
 * `*` or `?` a pattern, in the file's order until `QUESTION_PIECE_CHARS`; duplicates
 * dropped.
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
      const cost = questionPieceChars(anchored);
      if (used + cost > QUESTION_PIECE_CHARS) {
        unapplied.push(line.trim());
        continue;
      }
      used += cost;
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

/** The list's pattern lines past `QUESTION_PIECE_CHARS`, in its order: not applied. */
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
