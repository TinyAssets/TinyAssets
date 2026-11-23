/**
 * The owner's answers the doors read, as the door table declares them (`contract/doors.json`
 * `answer`), which the deploy plan holds across serving versions (`deploy/handed.ts`): the
 * fields the doors' code and their app read, each found by their compiler
 * (`ci/kit/answer-reads.ts`), and no other. The app is its served scripts
 * (`src/pages.ts` `STATIC`), typed by the routes' answers (`src/app.ts` `AppAnswer`). And
 * the doors' tests' own types of the owner's answers past those (`test/answers.ts`
 * `TestAnswers`) are what the owner answers (`docs/decisions/untangle-2026-11/answer-readers.md`).
 */
import { readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { type AnswerRead, answerReads, readerProgram, unanswered } from "../../ci/kit/answer-reads";
import { PLATFORM } from "../../ci/paths";
import { doorTable } from "../../contract/src/doors";
import { STATIC, STATIC_PREFIX } from "../src/pages";

/** Every module of the doors' own source, from `platform/`. */
function sources(dir: string): string[] {
  return readdirSync(join(PLATFORM, dir), { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? sources(`${dir}/${entry.name}`)
      : entry.name.endsWith(".ts") ? [`${dir}/${entry.name}`] : []);
}

/** The readers a deploy pairs with the owner: the doors' code, and the app's served scripts. */
const READERS = Object.freeze([
  ...sources("doors/src"),
  ...Object.keys(STATIC).filter((name) => name.endsWith(".js")).map((name) => `doors/app/${name}`),
]);
/** The app's scripts import one another by the path the doors serve them at. */
const SERVED = Object.freeze({ [`${STATIC_PREFIX}*`]: ["doors/app/*"] });
const TEST_ANSWERS = "doors/test/answers.ts#TestAnswers";
const OWNER = "owner/src/owner-do.ts#OwnerDO";

/** The module of a `<path>#<name>`. */
const moduleOf = (named: string) => named.split("#")[0]!;

/** `door.field` for each read, sorted, once each. */
const named = (reads: readonly AnswerRead[]) =>
  [...new Set(reads.map(({ door, field }) => `${door}.${field}`))].sort();

/** Each answer field the table declares the doors read: its fields, but `unread` ones. */
function declared(): string[] {
  return doorTable().flatMap((row) => row.answer === undefined ? []
    : Object.keys({ ...row.answer.required, ...row.answer.optional })
      .filter((field) => !(row.answer!.unread ?? []).includes(field))
      .map((field) => `${row.door}.${field}`)).sort();
}

describe("the owner's answers the doors read", () => {
  it("are what the table declares: every field it declares read, and no other", () => {
    const { reads, undeclared, unheld } = answerReads(readerProgram(READERS, SERVED), READERS);
    const read = named(reads);
    expect(read.filter((each) => !declared().includes(each)), "read, not declared").toEqual([]);
    expect(declared().filter((each) => !read.includes(each)), "declared, not read").toEqual([]);
    expect(undeclared.map(({ door, field, at }) => `${at}: ${door}.${field}`)).toEqual([]);
    expect(unheld).toEqual([]);
  });

  it("past what the doors read, are typed by the doors' tests as the owner answers them", () => {
    const program = readerProgram([moduleOf(TEST_ANSWERS), moduleOf(OWNER)]);
    expect(unanswered(program, TEST_ANSWERS, OWNER)).toEqual([]);
  });

  it("find each read, and each read no type holds, in a probe of the doors and of the app", () => {
    const probe = "doors/src/zz-probe.ts";
    const app = "doors/app/zz-probe.js";
    const found = readerProgram([probe, app], SERVED, {
      [probe]: [
        'import type { DoorAnswer } from "../../contract/src/owner";',
        'declare const decided: DoorAnswer<"runDecide">;',
        'declare const listed: DoorAnswer<"connectionList">;',
        "declare const passed: object;",
        "declare function keep(listing: { connections: unknown }): void;",
        'export const asked = "verdict" in decided;',
        'export const bare = "x" in passed;',
        "export const told = (decided as { verdict: string }).verdict;",
        "keep(listed);",
        "export interface Typed {",
        "  readonly grantList: { readonly clients: string };",
        "  readonly noticeList: object;",
        "  readonly tierRead: { readonly tier: string };",
        "}",
        "export class Owner {",
        "  async grantList() { return { clients: [] as string[] }; }",
        "  async tierRead() { return { tier: 'free' }; }",
        "}",
      ].join("\n"),
      [app]: [
        'import { call } from "/app/static/api.js";',
        'const { notices, gone } = await call("notices");',
        "export { notices, gone };",
      ].join("\n"),
    });
    const { reads, undeclared, unheld } = answerReads(found, [probe, app]);
    expect(named(reads)).toEqual(["connectionList.connections", "noticeList.notices"]);
    expect(undeclared.map(({ door, field, at }) => `${at}: ${door}.${field}`)).toEqual([
      "doors/src/zz-probe.ts:6: runDecide.verdict", "doors/app/zz-probe.js:2: noticeList.gone"]);
    expect(unheld).toEqual([
      'doors/src/zz-probe.ts:7: an `in` check of "x" on a bare object',
      "doors/src/zz-probe.ts:8: a cast of an answer to { verdict: string; }"]);
    expect(unanswered(found, `${probe}#Typed`, `${probe}#Owner`)).toEqual([
      "grantList: Owner answers { clients: string[]; }", "noticeList: Owner has no such door"]);
  });
});
