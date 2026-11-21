/**
 * The old stack's owner policy mapped to the new rules (d3 §N7; `r5_research.md` §5.6, §7;
 * `docs/decisions/untangle-2026-11/r5-import.md`), and what the owner is told of it
 * (`notice.json`, the founder's words).
 *
 * The old document is `{entries, spend, egress}` (`broker/policy.py` `_validate`). Its rules
 * decided by strictest-wins, an act no rule covered taking the old default (`DEFAULTS`), and
 * the main agent's rules deciding every agent's call, another agent's only its own
 * (`broker/authority_gate.py` `_agents`), across every agent for the strictest acts. So each
 * act's rule is what the main agent's rules or the defaults made it, for every agent and
 * outside client; and an agent the owner configured gets a rule of its own where its rules,
 * or the defaults, made the act stricter for it. `do` writes nothing (absent is allowed),
 * `do_if_preapproved` and `ask_first` write `ask`, `hand_off` writes `never`. An owner who never
 * set a rule, or had no document at all, gets the old defaults written as its own settings,
 * so nothing it relied on loosens; new owners start empty (Fact 15).
 *
 * Not carried, each listed in the README with why: a rule on an act the new platform has no
 * name for (`commons.publish`, `app.read`, `app.write`, `shell.egress`), or one nothing sees
 * until the owner names its tools (`browser.action`, `spend`, `money.move`); everything keyed
 * to the old app connections (a rule naming a connection or an operation, a standing grant,
 * the declared operations, the review settings), since no connection carries; and an egress
 * destination the new validator refuses (an address). Egress `ask` never asked: it refused
 * what was off the list (`broker/egress.py`), so it is `allowlist`. A legacy document with no
 * egress takes the old provisioning upgrade's `open`.
 *
 * Refused loudly, as the owner's problem: a document not in the old shape
 * (`policy_unreadable`), and any spend cap (`policy_spend`): the old caps are minor units of
 * no named currency, and with any set the old stack refused every metered model call, so a
 * mapping into the `models` ceilings is built from the census's numbers, if it finds any.
 *
 * The old document itself is kept verbatim (`build.ts` `POLICY_ARCHIVE`). Deleted at the
 * cutover with the importer.
 */
import { actNamed } from "../contract/src/acts";
import { keyed, object } from "../contract/src/checks";
import {
  type ActionRule, checkDestination, type PolicyDocument, seenUnnamed, validate,
} from "../contract/src/policy-document";
import { IMPORTED } from "../contract/src/paths";
import { Refusal } from "../contract/src/refusal";
import { AGENT_ID } from "../contract/src/text";

import words from "./notice.json" with { type: "json" };

/** The old behaviours, laxest first (`broker/policy.py` `BEHAVIOURS`). */
export const BEHAVIOURS = Object.freeze(
  ["do", "do_if_preapproved", "ask_first", "hand_off"] as const);
type Behaviour = (typeof BEHAVIOURS)[number];

/** The old defaults (`broker/policy.py` `DEFAULT_RULES`), in its order. */
export const DEFAULTS: Readonly<Record<string, Behaviour>> = Object.freeze({
  "workspace.files": "do", "workspace.shell": "do", "workspace.workflows": "do",
  "shell.egress": "do", "app.read": "do", "app.write": "do",
  "people.message": "ask_first", "commons.publish": "ask_first", "share": "ask_first",
  "browser.action": "ask_first", "spend": "ask_first", "money.move": "hand_off",
  "security.change": "hand_off", "access.grant": "hand_off",
});

/** The acts every configured agent's rules decide (`authority_gate.py` `STRICTEST_AGENT`). */
export const STRICTEST = Object.freeze(["security.change", "access.grant", "money.move"]);
/** The old agent a rule names when it names none (`authority_gate.py` `MAIN_AGENT`). */
const MAIN = "main";
/** The old tables (`broker/policy.py` `TABLES`), and those naming agents (`_agents`). */
const TABLES = Object.freeze(["rules", "approval_grants", "operation_kinds", "review_on",
                              "review_off"]);
const CONFIGURING = Object.freeze(["rules", "review_on", "approval_grants"]);
const SPEND = Object.freeze(["daily", "monthly", "transaction", "ask_above", "source_ceilings"]);
/** The document the old stack gave an owner it bootstrapped (`PolicyStore`): no rule set. */
const OLD_BOOTSTRAP = JSON.stringify({ entries: [], egress: { mode: "open", destinations: [] },
                                       spend: { daily: null, monthly: null, transaction: null,
                                                ask_above: null, source_ceilings: {} } });

export type Why = keyof typeof words.readme.why;

/** Something of the old document that did not come across, and why. */
export interface NotCarried {
  readonly old: string;
  readonly why: Why;
}

/** An old document mapped, or the problem that refuses its owner. */
export type Mapped =
  | { readonly document: PolicyDocument; readonly notCarried: readonly NotCarried[];
      readonly egressAsk: boolean; readonly entries: number }
  | { readonly problem: "policy_unreadable" | "policy_spend" };
/** An old document that maps. */
export type Carried = Extract<Mapped, { readonly document: PolicyDocument }>;

interface Entry {
  readonly table: string;
  readonly values: Record<string, unknown>;
}

interface Old {
  readonly entries: readonly Entry[];
  readonly spend: Record<string, unknown>;
  readonly egress: { readonly mode: string;
                     readonly destinations: readonly { host: string; port: number }[] };
}

/**
 * The old shape, its key sets as `_validate` held them, as much of the rest as the mapping
 * reads; null when it is not that shape.
 */
function oldShape(document: unknown): Old | null {
  if (!keyed(document, ["entries", "spend"], ["egress"]) || !Array.isArray(document["entries"])
      || !keyed(document["spend"], SPEND) || !object(document["spend"]["source_ceilings"])) {
    return null;
  }
  const egress = document["egress"] ?? { mode: "open", destinations: [] };
  if (!keyed(egress, ["mode", "destinations"])
      || !["open", "allowlist", "ask"].includes(egress["mode"] as string)
      || !Array.isArray(egress["destinations"])
      || !egress["destinations"].every((entry) => keyed(entry, ["host", "port"])
        && typeof entry["host"] === "string" && typeof entry["port"] === "number")) {
    return null;
  }
  const entries = document["entries"] as unknown[];
  if (!entries.every((entry) => keyed(entry, ["scope", "table", "values"])
      && entry["scope"] === "account"
      && TABLES.includes(entry["table"] as string) && object(entry["values"])
      && (entry["table"] !== "rules"
          || (BEHAVIOURS.includes(entry["values"]["behaviour"] as Behaviour)
              && Object.hasOwn(DEFAULTS, entry["values"]["action_class"] as string))))) {
    return null;
  }
  return { entries: entries as Entry[], spend: document["spend"], egress: egress as Old["egress"] };
}

const rank = (behaviour: Behaviour): number => BEHAVIOURS.indexOf(behaviour);
const stricter = (a: Behaviour, b: Behaviour): Behaviour => rank(a) >= rank(b) ? a : b;
const agentOf = (entry: Entry): string => String(entry.values["agent"] ?? MAIN);

/**
 * The old document (its stored text), or none, mapped to the new rules. Each written rule is
 * held to the platform's own validator, so a mapping it would refuse fails loudly here.
 */
export function mapPolicy(text: string | null): Mapped {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text ?? OLD_BOOTSTRAP);
  } catch {
    return { problem: "policy_unreadable" };
  }
  const old = oldShape(parsed);
  if (old === null) {
    return { problem: "policy_unreadable" };
  }
  if (SPEND.some((key) => key === "source_ceilings"
    ? Object.keys(old.spend[key] as object).length > 0 : old.spend[key] !== null)) {
    return { problem: "policy_spend" };
  }
  const notCarried: NotCarried[] = [];
  const drop = (item: NotCarried) => {
    if (!notCarried.some((had) => had.old === item.old && had.why === item.why)) {
      notCarried.push(item);
    }
  };
  // A rule naming a connection or an operation, or any record but a behaviour, was the old
  // connections' alone; so was every other table.
  const general = old.entries.filter((entry) => {
    const values = entry.values;
    const kept = entry.table === "rules" && (values["record_kind"] ?? "behavior") === "behavior"
      && !values["connection"] && !values["operation"];
    if (!kept) {
      drop({ old: entry.table === "rules" ? String(values["action_class"]) : entry.table,
             why: "connection" });
    }
    return kept;
  });
  const configured = [...new Set([MAIN, ...old.entries
    .filter((entry) => CONFIGURING.includes(entry.table)).map(agentOf)])].sort();
  const effective = (agent: string, act: string): Behaviour => general
    .filter((entry) => entry.values["action_class"] === act && agentOf(entry) === agent)
    .map((entry) => entry.values["behaviour"] as Behaviour)
    .reduce<Behaviour | null>((a, b) => a === null ? b : stricter(a, b), null) ?? DEFAULTS[act]!;
  // An agent the new grammar cannot name holds for every agent: stricter, never laxer.
  const named = configured.filter((agent) => agent !== MAIN && AGENT_ID.test(agent));
  const actions: ActionRule[] = [];
  for (const act of Object.keys(DEFAULTS)) {
    let every = effective(MAIN, act);
    for (const agent of configured.filter((agent) => agent !== MAIN)) {
      if (STRICTEST.includes(act) || !named.includes(agent)) {
        every = stricter(every, effective(agent, act));
      }
    }
    const rules: { agent?: string; behaviour: Behaviour }[] = [{ behaviour: every }];
    if (!STRICTEST.includes(act)) {
      rules.push(...named.filter((agent) => rank(effective(agent, act)) > rank(every))
        .map((agent) => ({ agent, behaviour: effective(agent, act) })));
    }
    for (const rule of rules.filter((r) => r.behaviour !== "do")) {
      if (actNamed(act) === undefined) {
        drop({ old: act, why: "no_act" });
      } else if (!seenUnnamed(act)) {
        drop({ old: act, why: "unseen" });
      } else {
        actions.push({ act, do: rule.behaviour === "hand_off" ? "never" : "ask",
                       ...(rule.agent === undefined ? {} : { agent: rule.agent }) });
      }
    }
  }
  const destinations: { host: string; port: number }[] = [];
  for (const entry of old.egress.destinations) {
    try {
      checkDestination(entry);
      destinations.push(entry);
    } catch (error) {
      if (!(error instanceof Refusal)) {
        throw error;
      }
      drop({ old: `${entry.host}:${entry.port}`, why: "host" });
    }
  }
  const document = validate({
    egress: { mode: old.egress.mode === "open" ? "open" : "allowlist", destinations },
    ...(actions.length === 0 ? {} : { actions }),
  });
  return { document, notCarried, egressAsk: old.egress.mode === "ask",
           entries: old.entries.length };
}

/** `text` with each `{name}` filled from `values`; a name with no value fails loudly. */
function fill(text: string, values: Readonly<Record<string, string>>): string {
  return text.replace(/\{([a-z]+)\}/g, (_, name: string) => {
    if (!Object.hasOwn(values, name)) {
      throw new Error(`notice.json names {${name}}, which nothing fills here`);
    }
    return values[name]!;
  });
}

/** The owner's one notice of the move, the `kept` part only when it had an old rule list. */
export function noticeOf(kept: boolean): string {
  const parts = words.notice;
  return [parts.files, parts.rules, ...(kept ? [parts.kept] : []), parts.readme]
    .map((part) => fill(part, { imported: IMPORTED })).join(" ");
}

/** The README's section on the owner's rules: the archive, what came across, and what did not. */
export function readmeLines(mapped: Carried, kept: boolean): string[] {
  const say = words.readme;
  const lines = [say.heading, ""];
  if (kept) {
    lines.push(say.kept, "");
  }
  const actions = mapped.document.actions ?? [];
  if (actions.length > 0) {
    lines.push(say.written, "", ...actions.map((rule) =>
      fill(say[rule.do], { act: actNamed(rule.act)!.says })
      + (rule.agent === undefined ? "" : fill(say.agent, { agent: rule.agent }))), "");
  }
  if (mapped.egressAsk) {
    lines.push(say.egress_ask, "");
  }
  if (mapped.notCarried.length > 0) {
    lines.push(say.not_carried, "", ...mapped.notCarried.map((item) =>
      fill(say.item, { old: item.old, why: say.why[item.why] })), "");
  }
  return lines;
}
