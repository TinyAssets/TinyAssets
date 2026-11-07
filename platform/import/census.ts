/**
 * The census (`import_research.md` §1, slice 1): read-only counts of a backup copy, rule 0
 * held over every store, table and entry (`rules.ts`), and each owner's fit against the new
 * platform's own limits: `FILE_DEFAULTS` (`max_entries`, `max_inline_bytes`,
 * `max_file_bytes`), the stored-path rules (`contract/src/paths.ts`, read by the survey) and
 * the tier's storage from the control record. Every later choice is made from its numbers.
 *
 * Its report is counts only: no principal, no name an owner chose, no secret. The
 * per-owner table is keyed by `sha256(principal)[:16]` and is the caller's to keep 0600.
 */
import { lstatSync, readdirSync } from "node:fs";

import { ancestors, WORKSPACE } from "../contract/src/paths";
import { FILE_DEFAULTS, INLINE_BYTES } from "../contract/src/limits";
import starterJson from "../starter/starter.json" with { type: "json" };

import { fileSha256, layout } from "./backup";
import {
  type Copy, type Fleet, IMPORTED, type Owner, type Problem, type Refusal, survey,
} from "./survey";

/** The tier numbers, as the control record (`c:control`'s `tiers`) holds them. */
export type Tiers = Readonly<Record<string, { readonly storage_bytes: number }>>;

/** The files the import itself writes into an owner's tree beside what it carries. */
export const README = `${IMPORTED}/README.md`;
export const LINKS = `${IMPORTED}/links.txt`;
export const REFUSED = `${IMPORTED}/refused.tar`;
/** A tar member's header, and the most padding it takes. */
const TAR_BLOCK = 512;

/**
 * The backup copy's own integrity, before anything is read from it (research §4.4): the
 * archive's hash is the manifest's and each pinned image's is its pin's (over the
 * uncompressed image, `dr-owner-restore.py`).
 */
export function verifyCopy(copy: Copy): Problem[] {
  const where = layout(copy.work);
  const problems: Problem[] = [];
  const archive = where.archive(copy.manifest.archive);
  if (!exists(archive) || fileSha256(archive) !== copy.manifest.sha256) {
    problems.push({ code: "manifest_archive", at: "archive" });
  }
  for (const pin of copy.manifest.snapshots) {
    const image = where.image(pin.uid);
    if (!exists(image)) {
      problems.push({ code: "image_missing", at: `pin/${pin.uid}` });
    } else if (fileSha256(image) !== pin.sha256) {
      problems.push({ code: "image_mismatch", at: `pin/${pin.uid}` });
    }
  }
  return problems;
}

function exists(path: string): boolean {
  try {
    return lstatSync(path).isFile();
  } catch {
    return false;
  }
}

/** The starter's own paths, which it seeds around the imported tree (`starter.ts`). */
export function starterPaths(): string[] {
  return (starterJson as { files: { path: string }[] }).files.map((file) => file.path);
}

/** The bytes of a refused node and everything below it, and its members. */
function refusedSize(refusal: Refusal): { bytes: number; members: number } {
  const stat = lstatSync(refusal.source);
  let bytes = stat.isFile() ? stat.size : 0;
  let members = 1;
  if (stat.isDirectory()) {
    for (const name of readdirSync(refusal.source, { encoding: "buffer" })) {
      const inner = refusedSize({ member: refusal.member,
                                  source: Buffer.concat([refusal.source, Buffer.from("/"), name]) });
      bytes += inner.bytes;
      members += inner.members;
    }
  }
  return { bytes, members };
}

export interface Fit {
  readonly entries: number;
  readonly inline_bytes: number;
  readonly largest: number;
  readonly stored: number;
  readonly problems: Problem[];
}

/**
 * The owner's fit, as an upper bound the bundle then makes exact: the entries the files
 * plane would hold after the import and the starter's seed (folders count as entries), the
 * small files kept inline, the largest file, and every byte stored against the tier. An
 * owner past one is refused before the window (research §3: the import never raises a
 * setting; the fix is the owner's, told). `max_manifest_bytes` and `max_history` cannot bind
 * first: 30,000 entries at one version each take well under either.
 */
export function fitOf(owner: Owner, tiers: Tiers): Fit {
  const tree = owner.tree;
  const sizes = [...tree.carried.filter((item) => item.kind === "file").map((item) => item.size),
                 ...tree.archived.map((item) => item.size)];
  const generated: string[] = [];
  if (tree.links.length > 0) {
    generated.push(LINKS);
  }
  let refused = 0;
  if (tree.refused.length > 0) {
    generated.push(REFUSED);
    for (const refusal of tree.refused) {
      const { bytes, members } = refusedSize(refusal);
      refused += bytes + members * 3 * TAR_BLOCK;
    }
    refused += 2 * TAR_BLOCK;
    sizes.push(refused);
  }
  const paths = [...tree.carried.map((item) => item.path), ...tree.archived.map((a) => a.path),
                 ...generated];
  if (paths.length > 0) {
    paths.push(README);
    sizes.push(INLINE_BYTES);
  }
  const all = new Set<string>([...paths, ...starterPaths()]);
  for (const path of [...all]) {
    for (const dir of ancestors(path)) {
      all.add(dir);
    }
  }
  all.delete(WORKSPACE);
  const tier = tiers[owner.plan?.tier ?? "free"];
  const fit = {
    entries: all.size,
    inline_bytes: sizes.filter((size) => size <= INLINE_BYTES).reduce((a, b) => a + b, 0),
    largest: sizes.reduce((a, b) => Math.max(a, b), 0),
    stored: sizes.reduce((a, b) => a + b, 0) + tree.rows.body_bytes,
    problems: [] as Problem[],
  };
  const over = (code: Problem["code"], value: number, limit: number) => {
    if (value > limit) {
      fit.problems.push({ code, at: `owner/${owner.key}` });
    }
  };
  over("over_entries", fit.entries, FILE_DEFAULTS.max_entries);
  over("over_inline_bytes", fit.inline_bytes, FILE_DEFAULTS.max_inline_bytes);
  over("over_file_bytes", fit.largest, FILE_DEFAULTS.max_file_bytes);
  if (tier === undefined) {
    fit.problems.push({ code: "no_tier", at: `owner/${owner.key}` });
  } else {
    over("over_storage", fit.stored, tier.storage_bytes);
  }
  return fit;
}

/** Whether the owner is imported at all: never one the old stack erased. */
export function erased(owner: Owner): boolean {
  return owner.admission?.state === "erased" || owner.erasedMarker;
}

/** Every problem that stops the owner's import: its tree's, and its fit's. */
export function ownerProblems(owner: Owner, tiers: Tiers): Problem[] {
  return [...owner.tree.problems, ...fitOf(owner, tiers).problems];
}

const tally = <T extends string>(items: readonly T[]): Record<string, number> => {
  const found: Record<string, number> = {};
  for (const item of [...items].sort()) {
    found[item] = (found[item] ?? 0) + 1;
  }
  return found;
};

export interface Census {
  /** The counts that may leave the host: no principal, no owner's name, no secret. */
  readonly report: Record<string, unknown>;
  /** The per-owner table, keyed by owner key, one row a line (keep it 0600). */
  readonly table: string;
  readonly ready: boolean;
  readonly fleet: Fleet;
}

/** The census of a backup copy. */
export function census(copy: Copy, tiers: Tiers): Census {
  const copyProblems = verifyCopy(copy);
  const fleet = survey(copy);
  const live = fleet.owners.filter((owner) => !erased(owner));
  const fits = new Map(live.map((owner) => [owner.key, fitOf(owner, tiers)]));
  const problems = [...copyProblems, ...fleet.problems,
                    ...live.flatMap((owner) => [...owner.tree.problems,
                                                ...fits.get(owner.key)!.problems])];
  const stores: Record<string, number> = {};
  for (const store of [...fleet.stores, ...fleet.owners.flatMap((owner) => owner.tree.stores)]) {
    for (const table of store.tables) {
      const key = `${store.at}/${table.table} ${table.use}`;
      stores[key] = (stores[key] ?? 0) + table.rows;
    }
  }
  const sum = (pick: (owner: Owner) => number) => live.reduce((a, owner) => a + pick(owner), 0);
  const files = (owner: Owner) => owner.tree.carried.filter((item) => item.kind === "file");
  const report = {
    backup: { pins: copy.manifest.snapshots.length, verified: copyProblems.length === 0 },
    owners: {
      identities: fleet.owners.length,
      imported: live.length,
      erased: fleet.owners.length - live.length,
      admission: tally(fleet.owners.map((owner) => owner.admission?.state ?? "none")),
      tier: tally(live.map((owner) => owner.plan?.tier ?? "none")),
      with_subscription: live.filter((owner) => owner.plan?.subscription_id != null).length,
      not_authkit: fleet.owners.filter((owner) => !owner.principal.startsWith("user_")).length,
      with_volume: fleet.owners.filter((owner) => owner.volume !== null).length,
      centers: sum((owner) => owner.centers.length),
      retired_centers: sum((owner) => owner.centers.filter((center) => center.retired).length),
      orphan_accounts: fleet.orphanAccounts,
    },
    tombstones: { by_principal: fleet.tombstones.principals.length,
                  digest_only: fleet.tombstones.digests.length },
    files: {
      files: sum((owner) => files(owner).length),
      dirs: sum((owner) => owner.tree.carried.length - files(owner).length),
      bytes: sum((owner) => files(owner).reduce((a, item) => a + item.size, 0)),
      inline_files: sum((owner) => files(owner).filter((f) => f.size <= INLINE_BYTES).length),
      largest: live.reduce((a, owner) => Math.max(a, fits.get(owner.key)!.largest), 0),
      links: sum((owner) => owner.tree.links.length),
      refused_names: sum((owner) => owner.tree.refused.length),
      ignored: sum((owner) => owner.tree.ignored),
      specials: sum((owner) => owner.tree.specials),
      hardlinks: sum((owner) => owner.tree.hardlinks),
      sqlite_in_user_files: sum((owner) => owner.tree.sqliteInUser),
      archived_stores: sum((owner) => owner.tree.archived.length),
    },
    rows: { policies: sum((owner) => owner.tree.rows.policy),
            collection_rows: sum((owner) => owner.tree.rows.collection),
            body_bytes: sum((owner) => owner.tree.rows.body_bytes) },
    fit: {
      max_entries: FILE_DEFAULTS.max_entries,
      most_entries: live.reduce((a, owner) => Math.max(a, fits.get(owner.key)!.entries), 0),
      most_stored: live.reduce((a, owner) => Math.max(a, fits.get(owner.key)!.stored), 0),
    },
    stores,
    problems: tally(problems.map((problem) => problem.code)),
    ready: problems.length === 0,
  };
  const lines = ["key\tfield\tvalue"];
  for (const owner of fleet.owners) {
    const fit = fits.get(owner.key);
    const row = (field: string, value: unknown) => lines.push(`${owner.key}\t${field}\t${value}`);
    row("uid", owner.uid);
    row("admission", owner.admission === null ? "none"
      : [owner.admission.state, ...owner.admission.reasons].join(","));
    row("tier", owner.plan?.tier ?? "none");
    row("erased", erased(owner));
    row("centers", owner.centers.length);
    if (fit !== undefined) {
      row("files", files(owner).length);
      row("entries", fit.entries);
      row("inline_bytes", fit.inline_bytes);
      row("largest", fit.largest);
      row("stored", fit.stored);
    }
    row("links", owner.tree.links.length);
    row("refused_names", owner.tree.refused.length);
    row("ignored", owner.tree.ignored);
    for (const store of owner.tree.stores) {
      for (const table of store.tables) {
        row(`rows ${store.at}/${table.table}`, table.rows);
      }
    }
    for (const problem of [...owner.tree.problems, ...(fit?.problems ?? [])]) {
      row(`problem ${problem.code}`, problem.at);
    }
  }
  for (const problem of [...copyProblems, ...fleet.problems]) {
    lines.push(`fleet\tproblem ${problem.code}\t${problem.at}`);
  }
  return { report, table: `${lines.join("\n")}\n`, ready: report.ready, fleet };
}
