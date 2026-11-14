/**
 * A catalog of source records (`Sources`): the records, held together by the checker's
 * rules for a catalog (`checkedTogether`) when it is made, and each checked whole
 * (`sourceRecord`) when it is read. The platform's own are every record file beside this
 * module, listed by `records.ts`, which `npm run generate` writes from the folder, so a new
 * record needs no hand-added import.
 */
import { object } from "../../contract/src/checks";
import { SOURCE_ID } from "../../contract/src/text";

import { checkedTogether, sourceRecord } from "./check";
import { RECORDS } from "./records";
import { type SourceRecord, SourceRecordError, type Sources } from "./schema";

/** `records` as a catalog serves them: each checked whole when it is read. */
export function catalogOf(records: readonly unknown[]): Sources {
  checkedTogether(records);
  const byId = (id: string): unknown => records.find((record) =>
    object(record) && record["id"] === id);
  return {
    get: (id) => {
      const found = typeof id === "string" && SOURCE_ID.test(id) ? byId(id) : undefined;
      if (found === undefined) {
        return undefined;
      }
      const record = sourceRecord(found);
      // Each engine a key names is a served CLI record that runs on a key of this provider.
      for (const engine of record.engines ?? []) {
        const named = byId(engine) === undefined ? undefined : sourceRecord(byId(engine));
        const cli = named?.cli;
        if (cli?.api_key_env?.[record.upstream!.provider] === undefined) {
          throw new SourceRecordError(`source record ${JSON.stringify(record.id)}: engine ` +
            `${JSON.stringify(engine)} is a CLI record that runs on an API key of provider ` +
            `${JSON.stringify(record.upstream!.provider)}`);
        }
      }
      return record;
    },
    ids: () => records.map((record) => object(record) ? String(record["id"]) : "?"),
  };
}

/** The platform's records (`platform/sources/records/*.json`). */
export function bundledSources(): Sources {
  return catalogOf(RECORDS);
}

/**
 * A CLI record's vendor hosts, its sign-in's and its model calls', and the chosen region's
 * (`cli.regions`) when it has regions, each once: what a sign-in's network is widened by (the
 * sandbox Worker's) and the template's sign-in check runs under. A region it lacks throws.
 */
export function engineHosts(record: SourceRecord, region: string | null): string[] {
  const cli = record.cli;
  if (cli === undefined) {
    throw new Error(`engine ${record.id} has no CLI record`);
  }
  const chosen = region === null ? undefined : cli.regions?.[region];
  if (region !== null && chosen === undefined) {
    throw new Error(`engine ${record.id} has no region ${region}`);
  }
  return [...new Set([...cli.hosts.login, ...cli.hosts.provider,
                      ...(chosen?.hosts.login ?? []), ...(chosen?.hosts.provider ?? [])])];
}

/** Every platform record as its file holds it, unchecked: the raw data a catalog reads. */
export function bundledRecords(): readonly unknown[] {
  return RECORDS;
}
