/**
 * The owner's answers the runner reads, as the doors relay them (`contract/src/owner.ts`
 * `RelayedAnswer`), found by its compiler (`ci/kit/answer-reads.ts`): each recorded in its
 * protocol's record (`runner/protocol.json` `relayed`) as the door table types it, which the
 * deploy kit holds the owner to while a sandbox Worker speaks that protocol (`deploy/handed.ts`;
 * `docs/decisions/untangle-2026-11/runner-answers.md`). A record is frozen, so it may name
 * more: what an earlier runner of its protocol read.
 */
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { isDeepStrictEqual } from "node:util";

import { describe, expect, it } from "vitest";

import { answerReads, readerProgram } from "../../ci/kit/answer-reads";
import { PLATFORM } from "../../ci/paths";
import { type Answer, doorTable } from "../../contract/src/doors";
import { PROTOCOL } from "../runner/protocol";
import { PROTOCOLS } from "../runner/test/state-shape";

/** The runner's own modules, from `platform/`: its source, not its tests. */
const RUNNER = Object.freeze(readdirSync(join(PLATFORM, "sandbox/runner"))
  .filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"))
  .map((name) => `sandbox/runner/${name}`));

/** `field` of `answer` as declared: whether always answered, and its schema. */
const declared = (answer: Answer | undefined, field: string) =>
  answer === undefined ? undefined
    : Object.hasOwn(answer.required, field) ? { required: answer.required[field] }
      : Object.hasOwn(answer.optional, field) ? { optional: answer.optional[field] } : undefined;

describe("the owner's answers the runner reads", () => {
  it("are each in its protocol's record, as the door table types them", () => {
    const { reads, undeclared, unheld } = answerReads(readerProgram(RUNNER), RUNNER);
    expect(undeclared.map(({ door, field, at }) => `${at}: ${door}.${field}`)).toEqual([]);
    expect(unheld).toEqual([]);
    const record = PROTOCOLS[String(PROTOCOL)]!.relayed ?? {};
    // The hold skips a field a reader marks `unread` (`deploy/handed.ts`): a record marks none.
    expect(Object.entries(PROTOCOLS).flatMap(([n, each]) => Object.entries(each.relayed ?? {})
      .filter(([, answer]) => answer.unread !== undefined).map(([door]) => `${n}: ${door}`)))
      .toEqual([]);
    const rows = new Map(doorTable().map((row) => [row.door, row.answer]));
    const read = [...new Set(reads.map(({ door, field }) => `${door}.${field}`))].sort();
    expect(read.filter((each) => {
      const [door, field] = each.split(".") as [string, string];
      const recorded = declared(record[door], field);
      return recorded === undefined
        || !isDeepStrictEqual(recorded, declared(rows.get(door), field));
    }), `read, not in protocol ${PROTOCOL}'s record as the table types it`).toEqual([]);
  });
});
