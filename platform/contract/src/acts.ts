/**
 * The kinds of action an owner's `actions` rule names, as data (`acts.json`; d3 §N,
 * `r5_research.md` §5): each act's own CLI tool kinds (`hook`), and the ways it can be done
 * that nothing of ours sees (`unseen`). The doors that do an act name it in their own row
 * (`doors.json` `act`, `doors.ts`), so a door added with its act lands in one place. The
 * policy's validator (`policy-document.ts`) refuses a rule on an act no door does, no hook
 * kind is and no tool of the rule names: nothing could decide it.
 *
 * The module keeps no state and does no work at load (ADR-032). The whole file is held by its
 * guard (`guards/acts.test.ts`), and each act is held again, with the file's tables, when it
 * is read: an act missing its words, or naming a kind, a condition or an unseen way the file
 * does not define, fails loudly at its first read.
 */
import raw from "../acts.json" with { type: "json" };

import { object as plain } from "./checks";

/** A CLI's own tool kind whose every call is the act (`kinds`). */
export type HookKind = keyof typeof raw.kinds;
/** When a door's call is its act (`conditions`): every call when its row names none. */
export type Condition = keyof typeof raw.conditions;
/** A way an act can be done that nothing of ours sees (`unseen`). */
export type Unseen = keyof typeof raw.unseen;

export interface Act {
  readonly says: string;
  readonly hook: readonly HookKind[];
  readonly unseen: readonly Unseen[];
}

const ACT_KEYS = "hook,says,unseen";
const FILE_KEYS = "about,acts,conditions,kinds,unseen";
/** An act's name: dotted lowercase words. */
const ACT_NAME = /^[a-z]+(?:\.[a-z]+)*$/;

/** Distinct names, each one of `known`'s keys. */
function namesOf(value: unknown, known: Record<string, unknown>): boolean {
  return Array.isArray(value) && new Set(value).size === value.length
    && value.every((name) => typeof name === "string" && Object.hasOwn(known, name));
}

/** Each way `file` breaks the acts file's rules, as `act: what`. */
export function actProblems(file: unknown): string[] {
  if (!plain(file) || Object.keys(file).sort().join() !== FILE_KEYS
      || !["acts", "conditions", "kinds", "unseen"].every((key) => plain(file[key]))) {
    return ["the file is its about, acts, conditions, kinds and unseen ways"];
  }
  const problems: string[] = [];
  for (const table of ["conditions", "kinds", "unseen"]) {
    for (const [name, says] of Object.entries(file[table] as Record<string, unknown>)) {
      if (!/^[a-z][a-z_]{0,31}$/.test(name) || typeof says !== "string" || says === "") {
        problems.push(`${table} ${name}: a name and its words`);
      }
    }
  }
  const kinds = file["kinds"] as Record<string, unknown>;
  const unseen = file["unseen"] as Record<string, unknown>;
  for (const [name, act] of Object.entries(file["acts"] as Record<string, unknown>)) {
    const say = (what: string) => problems.push(`${name}: ${what}`);
    if (!ACT_NAME.test(name)) {
      say("not an act's name");
    }
    if (!plain(act) || Object.keys(act).sort().join() !== ACT_KEYS) {
      say("an act is its words, its hook kinds and its unseen ways");
      continue;
    }
    if (typeof act["says"] !== "string" || act["says"] === "") {
      say("no words");
    }
    if (!namesOf(act["hook"], kinds)) {
      say("hook kinds the file does not define, or one twice");
    }
    if (!namesOf(act["unseen"], unseen)) {
      say("unseen ways the file does not define, or one twice");
    }
  }
  return problems;
}

/**
 * The act named `name`, held whole and frozen at its read, so no module can change one for
 * the isolate; or undefined for a name the file does not hold.
 */
export function actNamed(name: unknown): Act | undefined {
  const found = Object.entries(raw.acts as Record<string, Act>).find(([key]) => key === name);
  if (found === undefined) {
    return undefined;
  }
  const act = found[1];
  const problems = actProblems({ ...raw, acts: Object.fromEntries([found]) });
  if (problems.length > 0) {
    throw new Error(`the acts file is broken: ${problems.join("; ")}`);
  }
  Object.freeze(act.hook);
  Object.freeze(act.unseen);
  return Object.freeze(act);
}

/** Whether `name` is a condition the file defines. */
export function isCondition(name: unknown): name is Condition {
  return typeof name === "string" && Object.hasOwn(raw.conditions, name);
}
