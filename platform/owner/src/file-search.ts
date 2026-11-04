/**
 * Reading and finding the owner's files at the MCP door, for an agent that cannot walk them
 * itself: a main agent over MCP, with no sandbox and no grep (ai_storage_research.md §8.2).
 * Everything here is deterministic and lexical: no model, no index the platform keeps, no
 * embedding (ADR-006; Direction 3).
 *
 * - `file_read` is a file's text, a window of its UTF-8 bytes at a time, cut back to a
 *   character boundary, with where the next window starts.
 * - `file_list`'s heads: the start of each small file's text (an inline file, `INLINE_BYTES`
 *   or less, so no bucket is read), enough for its frontmatter's title and description.
 * - `search` and `fetch`, in the shape ChatGPT's deep research and company knowledge require
 *   (platform.openai.com/docs/mcp): `search(query)` answers `results` of `{id, title, url}`,
 *   `fetch(id)` answers `{id, title, text, url, metadata}`. The id is the stored path; the url
 *   is the owner app's folder holding the file. A search matches the query's words, whole,
 *   against the words of each file's path, and of each small file's frontmatter (`title`,
 *   `description`, `tags`, `type`) and text, and of the lines of the small `index.md` files
 *   naming it (an OKF index names each page with its title and description, so a large page
 *   is found by its index line). Each field is split into words once, and a file's words are
 *   compared with the query's from the smaller side, so a call's cost is the words it reads,
 *   whatever the query's length (`searchPage`).
 * - Paging is by matches, in path order: a page answers every match of the entries it read,
 *   best first, and its `next_cursor` resumes right after the last match it answered, or
 *   after the last entry it read, so no match is ever out of reach. It stops at whichever
 *   comes first of `SEARCH_SCAN` entries read and `SEARCH_ANSWER_BYTES` of results.
 */
import { escapedBytes, fitted, jsonBytes } from "./encoded";
import { PAGE_BYTES } from "./pages";
import { nameOf } from "./paths";

/**
 * The most manifest entries one search reads, a technical ceiling: the object runs one call
 * at a time, and a call's CPU must stay well inside Workers' CPU limit an invocation (30 s
 * by default, developers.cloudflare.com/workers/platform/limits/#cpu-time). Measured worst
 * case (`search-cpu.test.ts`: every entry an inline file of 4,096 bytes of distinct words,
 * a 16 MiB query): `limits-audit.md` records the figure. Past it, `next_cursor` continues.
 */
export const SEARCH_SCAN = 5000;
/**
 * What one answer holds beside its results, at its widest: `next_cursor` (a stored path of
 * at most `MAX_PATH_BYTES`, as base64url: four characters each three bytes, 5,462, which a
 * test holds to `MAX_PATH_BYTES`), `note`, and the keys.
 */
export const SEARCH_ENVELOPE_BYTES = 5_462 + 2 + 1024;
/**
 * The most bytes of results, as JSON, one answer holds: one tool answer (`PAGE_BYTES`,
 * `pages.ts`, answered once as text) less `SEARCH_ENVELOPE_BYTES`. A search counts each
 * result's JSON in UTF-8 bytes as it adds it. Past it, `next_cursor` continues.
 */
export const SEARCH_ANSWER_BYTES = PAGE_BYTES - SEARCH_ENVELOPE_BYTES;
/**
 * The most UTF-8 bytes a query holds, a technical ceiling of the isolate's 128 MB (Workers
 * limits): a query's set of words takes about 15 bytes of heap a query byte (measured,
 * `search-cpu.test.ts`), so 1 MiB of query takes about 15 MB, which fits in the room the
 * platform's one body ceiling leaves (about 44 MB, `EGRESS_BODY_MAX`'s derivation); the
 * body ceiling alone (16 MiB) would let one query's words take 250 MB.
 */
export const SEARCH_QUERY_BYTES = 1024 * 1024;
/** A `file_read` window when the call names none: 64 KiB of text. */
export const READ_DEFAULT_BYTES = 65_536;

/** A file's frontmatter keys the search and fetch read. */
export interface Front {
  readonly type?: string;
  readonly title?: string;
  readonly description?: string;
  readonly tags: readonly string[];
}

/** Bytes as UTF-8 text, or null when they are not. */
export function textOf(bytes: Uint8Array): string | null {
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
  } catch {
    return null;
  }
}

function unquoted(value: string): string {
  const trimmed = value.trim();
  return /^(["']).*\1$/.test(trimmed) ? trimmed.slice(1, -1) : trimmed;
}

/** The leading `---` YAML block's plain keys (no nesting read), or none. */
export function frontmatter(text: string): Front {
  const lines = text.split("\n");
  if (lines[0]?.trim() !== "---") {
    return { tags: [] };
  }
  const front: { type?: string; title?: string; description?: string; tags: string[] } =
    { tags: [] };
  let inTags = false;
  for (const line of lines.slice(1)) {
    if (line.trim() === "---") {
      break;
    }
    const item = /^\s+-\s+(.+)$/.exec(line);
    if (inTags && item !== null) {
      front.tags.push(unquoted(item[1]!));
      continue;
    }
    inTags = false;
    const pair = /^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$/.exec(line);
    if (pair === null) {
      continue;
    }
    const [, key, raw] = pair as unknown as [string, string, string];
    if (key === "tags") {
      const inline = /^\[(.*)\]$/.exec(raw.trim());
      if (inline !== null) {
        front.tags.push(...inline[1]!.split(",").map(unquoted).filter((tag) => tag !== ""));
      } else {
        inTags = raw.trim() === "";
      }
    } else if (key === "type" || key === "title" || key === "description") {
      front[key] = unquoted(raw);
    }
  }
  return front;
}

/**
 * An OKF index's entry lines (`* [Title](target) - description`): each link's target as
 * written, with its title and description. Outside URLs are left out.
 */
export function indexLines(text: string): { target: string; text: string }[] {
  const found: { target: string; text: string }[] = [];
  for (const line of text.split("\n")) {
    const entry = /^\s*[*-]\s+\[([^\]]*)\]\(([^)\s]+)\)\s*(?:[-:]\s*(.*))?$/.exec(line);
    if (entry === null || /^[a-z][a-z0-9+.-]*:/i.test(entry[2]!)) {
      continue;
    }
    found.push({ target: entry[2]!, text: `${entry[1]} ${entry[3] ?? ""}` });
  }
  return found;
}

/**
 * Where an index line's target is stored: a relative one below the index's directory; a
 * root-absolute one below its bundle's root (`root`: the nearest directory whose `index.md`
 * declares `okf_version`, OKF §12, else `/workspace`), or as written when it names a stored
 * path. Null for a target that leaves its root or names nothing.
 */
export function target(dir: string, written: string, root: string | null): string | null {
  const clean = written.split("#")[0]!.replace(/^\.\//, "");
  if (clean === "" || clean.split("/").includes("..")) {
    return null;
  }
  if (!clean.startsWith("/")) {
    return `${dir}/${clean}`;
  }
  return clean.startsWith("/workspace/") ? clean : `${root ?? "/workspace"}${clean}`;
}

/** Whether an index's text declares an OKF bundle root (`okf_version` in its frontmatter). */
export function bundleRoot(text: string): boolean {
  if (!text.startsWith("---\n")) {
    return false;
  }
  const end = text.indexOf("\n---", 3);
  return end > 0 && /(^|\n)okf_version:/.test(text.slice(4, end + 1));
}

/** A text's words, lowercased once: letters and digits of any script, each once. */
export function words(text: string): string[] {
  return [...new Set(text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((word) => word !== ""))];
}

/** One field a file is matched on, and its weight. */
export interface Field {
  readonly text: string;
  readonly weight: number;
}

/** A file's words, each with the best weight of the fields holding it: split once a field. */
export function wordWeights(fields: readonly Field[]): Map<string, number> {
  const found = new Map<string, number>();
  for (const field of fields) {
    for (const word of words(field.text)) {
      found.set(word, Math.max(found.get(word) ?? 0, field.weight));
    }
  }
  return found;
}

/**
 * How well a file's words match the query's: how many of the query's words it holds, then
 * their weights; [0, 0] for none. Compared from the smaller side, so the cost is the smaller
 * of the two counts.
 */
export function score(file: ReadonlyMap<string, number>, query: ReadonlySet<string>):
    [number, number] {
  let held = 0;
  let weight = 0;
  if (query.size <= file.size) {
    for (const word of query) {
      const best = file.get(word);
      if (best !== undefined) {
        held += 1;
        weight += best;
      }
    }
  } else {
    for (const [word, best] of file) {
      if (query.has(word)) {
        held += 1;
        weight += best;
      }
    }
  }
  return [held, weight];
}

/** One manifest entry as a search reads it. */
export interface Scanned {
  readonly path: string;
  readonly kind: "file" | "dir";
  /** Its bytes when inline (`INLINE_BYTES` or less), else null. */
  readonly inline: Uint8Array | null;
}

/** One page of a search: its results, best first, and where the next page resumes. */
export interface SearchPage {
  /** Each match; one whose title was cut to fit one answer says so (`title_cut`). */
  readonly results: readonly { id: string; title: string; url: string; title_cut?: true }[];
  /** The path the next page resumes after, or null when the entries read were all. */
  readonly resume: string | null;
}

/**
 * One page of a search over `entries` (the manifest's next page, in path order; `more`
 * whether the manifest holds entries past them): every match, until the results reach
 * `SEARCH_ANSWER_BYTES`; best first. An index line counts for a file of the same page only,
 * the only ones the page scores, so resolving it reads nothing more.
 */
export function searchPage(entries: readonly Scanned[], more: boolean, query: ReadonlySet<string>,
                           url: (path: string) => string,
                           answerBytes: number = SEARCH_ANSWER_BYTES): SearchPage {
  const texts = new Map<string, string>();
  const fronts = new Map<string, Front>();
  const lines = new Map<string, string[]>();
  const files = new Set<string>();
  const roots = new Set<string>();
  for (const entry of entries) {
    if (entry.kind === "file") {
      files.add(entry.path);
    }
    const text = entry.kind === "file" && entry.inline !== null ? textOf(entry.inline) : null;
    if (text === null) {
      continue;
    }
    texts.set(entry.path, text);
    fronts.set(entry.path, frontmatter(text));
    if (nameOf(entry.path) === "index.md" && bundleRoot(text)) {
      roots.add(entry.path.slice(0, entry.path.lastIndexOf("/")));
    }
  }
  const rootOf = new Map<string, string | null>();
  const root = (dir: string): string | null => {
    const known = rootOf.get(dir);
    if (known !== undefined) {
      return known;
    }
    const up = dir.lastIndexOf("/");
    const found = roots.has(dir) ? dir : up <= 0 || dir === "/workspace" ? null
      : root(dir.slice(0, up));
    rootOf.set(dir, found);
    return found;
  };
  for (const [path, text] of texts) {
    if (nameOf(path) !== "index.md") {
      continue;
    }
    const dir = path.slice(0, path.lastIndexOf("/"));
    for (const line of indexLines(text)) {
      const found = target(dir, line.target, root(dir));
      if (found !== null && files.has(found)) {
        lines.set(found, [...(lines.get(found) ?? []), line.text]);
      }
    }
  }
  const hits: { path: string; title: string; score: [number, number] }[] = [];
  let bytes = 2;
  let resume: string | null = null;
  let cutTitle: string | null = null;
  for (const entry of entries) {
    if (entry.kind !== "file") {
      continue;
    }
    const front = fronts.get(entry.path) ?? null;
    const text = texts.get(entry.path);
    const fields: Field[] = [{ text: entry.path, weight: 2 },
                             ...(lines.get(entry.path) ?? []).map((line) => ({ text: line, weight: 3 }))];
    if (front !== null) {
      fields.push({ text: front.title ?? "", weight: 4 }, { text: front.description ?? "", weight: 3 },
                  { text: front.tags.join(" "), weight: 3 }, { text: front.type ?? "", weight: 1 });
    }
    if (text !== undefined) {
      fields.push({ text, weight: 1 });
    }
    const found = score(wordWeights(fields), query);
    if (found[0] === 0) {
      continue;
    }
    let title = titleOf(entry.path, front);
    let size = jsonBytes({ id: entry.path, title, url: url(entry.path) }) + 1;
    if (bytes + size > answerBytes) {
      if (hits.length > 0) {
        resume = hits.at(-1)!.path;  // the last match answered: the next page starts after it
        break;
      }
      // The first match is always answered: its title, display text, cut to fit and said so.
      title = fitted(title, Math.max(2, answerBytes - bytes - size + escapedBytes(title)
                                          - jsonBytes({ title_cut: true }) + 1)).text;
      cutTitle = entry.path;
      size = jsonBytes({ id: entry.path, title, url: url(entry.path), title_cut: true }) + 1;
    }
    bytes += size;
    hits.push({ path: entry.path, title, score: found });
  }
  if (resume === null && more) {
    resume = entries.at(-1)?.path ?? null;
  }
  hits.sort((a, b) => b.score[0] - a.score[0] || b.score[1] - a.score[1]
                      || (a.path < b.path ? -1 : 1));
  return { results: hits.map((hit) => ({ id: hit.path, title: hit.title, url: url(hit.path),
                                         ...(hit.path === cutTitle ? { title_cut: true as const }
                                           : {}) })),
           resume };
}

/** A file's display title: its frontmatter's, else its name. */
export function titleOf(path: string, front: Front | null): string {
  return front?.title !== undefined && front.title !== "" ? front.title : nameOf(path);
}

/**
 * The window of `bytes` from `offset`, at most `max` bytes, cut back to a UTF-8 character
 * boundary: its end, or null when `offset` is inside a character. A window that holds less
 * than one whole character is widened to hold it.
 */
export function windowEnd(bytes: Uint8Array, offset: number, max: number): number | null {
  const continuation = (at: number) => at < bytes.byteLength && (bytes[at]! & 0xc0) === 0x80;
  if (continuation(offset)) {
    return null;
  }
  let end = Math.min(bytes.byteLength, offset + max);
  while (end > offset && continuation(end)) {
    end -= 1;
  }
  if (end === offset && offset < bytes.byteLength) {
    end = offset + 1;
    while (continuation(end)) {
      end += 1;
    }
  }
  return end;
}
