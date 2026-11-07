/**
 * The vendor's own sign-in, as data (slice SIGNIN; ADR-031 decision 2, ADR-034): each record's
 * `cli.login` names the command (`argv`, `{bin}` and `{region}` filled), what of its output
 * the owner sees (`url_pattern`, `code_pattern`: each pattern's first group, and nothing else
 * of the output, then held to the record's pinned page, `signinShape`), whether it takes a
 * pasted code on its stdin (`accepts_code`), and how it is known done (its exit, or
 * `state_checks` met when `complete_on_state`). Every login runs on a terminal of its own. No
 * vendor's name is in this module.
 *
 * What is relayed is the vendor's own prompt, a URL to open in the owner's browser or a code
 * to type there, never a token: a value holding a secret file's atom, or credential-shaped
 * (`credentialLike`, checked again on the trusted side), is refused, and the sign-in ends.
 */
import type { LoginSpec } from "../../sources/src/schema";
import { credentialLike } from "../../sources/src/signin";

import { heldSecret } from "./guards";

/** What a login's output showed the owner so far. */
export interface Prompt {
  readonly url: string | null;
  readonly code: string | null;
}

/** A terminal's escape sequences and carriage returns taken out of its text. */
export function plain(text: string): string {
  // CSI and OSC sequences, then any lone escape and its next character; carriage returns.
  return text.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, "")
    .replace(/\u001b./g, "").replace(/\r/g, "");
}

/** The record's patterns' first groups in `text`, as far as each has matched. */
export function prompted(login: LoginSpec, text: string, before: Prompt): Prompt {
  const first = (pattern: string | undefined): string | null =>
    pattern === undefined ? null : new RegExp(pattern).exec(text)?.[1] ?? null;
  return { url: before.url ?? first(login.url_pattern),
           code: before.code ?? first(login.code_pattern) };
}

/**
 * Whether `value` may not be relayed: it holds a secret file's atom (`atoms`, from the
 * engines' stores, which only the VM holds) or is credential-shaped (`credentialLike`).
 */
export function credentialShaped(value: string, atoms: readonly string[]): boolean {
  return heldSecret(value, atoms) !== null || credentialLike(value);
}

/** The login's argv: `{bin}` the engine's binary, `{region}` the owner's choice. */
export function loginArgv(login: LoginSpec, bin: string, region: string | null): string[] {
  const { argv } = login;
  if (argv.some((arg) => arg.includes("{region}")) && region === null) {
    throw new Error("this sign-in takes the owner's choice of region");
  }
  return argv.map((arg) => arg.replaceAll("{bin}", bin).replaceAll("{region}", region ?? ""));
}

/** One argument for a POSIX shell, single-quoted. */
function quoted(arg: string): string {
  return `'${arg.replaceAll("'", "'\\''")}'`;
}

/**
 * A command run on a terminal of its own (every sign-in: some CLIs prompt only on one):
 * util-linux's `script`, its typescript discarded, its exit the command's.
 */
export function onTerminal(argv: readonly string[]): string[] {
  return ["script", "-qfec", argv.map(quoted).join(" "), "/dev/null"];
}

/** A dotted path's value in a parsed JSON document, or undefined. */
function at(document: unknown, dotted: string): unknown {
  let value = document;
  for (const key of dotted.split(".")) {
    if (typeof value !== "object" || value === null || !(key in value)) {
      return undefined;
    }
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

/**
 * Whether the record's `state_checks` hold for files read by `read` (a path with `{home}`
 * filled; null when absent): each file present as JSON with every named field a non-empty
 * value. No checks, false.
 */
export function statesHold(login: LoginSpec, read: (path: string) => string | null): boolean {
  const checks = login.state_checks;
  if (checks === undefined || checks.length === 0) {
    return false;
  }
  return checks.every((check) => {
    const text = read(check.path);
    if (text === null) {
      return false;
    }
    try {
      const document: unknown = JSON.parse(text);
      return check.present.every((field) => {
        const value = at(document, field);
        return value !== undefined && value !== null && value !== "";
      });
    } catch {
      return false;
    }
  });
}
