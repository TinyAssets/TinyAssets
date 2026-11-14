/**
 * The bundle builder (`import_research.md` §3, slice 2): the backup copy read into one
 * bundle an owner, in the contract's format (THE BUNDLE, `contract/src/import-message.ts`),
 * which the import doors and the doors' consumer take (slices 3 and 4), written to a local
 * folder at `<out>/<key>/` (`key` = `sha256(principal)[:16]`): the owner's imported tree
 * (`build.ts`), each file's content id computed by the contract's own `inlineId`/`chunkedId`
 * over the backup's bytes, its live rows, and the index last. Uploading it to R2 staging is
 * slice 4's.
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

import type { Tiers } from "../contract/src/control";
import { sha256Hex } from "../contract/src/digest";
import {
  BUNDLE_FORMAT, BUNDLE_INDEX, BUNDLE_OWNER, type BundleIndex, type BundleOwner, bundlePages,
  pageName, rehearsalName,
} from "../contract/src/import-message";

import { tierName } from "./build";
import { admissionProblems, erased, measure, verifyCopy, withheld } from "./census";
import { names } from "./erasure";
import { type Copy, type Fleet, type Owner, type Problem, survey } from "./survey";

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
  /**
   * A rehearsal's run: each bundle in `out` is then its owner's under its rehearsal's name
   * (`rehearsalName`), never its real one, and keyed by it, so the rehearsal imports into
   * objects no one can sign into and its wipe needs no erasure (`import_research.md` §4).
   */
  readonly rehearsal?: string | null;
}

/** The run's counts: no key, no digest, no name. */
export interface Summary {
  readonly dry_run: boolean;
  readonly owners: number;
  readonly bundled: number;
  readonly excluded: number;
  readonly erased: number;
  /** Owners with a deletion job not finished: in no bundle, neither imported nor backed up. */
  readonly erasure_pending: number;
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

/**
 * The last word before anything is written: refuses the run if any principal about to be
 * bundled, into the out folder or the private one, is erased or named by any erasure fact
 * (`erasure.ts`). The census withholds every such owner first, so this throwing is a bug.
 */
export function erasureGuard(fleet: Fleet, principals: Iterable<string>): void {
  const owners = new Map(fleet.owners.map((owner) => [owner.principal, owner]));
  for (const principal of principals) {
    if (names(fleet.erasure, principal) || (owners.get(principal)?.erasure ?? null) !== null) {
      throw new Error("an erased or erasing owner reached a bundle: the run is refused");
    }
  }
}

/** The owner under a rehearsal's name, keyed by it; its tree and stores its own still. */
function renamed(owner: Owner, name: string): Owner {
  return { ...owner, principal: name, key: sha256Hex(name).slice(0, 16) };
}

/** One owner's bundle at `dir`, or the fit's problems (and nothing left at `dir`). */
function writeBundle(owner: Owner, copy: Copy, dir: string | null, tiers: Tiers, fit: boolean):
    { problems: Problem[]; digest: string | null; counts: Omit<Summary, "dry_run" | "owners" |
      "bundled" | "excluded" | "erased" | "erasure_pending" | "refused"> } {
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
  const ownerPart: BundleOwner = {
    format: BUNDLE_FORMAT, owner: owner.principal, key: owner.key, uid: owner.uid,
    // An owner the old stack never admitted is "absent", never admitted by slice 5.
    admission: owner.admission ?? { state: "absent", reasons: [] },
    tier: tierName(owner), notice: NOTICE, centers: owner.centers,
  };
  part(BUNDLE_OWNER, ownerPart);
  bundlePages(entries).forEach((page, n) => part(pageName("manifest", n), { entries: page }));
  bundlePages(rows).forEach((page, n) => part(pageName("rows", n), { rows: page }));
  const bundleIndex: BundleIndex = {
    format: BUNDLE_FORMAT, key: owner.key, owner: owner.principal, parts,
    counts: { entries: entries.length, inline: store.inline, chunks: store.chunks,
              chunk_bytes: store.chunkBytes, inline_bytes: store.inlineBytes,
              rows: rows.length },
  };
  const index = `${JSON.stringify(bundleIndex)}\n`;
  writeFileSync(join(dir, BUNDLE_INDEX), index, { flag: "wx", mode: 0o600 });
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
  const live = fleet.owners.filter((owner) => !withheld(owner));
  for (const owner of live) {
    const excluded = options.exclude.has(owner.principal);
    // A backup is kept whatever the limits say; an import is held to them.
    const problems = excluded ? owner.tree.problems
      : [...owner.tree.problems, ...admissionProblems(owner)];
    if (problems.length > 0) {
      count(problems);
      continue;
    }
    erasureGuard(fleet, [owner.principal]);
    const base = excluded ? options.private : options.out;
    const named = excluded || (options.rehearsal ?? null) === null ? owner
      : renamed(owner, rehearsalName(options.rehearsal!, owner.principal));
    const built = writeBundle(named, copy, base === null ? null : join(base, named.key),
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
    record.push({ key: named.key, owner: owner.principal, uid: owner.uid, excluded,
                  tier: tierName(owner), ends_at: owner.plan?.ends_at ?? null,
                  customer_id: owner.plan?.customer_id ?? null,
                  subscription_id: owner.plan?.subscription_id ?? null });
  }
  erasureGuard(fleet, record.map((entry) => String(entry["owner"])));
  if (!dry) {
    writeFileSync(join(options.private!, PRIVATE_RECORD),
                  `${JSON.stringify({ format: BUNDLE_FORMAT, owners: record }, null, 2)}\n`,
                  { flag: "wx", mode: 0o600 });
    writeFileSync(join(options.private!, DIGESTS),
                  `${JSON.stringify({ format: BUNDLE_FORMAT, digests }, null, 2)}\n`,
                  { flag: "wx", mode: 0o600 });
  }
  return { dry_run: dry, owners: fleet.owners.length, erased: fleet.owners.filter(erased).length,
           erasure_pending: fleet.erasurePending, refused, ...totals };
}
