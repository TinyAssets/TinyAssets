/**
 * The bundle builder (`import_research.md` §3, slice 2): the backup copy read into one
 * bundle an owner, in the format the import doors and the doors' consumer take (slices 3
 * and 4), written to a local folder. Uploading it to R2 staging is slice 4's.
 *
 * A bundle, at `<out>/<key>/` (`key` = `sha256(principal)[:16]`):
 * - `owner.json`: the owner's name (the old principal), uid, admission, tier and notice;
 * - `manifest/<n>.json`: every folder and file of the owner's imported tree (`build.ts`),
 *   parents first, with each file's size, executable bit, mtime and expected content id,
 *   computed by the contract's own `inlineId`/`chunkedId` over the backup's bytes; a page
 *   holds at most `PAGE_ROWS` entries and `PAGE_BYTES` encoded (the export's page);
 * - `inline/<content id>` (content of `INLINE_BYTES` or less) and `chunks/<sha256>` (each
 *   `CHUNK_BYTES` chunk of larger content): the bytes, each named by what checks it;
 * - `rows/<n>.json`: the rows that carry live, the policy then the collections, in pages
 *   of the same bounds;
 * - `bundle.json`, written last: each part's SHA-256 and the counts. Its own SHA-256 is the
 *   bundle's digest, which the consumer's message is signed over.
 *
 * The owners on the exclusion list (the founder's and the canary's: they start fresh) get
 * the same bundle in the private folder instead, never in `out`, and the two folders are
 * never one inside the other, by their real paths. The list is input and never empty; each
 * name on it must be an owner of the backup, so a typo never lets an excluded owner be
 * imported. The bundles' digests and the billing ids go to 0600 files in the private
 * folder; the summary is counts alone. A dry run reads and hashes everything it would
 * carry, makes the import's own files in a temporary folder, and writes nothing else.
 */
import { mkdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { join, sep } from "node:path";

import { sha256Hex } from "../contract/src/digest";

import { type Tiers } from "./build";
import { admissionProblems, erased, measure, verifyCopy } from "./census";
import { type Copy, type Owner, type Problem, survey } from "./survey";

/** The bundle format this builder writes. */
export const FORMAT = 1;
/** A page's most entries or rows, and its most bytes encoded: the export's page. */
export const PAGE_ROWS = 256;
export const PAGE_BYTES = 1024 * 1024;
/** The notice the owner is given once imported (research §3, order step 4). */
export const NOTICE = "Your files from the old TinyAssets are in /workspace/imported";
/** The private folder's records: the billing ids, and each bundle's digest by owner key. */
export const PRIVATE_RECORD = "cutover.private.json";
export const DIGESTS = "bundles.private.json";

export interface Options {
  /** Where the bundles go; null for a dry run, which writes nothing. */
  readonly out: string | null;
  /** The private folder: the excluded owners' bundles and the private records. */
  readonly private: string | null;
  /** The principals whose owners start fresh: backed up, never imported. Never empty. */
  readonly exclude: ReadonlySet<string>;
  readonly tiers: Tiers;
}

/** The run's counts: no key, no digest, no name. */
export interface Summary {
  readonly dry_run: boolean;
  readonly owners: number;
  readonly bundled: number;
  readonly excluded: number;
  readonly erased: number;
  readonly refused: Readonly<Record<string, number>>;
  readonly entries: number;
  readonly files: number;
  readonly inline: number;
  readonly chunks: number;
  readonly bytes: number;
  readonly rows: number;
}

/** An exclusion list: one principal a line; blank lines and `#` comments are skipped. */
export function exclusionList(text: string): Set<string> {
  return new Set(text.split("\n").map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#")));
}

/** Items in pages of at most `PAGE_ROWS` items and `PAGE_BYTES` encoded. */
export function pages<T>(items: readonly T[]): T[][] {
  const found: T[][] = [];
  let page: T[] = [];
  let bytes = 0;
  for (const item of items) {
    const size = Buffer.byteLength(JSON.stringify(item)) + 1;
    if (page.length > 0 && (page.length === PAGE_ROWS || bytes + size > PAGE_BYTES)) {
      found.push(page);
      page = [];
      bytes = 0;
    }
    if (size > PAGE_BYTES) {
      throw new Error("one entry or row is larger than a page");
    }
    page.push(item);
    bytes += size;
  }
  return page.length > 0 ? [...found, page] : found;
}

/**
 * Why the two folders cannot hold the bundles, or null: each must exist, and neither may
 * be the other or lie inside it, compared by real path (so through a link, at any depth,
 * with a trailing slash or not), since the out folder is what slice 4 uploads to staging.
 */
export function foldersProblem(out: string, privateDir: string): string | null {
  const a = realpathSync(out);
  const b = realpathSync(privateDir);
  const inside = (child: string, parent: string) =>
    child === parent || child.startsWith(parent.endsWith(sep) ? parent : `${parent}${sep}`);
  return inside(a, b) || inside(b, a)
    ? "the private folder and the out folder are one inside the other: keep them apart" : null;
}

/** One owner's bundle at `dir`, or the fit's problems (and nothing left at `dir`). */
function writeBundle(owner: Owner, copy: Copy, dir: string | null, tiers: Tiers, fit: boolean):
    { problems: Problem[]; digest: string | null; counts: Omit<Summary, "dry_run" | "owners" |
      "bundled" | "excluded" | "erased" | "refused"> } {
  if (dir !== null) {
    mkdirSync(dir, { recursive: false, mode: 0o700 });
    for (const part of ["manifest", "rows"]) {
      mkdirSync(join(dir, part), { mode: 0o700 });
    }
  }
  const measured = measure(owner, copy, fit ? tiers : null, dir);
  const none = { entries: 0, files: 0, inline: 0, chunks: 0, bytes: 0, rows: 0 };
  if (measured.problems.length > 0 || measured.built === null) {
    if (dir !== null) {
      rmSync(dir, { recursive: true, force: true });
    }
    return { problems: measured.problems, digest: null, counts: none };
  }
  const { entries, rows, store } = measured.built;
  const counts = { entries: entries.length, files: entries.filter((e) => e.kind === "file").length,
                   inline: store.inline, chunks: store.chunks,
                   bytes: store.chunkBytes + store.inlineBytes, rows: rows.length };
  if (dir === null) {
    return { problems: [], digest: null, counts };
  }
  const parts: Record<string, string> = {};
  const part = (name: string, value: unknown) => {
    const text = `${JSON.stringify(value)}\n`;
    writeFileSync(join(dir, name), text, { flag: "wx", mode: 0o600 });
    parts[name] = sha256Hex(text);
  };
  part("owner.json", {
    format: FORMAT, owner: owner.principal, key: owner.key, uid: owner.uid,
    // An owner the old stack never admitted is "absent", never admitted by slice 5.
    admission: owner.admission ?? { state: "absent", reasons: [] },
    tier: owner.plan?.tier ?? "free", notice: NOTICE, centers: owner.centers,
  });
  pages(entries).forEach((page, n) => part(`manifest/${n}.json`, { entries: page }));
  pages(rows).forEach((page, n) => part(`rows/${n}.json`, { rows: page }));
  const index = `${JSON.stringify({
    format: FORMAT, key: owner.key, owner: owner.principal, parts,
    counts: { entries: entries.length, inline: store.inline, chunks: store.chunks,
              chunk_bytes: store.chunkBytes, inline_bytes: store.inlineBytes,
              rows: rows.length },
  })}\n`;
  writeFileSync(join(dir, "bundle.json"), index, { flag: "wx", mode: 0o600 });
  return { problems: [], digest: sha256Hex(index), counts };
}

/**
 * Every owner's bundle. The inputs are checked first (the folders, the exclusion list),
 * then the backup copy; a fleet-wide problem (the archive, a pin, a fleet row no identity
 * holds, a shared store or entry no rule names) stops the whole run, and an owner's own
 * problem stops that owner alone, counted, while the others go on
 * (`restore_credentials.py`'s rule).
 */
export function buildBundles(copy: Copy, options: Options): Summary {
  const dry = options.out === null;
  if (options.exclude.size === 0) {
    throw new Error("the exclusion list names no owner: the founder's and the canary's " +
                    "owners start fresh, so it is never empty");
  }
  if (!dry) {
    if (options.private === null) {
      throw new Error("the bundles need a private folder of their own for the excluded owners");
    }
    const problem = foldersProblem(options.out!, options.private);
    if (problem !== null) {
      throw new Error(problem);
    }
  }
  const fleetProblems = [...verifyCopy(copy)];
  const fleet = survey(copy);
  fleetProblems.push(...fleet.problems);
  if (fleetProblems.length > 0) {
    throw new Error(`the backup copy is refused: ${[...new Set(fleetProblems.map((p) => p.code))]
      .sort().join(", ")}`);
  }
  const principals = new Set(fleet.owners.map((owner) => owner.principal));
  const missing = [...options.exclude].filter((principal) => !principals.has(principal));
  if (missing.length > 0) {
    throw new Error(`${missing.length} name(s) on the exclusion list are no owner of this ` +
                    "backup: fix the list before anything is written");
  }
  const refused: Record<string, number> = {};
  const digests: Record<string, string> = {};
  const totals = { bundled: 0, excluded: 0, entries: 0, files: 0, inline: 0, chunks: 0,
                   bytes: 0, rows: 0 };
  const record: Record<string, unknown>[] = [];
  const count = (problems: readonly Problem[]) => {
    for (const code of new Set(problems.map((problem) => problem.code))) {
      refused[code] = (refused[code] ?? 0) + 1;
    }
  };
  const live = fleet.owners.filter((owner) => !erased(owner));
  for (const owner of live) {
    const excluded = options.exclude.has(owner.principal);
    // A backup is kept whatever the limits say; an import is held to them.
    const problems = excluded ? owner.tree.problems
      : [...owner.tree.problems, ...admissionProblems(owner)];
    if (problems.length > 0) {
      count(problems);
      continue;
    }
    const base = excluded ? options.private : options.out;
    const built = writeBundle(owner, copy, base === null ? null : join(base, owner.key),
                              options.tiers, !excluded);
    if (built.problems.length > 0) {
      count(built.problems);
      continue;
    }
    totals[excluded ? "excluded" : "bundled"] += 1;
    for (const key of ["entries", "files", "inline", "chunks", "bytes", "rows"] as const) {
      totals[key] += built.counts[key];
    }
    if (built.digest !== null) {
      digests[owner.key] = built.digest;
    }
    record.push({ key: owner.key, owner: owner.principal, uid: owner.uid, excluded,
                  tier: owner.plan?.tier ?? "free", ends_at: owner.plan?.ends_at ?? null,
                  customer_id: owner.plan?.customer_id ?? null,
                  subscription_id: owner.plan?.subscription_id ?? null });
  }
  if (!dry) {
    writeFileSync(join(options.private!, PRIVATE_RECORD),
                  `${JSON.stringify({ format: FORMAT, owners: record }, null, 2)}\n`,
                  { flag: "wx", mode: 0o600 });
    writeFileSync(join(options.private!, DIGESTS),
                  `${JSON.stringify({ format: FORMAT, digests }, null, 2)}\n`,
                  { flag: "wx", mode: 0o600 });
  }
  return { dry_run: dry, owners: fleet.owners.length, erased: fleet.owners.length - live.length,
           refused, ...totals };
}
