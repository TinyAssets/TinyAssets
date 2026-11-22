/**
 * The forward list (`platform.json` `forward`), held against the base branch: it only grows,
 * as the migration locks do, so no Worker's forward count, which its version's tag carries
 * and a rollback reads (`deploy.ts` `forwardOnly`), ever falls or changes meaning; and a
 * change of the sandbox's pinned template (`sandbox/template/pair.ts` `pinned`) adds a
 * change naming the sandbox, since a rolled-back sandbox Worker resumes a sandbox made from
 * the newer image with its older runner, a pair no check passed. Every other kind it names is
 * the author's to add and the reviewer's to find missing. The base is `ci/history.ts`'s.
 */
import { describe, expect, it } from "vitest";

import { baseOf, fileAt } from "../../ci/history";
import { parseWrangler } from "../../ci/wrangler";
import { pinned } from "../../sandbox/template/pair";
import { FORWARD, forwardCounts, NAMES } from "../facts";
import platform from "../platform.json" with { type: "json" };

const PLATFORM_JSON = "platform/deploy/platform.json";
const SANDBOX_CONFIG = "platform/sandbox/wrangler.jsonc";

/** What the list and the pinned template are on one side of the change. */
interface Side {
  readonly changes: readonly unknown[];
  readonly template: string;
}

/** Each way `head` is not `base` with changes appended, and a template change unmarked. */
function forwardProblems(base: Side, head: Side): string[] {
  const found: string[] = [];
  if (JSON.stringify(head.changes.slice(0, base.changes.length))
      !== JSON.stringify(base.changes)) {
    found.push("a forward change was edited or taken away: a rollback reads each Worker's " +
               "count from its version's tag, so add a change instead");
  }
  const sandbox = (side: Side) => forwardCounts(side.changes.slice(base.changes.length), NAMES)[
    "sandbox"]!;
  if (head.template !== base.template && sandbox(head) === 0) {
    found.push(`the sandbox's pinned template moves from ${base.template} to ${head.template} ` +
               "with no forward change naming the sandbox: add one (a rolled-back sandbox " +
               "Worker would resume its sandboxes with its older runner)");
  }
  return found;
}

/** The base's side; a base before the list holds none. */
function baseSide(base: string): Side {
  const json = fileAt(base, PLATFORM_JSON);
  const config = fileAt(base, SANDBOX_CONFIG);
  if (json === null || config === null) {
    throw new Error(`${base} holds no ${json === null ? PLATFORM_JSON : SANDBOX_CONFIG}`);
  }
  const held = (JSON.parse(json) as { forward?: { changes?: unknown[] } }).forward;
  return { changes: held?.changes ?? [],
           template: String((parseWrangler(config) as { vars: { E2B_TEMPLATE: string } }).vars
             .E2B_TEMPLATE) };
}

describe("the forward list against the base branch", () => {
  it("only grows, and a moved template adds a change naming the sandbox", () => {
    expect(forwardProblems(baseSide(baseOf()), { changes: platform.forward.changes,
                                                 template: pinned() })).toEqual([]);
    expect(FORWARD).toEqual(forwardCounts(platform.forward.changes, NAMES));
  });

  it("refuses an edited or dropped change, and a template moved unmarked", () => {
    const one = { workers: ["doors"], why: "its app's page asks /app/api in a new shape" };
    const two = { workers: ["sandbox"], why: "the image's engines write a new sign-in layout" };
    const base: Side = { changes: [one], template: "tinyassets-sandbox:s1" };
    expect(forwardProblems(base, { changes: [one, two], template: "tinyassets-sandbox:s2" }))
      .toEqual([]);
    expect(forwardProblems(base, { changes: [one], template: base.template })).toEqual([]);
    const edited = "a forward change was edited or taken away: a rollback reads each Worker's " +
      "count from its version's tag, so add a change instead";
    expect(forwardProblems(base, { changes: [{ ...one, why: "another" }, two],
                                   template: base.template })).toEqual([edited]);
    expect(forwardProblems(base, { changes: [two], template: base.template })).toEqual([edited]);
    const moved = "the sandbox's pinned template moves from tinyassets-sandbox:s1 to " +
      "tinyassets-sandbox:s2 with no forward change naming the sandbox: add one (a " +
      "rolled-back sandbox Worker would resume its sandboxes with its older runner)";
    expect(forwardProblems(base, { changes: [one], template: "tinyassets-sandbox:s2" }))
      .toEqual([moved]);
    // A change the base already holds does not mark a template moved now.
    expect(forwardProblems({ changes: [two], template: "tinyassets-sandbox:s1" },
                           { changes: [two], template: "tinyassets-sandbox:s2" }))
      .toEqual([moved]);
  });
});
