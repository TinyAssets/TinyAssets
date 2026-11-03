/**
 * The vendor's own sign-in, as data (slice SIGNIN; ADR-031 decision 2, ADR-034): each record's
 * `cli.login` names the command (`argv`, `{bin}` and `{region}` filled), what of its output
 * the owner sees (`url_pattern`, `code_pattern`: each pattern's first group, and nothing else
 * of the output), whether it takes a pasted code on its stdin (`accepts_code`), whether it
 * needs a terminal (`terminal`), and how it is known done (its exit, or `state_checks` met
 * when `complete_on_state`). No vendor's name is in this module.
 *
 * What is relayed is the vendor's own prompt, a URL to open in the owner's browser or a code
 * to type there, never a token: a value holding a secret file's atom, a JSON Web Token, or a
 * URL parameter named like a credential is refused, and the sign-in ends.
 */
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
export function prompted(login: Readonly<Record<string, unknown>>, text: string,
                         before: Prompt): Prompt {
  const first = (key: string): string | null => {
    const pattern = login[key];
    if (typeof pattern !== "string") {
      return null;
    }
    const found = new RegExp(pattern).exec(text);
    return found?.[1] ?? null;
  };
  return { url: before.url ?? first("url_pattern"), code: before.code ?? first("code_pattern") };
}

/** A URL parameter or fragment key named like a credential. */
const CREDENTIAL_PARAM = /(?:^|[_-])(?:access_token|refresh_token|id_token|token|secret|password|passwd|api_?key)$/i;
/** A JSON Web Token: three base64url segments, the first a JSON object's (`eyJ`). */
const JWT = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/;

/**
 * Why `value` may not be relayed, or null: it holds a secret file's atom (`atoms`, from the
 * engines' stores), a JSON Web Token, or, as a URL, a parameter named like a credential.
 */
export function credentialShaped(value: string, atoms: readonly string[]): string | null {
  if (heldSecret(value, atoms) !== null) {
    return "a secret file's value";
  }
  if (JWT.test(value)) {
    return "a token";
  }
  let url: URL | null = null;
  try {
    url = /^https?:\/\//.test(value) ? new URL(value) : null;
  } catch {
    url = null;
  }
  if (url !== null) {
    const keys = [...url.searchParams.keys(),
                  ...new URLSearchParams(url.hash.replace(/^#/, "")).keys()];
    if (keys.some((key) => CREDENTIAL_PARAM.test(key))) {
      return "a credential in the URL";
    }
  }
  return null;
}

/** The login's argv: `{bin}` the engine's binary, `{region}` the owner's choice. */
export function loginArgv(login: Readonly<Record<string, unknown>>, bin: string,
                          region: string | null): string[] {
  const argv = login["argv"] as string[];
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
 * A command run on a terminal of its own (`terminal` sign-ins: the CLI prints its prompt
 * only to a terminal): util-linux's `script`, its typescript discarded, its exit the
 * command's.
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
export function statesHold(login: Readonly<Record<string, unknown>>,
                           read: (path: string) => string | null): boolean {
  const checks = login["state_checks"] as { path: string; present: string[] }[] | undefined;
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
