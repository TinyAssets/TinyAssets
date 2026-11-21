/**
 * The old policy mapped to the new rules (`policy.ts`; d3 §N7): the old defaults written for
 * an owner who never set a rule, each act decided as the old stack decided it, what is keyed to
 * the old connections left behind, egress `ask` as the list it was, the owners refused loudly,
 * the old tables held to the Python's own, and the notice's words within the notice's bound.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { REPO } from "../../ci/paths";
import { NOTICE_DETAIL } from "../../contract/src/limits";
import { IMPORTED } from "../../contract/src/paths";
import {
  BEHAVIOURS, type Carried, DEFAULTS, mapPolicy, noticeOf, readmeLines, STRICTEST,
} from "../policy";

const SPEND = { daily: null, monthly: null, transaction: null, ask_above: null,
                source_ceilings: {} };
const old = (entries: unknown[], more: Record<string, unknown> = {}) => JSON.stringify({
  entries, egress: { mode: "open", destinations: [] }, spend: SPEND, ...more });
const rule = (action_class: string, behaviour: string, values: Record<string, unknown> = {}) =>
  ({ scope: "account", table: "rules", values: { action_class, behaviour, ...values } });
const carried = (text: string | null): Carried => {
  const mapped = mapPolicy(text);
  if (!("document" in mapped)) {
    throw new Error(`refused: ${mapped.problem}`);
  }
  return mapped;
};

/** The old defaults, as the new rules write them. */
const DEFAULT_ACTIONS = [
  { act: "people.message", do: "ask" }, { act: "share", do: "ask" },
  { act: "security.change", do: "never" }, { act: "access.grant", do: "never" },
];
/** The old defaults nothing carries: no act of the name, or none seen with no tools named. */
const DEFAULT_LEFT = [
  { old: "commons.publish", why: "no_act" }, { old: "browser.action", why: "unseen" },
  { old: "spend", why: "unseen" }, { old: "money.move", why: "unseen" },
];

describe("the old policy, mapped", () => {
  it("writes the old defaults for an owner who never set a rule, or had no document", () => {
    for (const text of [null, old([])]) {
      expect(carried(text)).toEqual({
        document: { egress: { mode: "open", destinations: [] }, actions: DEFAULT_ACTIONS },
        notCarried: DEFAULT_LEFT, egressAsk: false, entries: 0,
      });
    }
  });

  it("decides each act as the old stack did: strictest wins, the main agent's for every " +
     "agent", () => {
    const actions = (entries: unknown[]) => carried(old(entries)).document.actions;
    // The main agent's `do` loosens a default for every agent.
    expect(actions([rule("people.message", "do")]))
      .toEqual(DEFAULT_ACTIONS.filter((a) => a.act !== "people.message"));
    // An agent's own stricter rule is its own; the main agent's holds for every agent.
    expect(actions([rule("workspace.shell", "ask_first"),
                    rule("workspace.shell", "hand_off", { agent: "writer" })]))
      .toEqual([{ act: "workspace.shell", do: "ask" },
                { act: "workspace.shell", do: "never", agent: "writer" }, ...DEFAULT_ACTIONS]);
    // An agent the owner configured keeps the default where only the main agent loosened it.
    expect(actions([rule("people.message", "do"), rule("workspace.files", "do", { agent: "y" })]))
      .toEqual([{ act: "people.message", do: "ask", agent: "y" },
                ...DEFAULT_ACTIONS.filter((a) => a.act !== "people.message")]);
    // On the strictest acts, any configured agent's rule decides for every agent.
    expect(actions([rule("security.change", "do"),
                    rule("security.change", "ask_first", { agent: "x" })]))
      .toEqual(DEFAULT_ACTIONS.map((a) => a.act === "security.change" ? { ...a, do: "ask" } : a));
    // `do_if_preapproved` asks; an agent no new id can name holds for every agent.
    expect(actions([rule("workspace.files", "do_if_preapproved"),
                    rule("workspace.workflows", "hand_off", { agent: "*" })]))
      .toEqual([{ act: "workspace.files", do: "ask" }, { act: "workspace.workflows", do: "never" },
                ...DEFAULT_ACTIONS]);
  });

  it("leaves behind what was the old connections', and every act with no place", () => {
    const mapped = carried(old([
      rule("people.message", "do", { connection: "c1" }),
      rule("share", "do", { operation: "POST" }),
      rule("money.move", "do", { record_kind: "preapproval", grant_json: "{}" }),
      { scope: "account", table: "approval_grants", values: {} },
      { scope: "account", table: "operation_kinds", values: { kind: "write" } },
      rule("app.write", "hand_off"), rule("shell.egress", "ask_first"),
    ]));
    expect(mapped.document.actions).toEqual(DEFAULT_ACTIONS);
    expect(mapped.notCarried).toEqual([
      { old: "people.message", why: "connection" }, { old: "share", why: "connection" },
      { old: "money.move", why: "connection" }, { old: "approval_grants", why: "connection" },
      { old: "operation_kinds", why: "connection" }, { old: "shell.egress", why: "no_act" },
      { old: "app.write", why: "no_act" }, ...DEFAULT_LEFT,
    ]);
  });

  it("takes egress `ask` as the list it was, and a legacy document's egress as open", () => {
    const ask = carried(old([], { egress: { mode: "ask", destinations: [
      { host: "api.example.com", port: 443 }, { host: "93.184.216.34", port: 443 }] } }));
    expect([ask.document.egress, ask.egressAsk]).toEqual([
      { mode: "allowlist", destinations: [{ host: "api.example.com", port: 443 }] }, true]);
    expect(ask.notCarried).toEqual([...DEFAULT_LEFT, { old: "93.184.216.34:443", why: "host" }]);
    expect(carried(JSON.stringify({ entries: [], spend: SPEND })).document.egress)
      .toEqual({ mode: "open", destinations: [] });
  });

  it("refuses an owner with any spend cap, or a document not in the old shape", () => {
    for (const spend of [{ daily: 100 }, { ask_above: 0 }, { source_ceilings: { c1: 5 } }]) {
      expect(mapPolicy(old([], { spend: { ...SPEND, ...spend } })))
        .toEqual({ problem: "policy_spend" });
    }
    for (const text of ["{", old([], { egress: { mode: "everywhere", destinations: [] } }),
                        old([rule("teleport", "do")]), old([], { extra: 1 }),
                        old([], { spend: { ...SPEND, source_ceilings: null } }),
                        old([{ ...rule("share", "do"), extra: 1 }]),
                        old([], { egress: { mode: "open", destinations: [], extra: 1 } })]) {
      expect(mapPolicy(text)).toEqual({ problem: "policy_unreadable" });
    }
  });

  it("holds the old tables to the Python's own", () => {
    const python = (file: string) => readFileSync(join(REPO, "tinyassets", "broker", file), "utf8");
    const pairs = /DEFAULT_RULES = \{([^}]*)\}/.exec(python("policy.py"))![1]!;
    expect(Object.fromEntries([...pairs.matchAll(/"([a-z.]+)": "([a-z_]+)"/g)]
      .map((m) => [m[1], m[2]]))).toEqual(DEFAULTS);
    expect(Object.keys(DEFAULTS)).toEqual([...pairs.matchAll(/"([a-z.]+)":/g)].map((m) => m[1]));
    expect(/BEHAVIOURS = \(([^)]*)\)/.exec(python("policy.py"))![1]!.match(/[a-z_]+/g))
      .toEqual(BEHAVIOURS);
    expect(/STRICTEST_AGENT = frozenset\(\{([^}]*)\}\)/.exec(python("authority_gate.py"))![1]!
      .match(/[a-z.]+/g)).toEqual(STRICTEST);
  });
});

describe("what the owner is told", () => {
  it("gives one notice within the notice's bound, the archive named only when there is one", () => {
    const kept = noticeOf(true);
    const none = noticeOf(false);
    expect([...kept].length).toBeLessThanOrEqual(NOTICE_DETAIL);
    expect(kept).toContain(`${IMPORTED}/policy.json`);
    expect(none).not.toContain("policy.json");
    expect(kept.replace(/ Your old rule list is kept, unchanged, in \S+\./, "")).toBe(none);
    expect(kept).not.toMatch(/\{[a-z]+\}/);
  });

  it("lists in the README each rule that came across in the act's words, and what did not", () => {
    const lines = readmeLines(carried(old([rule("workspace.shell", "hand_off", { agent: "w" })],
                                          { egress: { mode: "ask", destinations: [] } })), true);
    expect(lines).toContain("- Leave to you: Running a shell command in the sandbox, its network " +
                            "included. (agent `w` only)");
    expect(lines).toContain("- Ask you first: A message to another person or party.");
    expect(lines).toContain("`policy.json` is your old rule list, unchanged.");
    expect(lines.join("\n")).toContain("It is now your list alone");
    expect(lines).toContain("- `commons.publish`: the new platform has no such action");
    expect(readmeLines(carried(null), false)).not.toContain(
      "`policy.json` is your old rule list, unchanged.");
  });
});
