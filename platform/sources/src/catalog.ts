/**
 * A catalog of source records (`Sources`): the records, held together by the checker's
 * rules for a catalog (`checkedTogether`) when it is made, and each checked whole
 * (`sourceRecord`) when it is read. The platform's own are every record file beside this
 * module, listed by `records.ts`, which `write-records.ts` writes from the folder and a
 * guard holds to it, so a new record needs no hand-added import.
 */
import { object } from "../../contract/src/checks";
import { SOURCE_ID } from "../../contract/src/text";

import { checkedTogether, sourceRecord } from "./check";
import { RECORDS } from "./records";
import { SourceRecordError, type Sources } from "./schema";

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
        if (named?.unwired !== undefined) {
          throw new SourceRecordError(`source record ${JSON.stringify(record.id)}: engine ` +
            `${JSON.stringify(engine)} is unwired (${named.unwired}), so no key runs it`);
        }
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

/** The platform's records (`platform/sources/*.json`). */
export function bundledSources(): Sources {
  return catalogOf(RECORDS);
}

/** Every platform record as its file holds it, unchecked: the raw data a catalog reads. */
export function bundledRecords(): readonly unknown[] {
  return RECORDS;
}
