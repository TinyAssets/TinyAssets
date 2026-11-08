/**
 * The job's verify step's diff (`import_research.md` §4, slice 5): what each owner's object
 * should hold, computed from its own bundle with the platform's own rules (the contract's
 * content ids, placement threshold and policy digest), held to what the object's own check
 * read back (`importCheck`, `owner/src/import.ts`). The result must be 0 missing, 0 extra and
 * 0 different; anything else is the record of what failed, since a step the consumer dropped
 * (acknowledged, so never in the dead-letter queue) shows here as a difference. Pure: no file
 * and no network, so the doors' tests run it against the real object. Deleted at the cutover.
 *
 * - **Files**: every manifest entry (its ancestors are entries already, `build.ts`), each by
 *   its kind, size, mode and content id, and a file by its mtime too; a folder's mtime is the
 *   import's own.
 * - **Chunks**: every chunk of every chunked file and of every body past `PLACE_BYTES` (placed
 *   in R2 by the object's own rule, ADR-035), each read back through `FILES` and proved its hash.
 * - **Rows**: the collections' count and order-free digest (`rowLeaf`, `rowsSum`).
 * - **The rest**: the policy's digest (the default's when the bundle carries none), the tier
 *   the import binding's, the owner still `pending` with no reason, and its storage within the
 *   limit the object holds it to.
 */
import { chunkPlan, cut } from "../contract/src/content";
import {
  bodyId, type CheckChunk, type CheckEntry, type CheckState, NO_ROWS, rowLeaf, rowsSum,
} from "../contract/src/import-message";
import { PLACE_BYTES } from "../contract/src/limits";
import { DEFAULT_POLICY_DIGEST, policyDigest } from "../contract/src/policy-document";

import type { Entry, Row } from "./build";

/** What one owner's object should hold, by its bundle. */
export interface Expected {
  readonly entries: ReadonlyMap<string, Omit<CheckEntry, "path" | "mtime_ms"> &
    { readonly mtime_ms: number | null }>;
  readonly chunks: ReadonlyMap<string, number>;
  readonly rows: { readonly count: number; readonly sum: string };
  readonly policy_digest: string;
}

/** What the object's check read back, its pages joined. */
export interface Answers {
  readonly entries: readonly CheckEntry[];
  readonly chunks: readonly CheckChunk[];
  readonly rows: { readonly count: number; readonly sum: string };
  readonly state: CheckState;
}

/** What differs, each a line naming its path, chunk or part: never an owner. */
export interface Differences {
  readonly missing: readonly string[];
  readonly extra: readonly string[];
  readonly different: readonly string[];
}

/** The expected state from a bundle's manifest entries and rows. */
export function expectedOf(entries: readonly Entry[], rows: readonly Row[]): Expected {
  const listed = new Map<string, Omit<CheckEntry, "path" | "mtime_ms"> &
    { mtime_ms: number | null }>();
  const chunks = new Map<string, number>();
  for (const entry of entries) {
    if (entry.kind === "dir") {
      listed.set(entry.path, { kind: "dir", size: 0, mode: 0, content_id: null, mtime_ms: null });
      continue;
    }
    listed.set(entry.path, { kind: "file", size: entry.size!, mode: entry.mode!,
                             content_id: entry.content_id!, mtime_ms: entry.mtime_ms! });
    if (entry.chunks !== undefined) {
      const sizes = chunkPlan(entry.size!);
      entry.chunks.forEach((sha, n) => chunks.set(sha, sizes[n]!));
    }
  }
  let policy = DEFAULT_POLICY_DIGEST;
  let count = 0;
  let sum = NO_ROWS;
  for (const row of rows) {
    if (row.kind === "policy") {
      policy = policyDigest(row.document);
      continue;
    }
    count += 1;
    sum = rowsSum(sum, rowLeaf(row.collection, row.row_id, row.private, bodyId(row.body)));
    const bytes = new TextEncoder().encode(row.body);
    if (bytes.byteLength > PLACE_BYTES) {
      for (const piece of cut(bytes)) {
        chunks.set(piece.sha256, piece.size);
      }
    }
  }
  return { entries: listed, chunks, rows: { count, sum }, policy_digest: policy };
}

/** The object's answers held to the expected state, the import binding's tier `tier`. */
export function differences(expected: Expected, answers: Answers, tier: string): Differences {
  const missing: string[] = [];
  const extra: string[] = [];
  const different: string[] = [];
  const read = new Map(answers.entries.map((entry) => [entry.path, entry]));
  for (const [path, want] of expected.entries) {
    const got = read.get(path);
    if (got === undefined) {
      missing.push(`file ${path}`);
      continue;
    }
    for (const field of ["kind", "size", "mode", "content_id"] as const) {
      if (got[field] !== want[field]) {
        different.push(`file ${path}: ${field}`);
      }
    }
    if (want.mtime_ms !== null && got.mtime_ms !== want.mtime_ms) {
      different.push(`file ${path}: mtime_ms`);
    }
  }
  for (const path of read.keys()) {
    if (!expected.entries.has(path)) {
      extra.push(`file ${path}`);
    }
  }
  const chunks = new Map(answers.chunks.map((chunk) => [chunk.sha256, chunk]));
  for (const [sha, size] of expected.chunks) {
    const got = chunks.get(sha);
    if (got === undefined) {
      missing.push(`chunk ${sha}`);
    } else if (got.read !== sha) {
      different.push(`chunk ${sha}: not read back as its hash`);
    } else if (got.size !== size) {
      different.push(`chunk ${sha}: size`);
    }
  }
  for (const sha of chunks.keys()) {
    if (!expected.chunks.has(sha)) {
      extra.push(`chunk ${sha}`);
    }
  }
  if (answers.rows.count !== expected.rows.count) {
    different.push(`rows: ${answers.rows.count} where the bundle has ${expected.rows.count}`);
  } else if (answers.rows.sum !== expected.rows.sum) {
    different.push("rows: their digest");
  }
  const { state } = answers;
  if (state.policy_digest !== expected.policy_digest) {
    different.push("policy: its digest");
  }
  if (state.tier !== tier) {
    different.push("tier");
  }
  if (state.state !== "pending" || state.reasons.length > 0) {
    different.push("admission: not pending");
  }
  if (state.limit_bytes !== null && state.used_bytes > state.limit_bytes) {
    different.push("storage: past its limit");
  }
  return { missing, extra, different };
}

/** Whether nothing differs. */
export function clean(found: Differences): boolean {
  return found.missing.length === 0 && found.extra.length === 0 && found.different.length === 0;
}
