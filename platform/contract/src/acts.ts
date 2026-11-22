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

import { keyed, object } from "./checks.ts";

/** A CLI's own tool kind whose every call is the act (`kinds`). */
export type HookKind = keyof typeof raw.kinds;
/**
 * The kind a command the agent's own definition starts is (`mcp.json`'s server, `hooks.json`'s
 * hook): one the run starts with no tool call, decided at the run's admission.
 */
export const COMMAND_KIND: HookKind = "shell";
/**
 * A tool a rule or the agent's own hook names beside the kinds: an MCP server's and its tool's
 * names (`mcp:<server>:<tool>`), `*` matching any run of them.
 */
export const TOOL_MATCHER = /^mcp:[A-Za-z0-9_*-]{1,64}:[A-Za-z0-9_.*-]{1,128}$/;
/** When a door's call is its act (`conditions`): every call when its row names none. */
export type Condition = keyof typeof raw.conditions;
/** A way an act can be done that nothing of ours sees (`unseen`). */
export type Unseen = keyof typeof raw.unseen;

export interface Act {
  readonly says: string;
  readonly hook: readonly HookKind[];
  readonly unseen: readonly Unseen[];
}

const ACT_KEYS = Object.freeze(["says", "hook", "unseen"]);
const FILE_KEYS = Object.freeze(["about", "acts", "conditions", "kinds", "unseen"]);
/** An act's name: dotted lowercase words. */
const ACT_NAME = /^[a-z]+(?:\.[a-z]+)*$/;

/** Distinct names, each one of `known`'s keys. */
function namesOf(value: unknown, known: Record<string, unknown>): boolean {
  return Array.isArray(value) && new Set(value).size === value.length
    && value.every((name) => typeof name === "string" && Object.hasOwn(known, name));
}

/** Each way `file` breaks the acts file's rules, as `act: what`. */
export function actProblems(file: unknown): string[] {
  if (!keyed(file, FILE_KEYS)
      || !["acts", "conditions", "kinds", "unseen"].every((key) => object(file[key]))) {
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
    if (!keyed(act, ACT_KEYS)) {
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

/** Whether `name` is a CLI tool kind the file defines. */
export function isHookKind(name: unknown): name is HookKind {
  return typeof name === "string" && Object.hasOwn(raw.kinds, name);
}

/**
 * How a CLI names its tools, as its record's `decide` block has it: its own names of the tools
 * that are each kind, and an MCP server's tool's name (`prefix`, the server, `join`, the tool).
 */
export interface ToolNames {
  readonly kinds: Readonly<Partial<Record<HookKind, readonly string[]>>>;
  readonly mcp: { readonly prefix: string; readonly join: string };
}

/**
 * A pattern of a name part a `TOOL_MATCHER` writes: each `*` any run of characters, each
 * character but a letter, a digit or `_` any one (a CLI rewrites those in its own names, each
 * its own way), the rest itself. So the pattern holds every name a CLI may give the tool,
 * never fewer.
 */
function partPattern(glob: string): string {
  return [...glob].map((char) => char === "*" ? ".*" : /[A-Za-z0-9_]/.test(char) ? char : ".")
    .join("");
}

/**
 * The patterns of a CLI's own tool names (`names`) that are one of `kinds` or match one of
 * `tools` (`TOOL_MATCHER`s): an owner's rule's, which its decide hook matches, and an agent's
 * own hook's, which its dispatcher matches, by this one rule. A kind the CLI lacks adds none.
 */
export function toolPatterns(names: ToolNames, kinds: readonly HookKind[],
                             tools: readonly string[]): string[] {
  return [...kinds.flatMap((kind) => (names.kinds[kind] ?? [])
    .map((name) => name.replaceAll(".", "\\."))),
          ...tools.map((matcher) => {
    const [, server, tool] = matcher.split(":") as [string, string, string];
    return `${names.mcp.prefix}${partPattern(server)}${names.mcp.join}${partPattern(tool)}`;
  })];
}
