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
 *   is the owner app's folder holding the file. A search matches the query's words against
 *   each file's path, and each small file's frontmatter (`title`, `description`, `tags`) and
 *   text, and the lines of the small `index.md` files (an OKF index names each page with its
 *   title and description, so a large page is found by its index line). It reads the
 *   manifest a page at a time and says loudly when it stopped short (`next_cursor`).
 */
import { nameOf } from "./paths";

/**
 * The most manifest entries one search reads: a technical ceiling, so one call never holds
 * the owner's object long (ADR-034's measured reason for keeping compute out of it). Past
 * it, the answer's `next_cursor` continues the scan.
 */
export const SEARCH_SCAN = 5000;
/** The results one search answers: the best of what it read. */
export const SEARCH_RESULTS = 20;
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
 * Where an index line's target may be stored: a relative one below the index's directory; a
 * root-absolute one (OKF's, from its bundle's root, which no file names) below each of the
 * index's ancestors, nearest first, and as a stored path itself.
 */
export function targets(dir: string, target: string): string[] {
  const clean = target.split("#")[0]!.replace(/^\.\//, "");
  if (clean === "" || clean.includes("..")) {
    return [];
  }
  if (!clean.startsWith("/")) {
    return [`${dir}/${clean}`];
  }
  const out: string[] = [];
  for (let at = dir; at.startsWith("/workspace"); at = at.slice(0, at.lastIndexOf("/"))) {
    out.push(`${at}${clean}`);
  }
  if (clean.startsWith("/workspace/")) {
    out.push(clean);
  }
  return out;
}

/** A query's words, lowercased: letters and digits of any script. */
export function words(text: string): string[] {
  return [...new Set(text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((word) => word !== ""))];
}

/** What one file is matched on: its fields, each with its weight. */
export interface Haystack {
  readonly path: string;
  readonly fields: readonly { readonly text: string; readonly weight: number }[];
}

/** How well a file matches: the query's words it holds, then their weights; 0 for none. */
export function score(haystack: Haystack, query: readonly string[]): [number, number] {
  let held = 0;
  let weight = 0;
  for (const word of query) {
    let best = 0;
    for (const field of haystack.fields) {
      if (field.text.toLowerCase().includes(word)) {
        best = Math.max(best, field.weight);
      }
    }
    if (best > 0) {
      held += 1;
      weight += best;
    }
  }
  return [held, weight];
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
