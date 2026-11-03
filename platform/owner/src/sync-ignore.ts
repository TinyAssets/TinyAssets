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
 * in the text for a pattern: the pattern's positions are walked together (a set of states,
 * no backtracking), so no pattern can make a match slow.
 */
export function wildcard(pattern: string, text: string): boolean {
  const p = [...pattern];
  let states = new Uint8Array(p.length + 1);
  const close = (from: Uint8Array) => {
    for (let i = 0; i < p.length; i++) {
      if (from[i] === 1 && p[i] === "*") {
        from[i + 1] = 1;
      }
    }
  };
  states[0] = 1;
  close(states);
  for (const ch of text) {
    const next = new Uint8Array(p.length + 1);
    for (let i = 0; i < p.length; i++) {
      if (states[i] !== 1) {
        continue;
      }
      if (p[i] === "*") {
        next[i] = 1;
      } else if (p[i] === "?" || p[i] === ch) {
        next[i + 1] = 1;
      }
    }
    close(next);
    states = next;
  }
  return states[p.length] === 1;
}

/** One sync-ignore line as a matcher: a name anywhere, or a path from the workspace. */
function rule(line: string): ((rel: string) => boolean) | null {
  const text = line.trim().replace(/\/+$/, "");
  if (text === "" || text.startsWith("#")) {
    return null;
  }
  if (text.includes("/")) {
    const anchored = text.replace(/^\/+/, "");
    return (rel) => {
      const parts = rel.split("/");
      return parts.some((_, i) => wildcard(anchored, parts.slice(0, i + 1).join("/")));
    };
  }
  return (rel) => rel.split("/").some((part) => wildcard(text, part));
}

/**
 * The matcher of a sync-ignore's lines: for a workspace path (relative to `/workspace`),
 * `custom` when a line of the owner's own (or a run's) names it, `default` when only a
 * default line does, or null. The sync-ignore file itself is never matched.
 */
export function ignoredBy(lines: readonly string[]):
    (rel: string) => "custom" | "default" | null {
  const defaults = new Set(DEFAULT_IGNORES);
  const rules = lines.map((line) => ({ matches: rule(line), default: defaults.has(line.trim()) }))
    .filter((r): r is { matches: (rel: string) => boolean; default: boolean } =>
      r.matches !== null);
  return (rel) => {
    if (rel === SYNC_IGNORE_REL || SYNC_IGNORE_REL.startsWith(`${rel}/`)) {
      return null;  // the file itself, and the folders on its way, are never kept out
    }
    let found: "default" | null = null;
    for (const r of rules) {
      if (r.matches(rel)) {
        if (!r.default) {
          return "custom";
        }
        found = "default";
      }
    }
    return found;
  };
}
