/**
 * The runner's on-disk format and the Worker↔runner protocol, and the rule that keeps a
 * rollback one deploy deep (runner by digest): the sandbox Worker ships the runner, so a
 * sandbox meets a newer runner, or an older one after a rollback, between its runs, its disk
 * kept; and a Worker meets a sandbox whose live run's runner is the one before its own.
 *
 * What the runner keeps on the sandbox's disk is mostly durable, never thrown away: the
 * engines' sign-ins (`/var/lib/ta/store`) and their generations, the agents' scratch and its
 * stamps, the conversations, each run's and each sign-in's state (`state.json`, which
 * `liveJobs` and so `adopt` read), and the sync mirror, which is each path's
 * base version (a sync with no mirror row writes with no base, and a path the owner has is a
 * conflict). Only true caches are discarded when the format changes: `sessions-seen.json`,
 * the engines' sessions as last hashed, rebuilt by the next run.
 *
 * The rule (expand, then contract), for the format and the protocol alike: a format change
 * ships in two deploys. The first (expand) reads the old format F and the new F+1 and still
 * writes F (`READS` holds F+1, `STATE_FORMAT` stays F); the second (contract) writes F+1 and
 * still reads F. So every runner reads the format before its own and, in an expand deploy,
 * the one after: the runner one deploy back always reads what the current one wrote, and a
 * rollback of one deploy is safe in either direction. The marker (`/var/lib/ta/format`) is
 * the newest format on the disk and is never lowered: a rolled-back runner leaves it as it
 * found it. A runner meeting a format it does not read fails loudly (`checkFormat`, at
 * `prepare` and `adopt`), and nothing runs. The protocol (`protocol.ts`: the commands and
 * the `state` answer the Worker relies on) moves the same way: the Worker speaks `SPEAKS`,
 * its runner's protocol and at most the ones either side of it.
 *
 * `state-format.json` records each format (the mirror's schema, and each persisted file's
 * fields and their types) and `protocol.json` each protocol; both are frozen once recorded
 * (`state-format.test.ts` pins every entry's digest, so a past entry can never be edited,
 * only a new one added). The tests hold what this runner writes to its format's record, and
 * have it read every format it says it reads.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { remove, safeWrite } from "./fsafe";

/** The format this runner writes. */
export const STATE_FORMAT = 1;
/** The formats it reads: its own, the one before, and (an expand deploy) the one after. */
export const READS: readonly number[] = Object.freeze([1]);
/** What a runner writes and reads. */
export interface FormatRule {
  readonly writes: number;
  readonly reads: readonly number[];
}

const THIS: FormatRule = Object.freeze({ writes: STATE_FORMAT, reads: READS });

/** Every runner's bytes in `runners` (`/opt/ta/runner`) but `own`'s gone. */
export function dropRunners(runners: string, own: string): void {
  for (const name of existsSync(runners) ? readdirSync(runners) : []) {
    if (name !== own) {
      remove(join(runners, name));
    }
  }
}

/** What a runner does on meeting a disk's format: refuse it, drop the caches, raise the marker. */
export interface FormatStep {
  /** Why it refuses the disk (a format it does not read), or null. */
  readonly refuse: string | null;
  readonly dropCaches: boolean;
  /** The marker written, or null (left as found). */
  readonly write: number | null;
}

/**
 * The rule, for a runner of `rule` meeting a disk of format `found` (null: none recorded, a
 * new sandbox): one it does not read is refused; the caches go when the disk's format is not
 * the one it writes; the marker is raised to it when it is newer, never lowered.
 */
export function formatStep(found: number | null, rule: FormatRule): FormatStep {
  if (found !== null && !rule.reads.includes(found)) {
    return { refuse: `this sandbox's state is format ${String(found)}; this runner reads ` +
                     `${rule.reads.join(" and ")}: a runner more than one deploy away, or a ` +
                     "format change shipped in one deploy", dropCaches: false, write: null };
  }
  return { refuse: null, dropCaches: found !== rule.writes,
           write: found === null || rule.writes > found ? rule.writes : null };
}

/**
 * The sandbox's state directory held to this runner's format (`formatStep`): a format it
 * does not read throws, changing nothing.
 */
export function checkFormat(state: string, caches: readonly string[]): void {
  const marker = `${state}/format`;
  const found = existsSync(marker) ? Number(readFileSync(marker, "utf8").trim()) : null;
  const step = formatStep(found, THIS);
  if (step.refuse !== null) {
    throw new Error(step.refuse);
  }
  if (step.dropCaches) {
    for (const cache of caches) {
      remove(cache);
    }
  }
  if (step.write !== null) {
    safeWrite(state, marker, new TextEncoder().encode(`${step.write}\n`), 0o600);
  }
}
