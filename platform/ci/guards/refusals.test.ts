/**
 * The refusal guard (untangle B2, `ci/kit/refusals.ts`): no Worker's own source has an error
 * class of its own or reads a code from a message, but for exactly what each workspace owes
 * (`<workspace>/guards/rules.ts`'s `OWED`, `ci/kit/owed.ts`).
 */
import { describe, expect, it } from "vitest";

import { owedLists, ratchet } from "../kit/owed";
import { refusalProblems } from "../kit/refusals";
import { workerSources } from "../kit/sources";

/** What the guard finds in one source at `path`, from `platform/`. */
const found = (path: string, source: string) =>
  refusalProblems(new Map([[path, source]])).map((each) => `${each.workspace} ${each.what}`);

describe("the refusal guard", () => {
  it("finds every break in a Worker's own source, but for exactly what is owed", async () => {
    const problems = refusalProblems(workerSources());
    expect(problems.length).toBeGreaterThan(0);
    expect(ratchet("refusals", problems, await owedLists())).toEqual([]);
  });

  it("finds a Worker's own error class, whatever error it extends", () => {
    expect(found("doors/src/a.ts", [
      "export class A extends Error {}",
      "class B extends TypeError {}",
      "const C = class extends Refusal {};",
      "class D extends errors.FileFault {}",
      "class E extends Map {}",
      "class F {}",
    ].join("\n"))).toEqual([
      "doors platform/doors/src/a.ts: class A extends Error",
      "doors platform/doors/src/a.ts: class B extends TypeError",
      "doors platform/doors/src/a.ts: class (anonymous) extends Refusal",
      "doors platform/doors/src/a.ts: class D extends FileFault",
    ]);
  });

  it("finds a code read from a message, by a string method, a regex or a comparison", () => {
    expect(found("owner/src/a.ts", [
      "if (error.message.startsWith('storage_busy:')) {}",
      "const [code] = e.message.split(':');",
      "if (/^busy/.test(String((error as Error)?.message))) {}",
      "if (error['message'] === ERASING) {}",
      "const first = error.message.split('\\n')[0];",
      "const said = error.message;",
      "if (code.startsWith('x')) {}",
    ].join("\n"))).toEqual([
      "owner platform/owner/src/a.ts: reads a code from a message: " +
        "error.message.startsWith('storage_busy:')",
      "owner platform/owner/src/a.ts: reads a code from a message: e.message.split(':')",
      "owner platform/owner/src/a.ts: reads a code from a message: " +
        "/^busy/.test(String((error as Error)?.message))",
      "owner platform/owner/src/a.ts: reads a code from a message: error['message'] === ERASING",
    ]);
  });

  it("holds a Worker's own source and nothing else", () => {
    const source = "export class A extends Error {}";
    expect([found("contract/src/a.ts", source), found("doors/test/a.ts", source),
            found("sources/src/a.ts", source), found("doors/src/a.ts", source)])
      .toEqual([[], [], [], ["doors platform/doors/src/a.ts: class A extends Error"]]);
  });
});
