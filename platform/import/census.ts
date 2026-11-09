/**
 * The census (`import_research.md` §1, slice 1): read-only counts of a backup copy, rule 0
 * held over every store, table and entry (`rules.ts`), and each owner's fit against the new
 * platform's own limits: `FILE_DEFAULTS` (`max_entries`, `max_inline_bytes`,
 * `max_file_bytes`), the stored-path rules (`contract/src/paths.ts`, read by the survey) and
 * the tier's storage from the control record, each counted by the files plane's own rule on
 * the tree the bundle builder would make (`build.ts`). Every later choice is made from its
 * numbers.
 *
 * Its report is counts only: no principal, no name an owner chose, no secret, no key. The
 * per-owner table is keyed by `sha256(principal)[:16]` and is the caller's to keep 0600.
 */
import { lstatSync, readdirSync } from "node:fs";

import { INLINE_BYTES } from "../contract/src/limits";
import { FILE_DEFAULTS } from "../contract/src/file-defaults";

import { fileSha256, layout } from "./backup";
import { type Built, buildTree, type Fit, fitOf, starterPaths, type Tiers } from "./build";
import { type Copy, type Fleet, type Owner, type Problem, type Refusal, survey } from "./survey";
import { TAR_MEMBER_MAX } from "./tar";

export type { Tiers } from "./build";

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

/** Whether the old stack erased the owner (its admission reads erased). */
export function erased(owner: Owner): boolean {
  return owner.erasure === "erased";
}

/**
 * Whether the owner is withheld from every bundle: any erasure fact names them
 * (`erasure.ts`), erased or with the erasure not yet through (the person's request
 * honoured, though the backup predates its end).
 */
export function withheld(owner: Owner): boolean {
  return owner.erasure !== null;
}

/**
 * An owner the old stack never admitted (no `owner_admission` row: `admission.py`'s
 * "absent", `OwnerUnadmitted`): refused and counted, so slice 5's admit step never admits
 * one by accident.
 */
export function admissionProblems(owner: Owner): Problem[] {
  return owner.admission === null ? [{ code: "never_admitted", at: `owner/${owner.key}` }] : [];
}

/** The largest regular file at or below `source`, never following a link. */
function largestBelow(source: Buffer): number {
  const stat = lstatSync(source);
  if (stat.isFile()) {
    return stat.size;
  }
  if (!stat.isDirectory()) {
    return 0;
  }
  let largest = 0;
  for (const name of readdirSync(source, { encoding: "buffer" })) {
    largest = Math.max(largest, largestBelow(Buffer.concat([source, Buffer.from("/"), name])));
  }
  return largest;
}

/**
 * What refuses an owner before its tree is made, read from sizes alone: a file past the
 * platform's largest or the tier's storage, more carried paths than `max_entries`, or a
 * file past what a tar member can hold in `refused.tar` or `ignored.tar`. So a 40 GiB file
 * is refused by its size, never read.
 */
export function precheck(owner: Owner, tiers: Tiers): Problem[] {
  const tree = owner.tree;
  const at = `owner/${owner.key}`;
  const largest = [...tree.carried.map((item) => item.size), ...tree.archived.map((a) => a.size)]
    .reduce((a, b) => Math.max(a, b), 0);
  const members = (list: readonly Refusal[]) =>
    list.reduce((a, member) => Math.max(a, largestBelow(member.source)), 0);
  const problems: Problem[] = [];
  if (largest > FILE_DEFAULTS.max_file_bytes) {
    problems.push({ code: "over_file_bytes", at });
  }
  const tier = tiers[owner.plan?.tier ?? "free"];
  if (tier !== undefined && largest > tier.storage_bytes) {
    problems.push({ code: "over_storage", at });
  }
  if (tree.carried.length + tree.archived.length + starterPaths().length
      > FILE_DEFAULTS.max_entries) {
    problems.push({ code: "over_entries", at });
  }
  if (Math.max(members(tree.refused), members(tree.ignoredTrees)) > TAR_MEMBER_MAX) {
    problems.push({ code: "over_tar_member", at });
  }
  return problems;
}

/**
 * The owner's fit, measured on its tree as the bundle builder makes it (written to `dir`,
 * or counted and not kept), once nothing refuses it first. With `tiers` null (a backup,
 * which the limits do not bind) only what a tar cannot hold refuses it.
 */
export function measure(owner: Owner, copy: Copy, tiers: Tiers | null, dir: string | null):
    { built: Built | null; fit: Fit | null; problems: Problem[] } {
  const first = precheck(owner, tiers ?? {})
    .filter((problem) => tiers !== null || problem.code === "over_tar_member");
  if (first.length > 0) {
    return { built: null, fit: null, problems: first };
  }
  const built = buildTree(owner, copy, dir);
  if (tiers === null) {
    return { built, fit: null, problems: [] };
  }
  const fit = fitOf(owner, built, tiers);
  return { built, fit, problems: fit.problems };
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
  const live = fleet.owners.filter((owner) => !withheld(owner));
  const fits = new Map<string, Fit>();
  const owned = new Map<string, Problem[]>();
  let archivedPolicies = 0;
  for (const owner of live) {
    const problems = [...owner.tree.problems, ...admissionProblems(owner)];
    if (owner.tree.problems.length === 0) {
      const measured = measure(owner, copy, tiers, null);
      problems.push(...measured.problems);
      archivedPolicies += measured.built?.policyArchived === true ? 1 : 0;
      if (measured.fit !== null) {
        fits.set(owner.key, measured.fit);
      }
    }
    owned.set(owner.key, problems);
  }
  const problems = [...copyProblems, ...fleet.problems, ...[...owned.values()].flat()];
  const stores: Record<string, number> = {};
  for (const store of [...fleet.stores, ...fleet.owners.flatMap((owner) => owner.tree.stores)]) {
    if (store.tables.length === 0) {
      // A store kept only in the final backup, never read: how many there are.
      stores[`${store.at} drop, not read`] = (stores[`${store.at} drop, not read`] ?? 0) + 1;
    }
    for (const table of store.tables) {
      const key = `${store.at}/${table.table} ${table.use}`;
      stores[key] = (stores[key] ?? 0) + table.rows;
    }
  }
  const sum = (pick: (owner: Owner) => number) => live.reduce((a, owner) => a + pick(owner), 0);
  const files = (owner: Owner) => owner.tree.carried.filter((item) => item.kind === "file");
  const most = (pick: (fit: Fit) => number) =>
    [...fits.values()].reduce((a, fit) => Math.max(a, pick(fit)), 0);
  const report = {
    backup: { pins: copy.manifest.snapshots.length, verified: copyProblems.length === 0 },
    owners: {
      identities: fleet.owners.length,
      imported: live.length,
      erased: fleet.owners.filter(erased).length,
      erasure_pending: fleet.erasurePending,
      admission: tally(fleet.owners.map((owner) => owner.admission?.state ?? "absent")),
      tier: tally(live.map((owner) => owner.plan?.tier ?? "none")),
      with_customer: live.filter((owner) => owner.plan?.customer_id != null).length,
      with_subscription: live.filter((owner) => owner.plan?.subscription_id != null).length,
      not_authkit: fleet.owners.filter((owner) => !owner.principal.startsWith("user_")).length,
      with_volume: fleet.owners.filter((owner) => owner.volume !== null).length,
      no_volume: fleet.noVolume,
      // A box never run, or one whose local image the old stack lost before any snapshot
      // (`image_absent`): either way no files, imported empty.
      never_ran: fleet.neverRan,
      centers: sum((owner) => owner.centers.length),
      retired_centers: sum((owner) => owner.centers.filter((center) => center.retired).length),
      orphan_accounts: fleet.orphanAccounts,
      orphan_rows: fleet.orphans,
    },
    tombstones: { by_principal: fleet.tombstones.principals.length,
                  digest_only: fleet.tombstones.digests.length },
    files: {
      files: sum((owner) => files(owner).length),
      dirs: sum((owner) => owner.tree.carried.length - files(owner).length),
      bytes: sum((owner) => files(owner).reduce((a, item) => a + item.size, 0)),
      inline_files: sum((owner) => files(owner).filter((f) => f.size <= INLINE_BYTES).length),
      largest: most((fit) => fit.largest),
      links: sum((owner) => owner.tree.links.length),
      refused_names: sum((owner) => owner.tree.refused.length),
      ignored: sum((owner) => owner.tree.ignored),
      ignored_trees: sum((owner) => owner.tree.ignoredTrees.length),
      specials: sum((owner) => owner.tree.specials),
      hardlinks: sum((owner) => owner.tree.hardlinks),
      sqlite_in_user_files: sum((owner) => owner.tree.sqliteInUser),
      archived_stores: sum((owner) => owner.tree.archived.length),
    },
    // A policy the platform's validator refuses: archived as a file, the default kept.
    rows: { policies: sum((owner) => owner.tree.rows.policy),
            policies_archived: archivedPolicies,
            collection_rows: sum((owner) => owner.tree.rows.collection),
            body_bytes: sum((owner) => owner.tree.rows.body_bytes) },
    fit: {
      max_entries: FILE_DEFAULTS.max_entries,
      most_entries: most((fit) => fit.entries),
      most_stored: most((fit) => fit.stored),
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
    row("admission", owner.admission === null ? "absent"
      : [owner.admission.state, ...owner.admission.reasons].join(","));
    row("tier", owner.plan?.tier ?? "none");
    row("erased", erased(owner));
    row("erasure_pending", owner.erasure === "pending");
    row("centers", owner.centers.length);
    row("files", files(owner).length);
    if (fit !== undefined) {
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
    for (const problem of owned.get(owner.key) ?? []) {
      row(`problem ${problem.code}`, problem.at);
    }
  }
  for (const problem of [...copyProblems, ...fleet.problems]) {
    lines.push(`fleet\tproblem ${problem.code}\t${problem.at}`);
  }
  return { report, table: `${lines.join("\n")}\n`, ready: report.ready, fleet };
}
