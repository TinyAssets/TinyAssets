/**
 * The runner's on-disk format, and the rule that keeps a rollback one deploy deep (runner by
 * digest): the sandbox Worker ships the runner, so a sandbox meets a newer runner, or an
 * older one after a rollback, between its runs, its disk kept.
 *
 * What the runner keeps on the sandbox's disk is mostly durable, never thrown away: the
 * engines' sign-ins (`/var/lib/ta/store`) and their generations, the agents' scratch and its
 * stamps, the conversations, and the sync mirror, which is each path's base version (a sync
 * with no mirror row writes with no base, and a path the owner has is a conflict). Only
 * true caches are discarded when the format changes: `sessions-seen.json`, the engines'
 * sessions as last hashed, rebuilt by the next run.
 *
 * The rule (expand, then contract): a runner reads the format it writes and the one before
 * it (`READS`), and fails loudly on any other; a change of format ships in two deploys, the
 * first reading both and still writing the old one, the second writing the new one. So the
 * runner one deploy back always reads what the current one wrote. `state-format.json`
 * records each format's schema; `state-format.test.ts` holds what this runner writes to its
 * format's record and has it read every format it says it reads.
 */
import { existsSync, readFileSync } from "node:fs";

import { remove, safeWrite } from "./fsafe";

/** The format this runner writes. */
export const STATE_FORMAT = 1;
/** The formats it reads: its own, and at most the one before it. */
export const READS: readonly number[] = Object.freeze([1]);

/**
 * The sandbox's state directory held to this runner's format: none recorded (a new
 * sandbox), or one it reads, and the marker is set to the format it writes, the caches
 * (`caches`) discarded when that changes it; a format it does not read throws.
 */
export function checkFormat(state: string, caches: readonly string[]): void {
  const marker = `${state}/format`;
  const found = existsSync(marker) ? Number(readFileSync(marker, "utf8").trim()) : null;
  if (found !== null && !READS.includes(found)) {
    throw new Error(`this sandbox's state is format ${String(found)}; this runner reads ` +
                    `${READS.join(" and ")}: a runner more than one deploy back, or a format ` +
                    "change shipped in one deploy");
  }
  if (found !== STATE_FORMAT) {
    for (const cache of caches) {
      remove(cache);
    }
    safeWrite(state, marker, new TextEncoder().encode(`${STATE_FORMAT}\n`), 0o600);
  }
}
