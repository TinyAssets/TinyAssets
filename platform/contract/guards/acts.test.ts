/**
 * The acts an owner's `actions` rule names (`contract/acts.json`, read by `src/acts.ts`), and
 * the door rows that name the act they do (`doors.json` `act`): each held whole, and no word of
 * the vocabulary unused, so the file says only what something reads.
 */
import { describe, expect, it } from "vitest";

import raw from "../acts.json" with { type: "json" };
import doors from "../doors.json" with { type: "json" };
import { actNamed, actProblems } from "../src/acts";
import { actDoors, doorProblems, doorTable } from "../src/doors";

const ACTS = raw.acts as Record<string, { hook: string[]; unseen: string[] }>;

describe("the acts file", () => {
  it("holds every act whole", () => {
    expect(actProblems(raw)).toEqual([]);
    expect(Object.keys(ACTS).length).toBeGreaterThan(5);
    expect(Object.isFrozen(actNamed("share"))).toBe(true);
    expect([actNamed("commons.publish"), actNamed("toString"), actNamed(7)])
      .toEqual([undefined, undefined, undefined]);
  });

  it.each<readonly [string, unknown, string]>([
    ["a table missing", { ...raw, kinds: undefined },
      "the file is its about, acts, conditions, kinds and unseen ways"],
    ["a key more", { ...raw, extra: {} },
      "the file is its about, acts, conditions, kinds and unseen ways"],
    ["an act's name", { ...raw, acts: { ...raw.acts, "Share!": raw.acts.share } },
      "Share!: not an act's name"],
    ["an act's key", { ...raw, acts: { ...raw.acts, share: { ...raw.acts.share, doors: [] } } },
      "share: an act is its words, its hook kinds and its unseen ways"],
    ["an act's words", { ...raw, acts: { ...raw.acts, share: { ...raw.acts.share, says: "" } } },
      "share: no words"],
    ["a hook kind", { ...raw, acts: { ...raw.acts, share: { ...raw.acts.share,
                                                            hook: ["web_fetch"] } } },
      "share: hook kinds the file does not define, or one twice"],
    ["a hook kind twice", { ...raw, acts: { ...raw.acts, share: { ...raw.acts.share,
                                                                  hook: ["shell", "shell"] } } },
      "share: hook kinds the file does not define, or one twice"],
    ["an unseen way", { ...raw, acts: { ...raw.acts, share: { ...raw.acts.share,
                                                              unseen: ["anywhere"] } } },
      "share: unseen ways the file does not define, or one twice"],
    ["a word with none", { ...raw, kinds: { ...raw.kinds, mcp: "" } },
      "kinds mcp: a name and its words"],
  ])("refuses %s", (_what, file, problem) => {
    expect(actProblems(file)).toEqual([problem]);
  });

  it("defines no kind, condition or unseen way that nothing names", () => {
    const named = (list: (each: { hook: string[]; unseen: string[] }) => string[]) =>
      new Set(Object.values(ACTS).flatMap(list));
    expect(Object.keys(raw.kinds).filter((kind) => !named((act) => act.hook).has(kind)))
      .toEqual([]);
    expect(Object.keys(raw.unseen).filter((way) => !named((act) => act.unseen).has(way)))
      .toEqual([]);
    const conditions = new Set<string>(doorTable().flatMap((row) => row.act?.when ?? []));
    expect(Object.keys(raw.conditions).filter((name) => !conditions.has(name))).toEqual([]);
  });
});

describe("a door's act", () => {
  it("names the act each door that does one does", () => {
    expect(actDoors("people.message")).toEqual(["roomPost"]);
    expect(actDoors("share")).toEqual(["collectionWrite"]);
    expect(actDoors("workspace.files")).toContain("fileSyncCommit");
    expect(actDoors("browser.action")).toEqual([]);
  });

  it.each<readonly [string, unknown]>([
    ["an act the file does not hold", { name: "commons.publish" }],
    ["a condition the file does not define", { name: "share", when: "always" }],
    ["a key more", { name: "share", why: "x" }],
    ["no name", { when: "public_row" }],
    ["a bare name", "share"],
  ])("refuses %s", (_what, act) => {
    const rows = doors.doors.map((row) => row.door === "collectionWrite" ? { ...row, act } : row);
    expect(doorProblems(rows))
      .toEqual(["collectionWrite: an act is one of acts.json's, and when, one of its conditions"]);
  });
});
