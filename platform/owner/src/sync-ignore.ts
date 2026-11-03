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
 * in the text for a pattern. A pattern of `*` alone (the common one, `*.pyc`) is matched by
 * its literal pieces in order, leftmost first; one with `?` walks the pattern's positions
 * together (a set of states, no backtracking). No pattern can make a match slow.
 */
export function wildcard(pattern: string, text: string): boolean {
  return pattern.includes("?") ? states(pattern, text) : stars(pattern, text);
}

function stars(pattern: string, text: string): boolean {
  const pieces = pattern.split("*");
  if (pieces.length === 1) {
    return pattern === text;
  }
  const first = pieces[0]!;
  const last = pieces.at(-1)!;
  if (text.length < first.length + last.length || !text.startsWith(first)
      || !text.endsWith(last)) {
    return false;
  }
  const end = text.length - last.length;
  let at = first.length;
  for (const piece of pieces.slice(1, -1)) {
    const found = piece === "" ? at : text.indexOf(piece, at);
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

/**
 * The sync-ignore's lines compiled once: each a name anywhere or a path from the workspace;
 * a literal line a set member (a lookup per path segment, however many lines), a line with
 * `*` or `?` a pattern; duplicates dropped. Whether a line is a default one is by its text.
 */
function compiled(lines: readonly string[]) {
  const defaults = new Set(DEFAULT_IGNORES);
  const names = { custom: new Set<string>(), default: new Set<string>() };
  const paths = { custom: new Set<string>(), default: new Set<string>() };
  const patterns = new Map<string, { text: string; path: boolean; kind: Kind }>();
  for (const line of lines) {
    const text = line.trim().replace(/\/+$/, "");
    if (text === "" || text.startsWith("#")) {
      continue;
    }
    const kind: Kind = defaults.has(line.trim()) ? "default" : "custom";
    const path = text.includes("/");
    const anchored = path ? text.replace(/^\/+/, "") : text;
    if (/[*?]/.test(anchored)) {
      const key = `${kind}\n${path}\n${anchored}`;
      patterns.set(key, { text: anchored, path, kind });
    } else {
      (path ? paths : names)[kind].add(anchored);
    }
  }
  return { names, paths, patterns: [...patterns.values()] };
}

/**
 * The matcher of a sync-ignore's lines: for a workspace path (relative to `/workspace`),
 * `custom` when a line of the owner's own (or a run's) names it, `default` when only a
 * default line does, or null. The sync-ignore file itself is never matched. Its cost a path
 * is a set lookup a segment and prefix, and one match a pattern line.
 */
export function ignoredBy(lines: readonly string[]): (rel: string) => Kind | null {
  const { names, paths, patterns } = compiled(lines);
  return (rel) => {
    if (rel === SYNC_IGNORE_REL || SYNC_IGNORE_REL.startsWith(`${rel}/`)) {
      return null;  // the file itself, and the folders on its way, are never kept out
    }
    const parts = rel.split("/");
    const prefixes = parts.map((_, i) => parts.slice(0, i + 1).join("/"));
    let found: Kind | null = null;
    for (const part of parts) {
      if (names.custom.has(part)) {
        return "custom";
      }
      found = found ?? (names.default.has(part) ? "default" : null);
    }
    for (const prefix of prefixes) {
      if (paths.custom.has(prefix)) {
        return "custom";
      }
      found = found ?? (paths.default.has(prefix) ? "default" : null);
    }
    for (const pattern of patterns) {
      if ((pattern.path ? prefixes : parts).some((each) => wildcard(pattern.text, each))) {
        if (pattern.kind === "custom") {
          return "custom";
        }
        found = "default";
      }
    }
    return found;
  };
}

/** The rule lines among `lines` (no comment, no blank), as the matcher reads each. */
export function ruleLines(lines: readonly string[]): string[] {
  return lines.map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));
}
