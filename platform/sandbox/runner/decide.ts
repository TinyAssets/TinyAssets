/**
 * The hook helper (R5 P-hooks; d3 §N3, `r5_research.md` §5.3): `ta-runner decide <door>`,
 * which a run's CLI's own pre-tool hook runs, as the run's user, on each tool call it matches
 * (the record's `decide` block, `sources/src/schema.ts`), where the CLI runs a command rather
 * than posting the call itself. It reads the call as every CLI hands its hook one
 * (`tool_name`, `tool_input`, on stdin), asks the runs' decide door (`runDecide`) and answers
 * in the one shape each CLI reads: let it be, exit 0 and `{}`; refused or held for the owner,
 * exit 2 and the door's reason on stderr, which the CLI hands the model.
 *
 * The door's address is its argument, which only the root-owned hook file names, and must be
 * the decide door's; the run's credential (`DECIDE_ENV`, `<run>:<secret>`) is its
 * environment's, so a value the agent forged names a run the door refuses, or none. Anything
 * that fails it (no credential, no answer within `DECIDE_MS`, an answer it cannot read) is
 * refused the same way, so the hook itself never lets a call through. `decide <door> check`
 * asks which tools the hook matches instead and prints them: the runner's start check.
 */
import { DECIDE_HEADER, decideCredential, egressDoor } from "../../contract/src/egress";
import type { DoorAnswer } from "../../contract/src/owner";

import { RUNNERS } from "./protocol";

/** The variable a run's CLI's environment carries its decide credential in. */
export const DECIDE_ENV = "TA_DECIDE";
/**
 * How long the helper waits for the door: a third of the 60 s each record's hook allows it,
 * so the helper refuses before the CLI times its hook out (which some CLIs let through).
 */
export const DECIDE_MS = 20_000;
const ALLOW = 0;
const REFUSE = 2;

/** Each field the door answers (`runDecide`), as read: anything, until it is held. */
type Fields<A> = A extends object ? keyof A : never;
type Answer = Partial<Record<Fields<DoorAnswer<"runDecide">>, unknown>>;

/** Whether `url` is the decide door: http(s), its path the door's, nothing after it. */
function isDoor(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === "https:" || parsed.protocol === "http:")
      && egressDoor(parsed.pathname)?.door === "decide" && parsed.search === ""
      && parsed.hash === "" && parsed.username === "" && parsed.password === "";
  } catch {
    return false;
  }
}

/** The door's answer, or why there is none: its refusal's message, or what failed. */
async function ask(url: string, credential: string, body: unknown):
    Promise<{ answer: Answer | null } | { reason: string }> {
  let response: Response;
  try {
    response = await fetch(url, { method: "POST",
                                  headers: { "content-type": "application/json",
                                             [DECIDE_HEADER]: credential },
                                  body: JSON.stringify(body),
                                  signal: AbortSignal.timeout(DECIDE_MS) });
  } catch (error) {
    return { reason: `the owner's rules could not be checked: ${String(error)}` };
  }
  let read: unknown;
  try {
    read = await response.json();
  } catch {
    read = null;
  }
  if (response.status === 200) {
    return { answer: typeof read === "object" ? read as Answer | null : null };
  }
  const message = (read as { error?: { message?: unknown } } | null)?.error?.message;
  return { reason: typeof message === "string" ? message
    : `the owner's rules could not be checked: status ${response.status}` };
}

/**
 * The patterns of the CLI's tool names its hook matches for the owner's rules, and the
 * policy generation they are of: the door's check.
 */
export async function hookTools(url: string, credential: string):
    Promise<{ tools: string[]; generation: number }> {
  const asked = await ask(url, credential, { check: true });
  const tools = "answer" in asked ? asked.answer?.tools : null;
  const generation = "answer" in asked ? asked.answer?.generation : null;
  if (!Array.isArray(tools) || !tools.every((tool) => typeof tool === "string")
      || typeof generation !== "number") {
    throw new Error("reason" in asked ? asked.reason : "the decide door's check is unreadable");
  }
  return { tools: tools as string[], generation };
}

/** The pattern a hook's matcher holds: exactly one of `tools`. */
export function matcherOf(tools: readonly string[]): string {
  return `^(?:${tools.join("|")})$`;
}

/**
 * The helper's argv: the runner of `digest`, as the loader installed it, by the Node the
 * loader runs it with (this process's), Node's own warnings off, so the reason a refusal
 * writes on stderr is the door's alone; the door its argument.
 */
export function helperArgv(digest: string, door: string): string[] {
  return [process.execPath, "--no-warnings", `${RUNNERS}/${digest}/runner.mjs`, "decide",
          door];
}

/**
 * The helper's command line, as a CLI's hook runs it through a shell: through `env -i`, so
 * Node starts with nothing of the CLI's environment but its CA and the run's credential.
 */
export function helperCommand(digest: string, door: string): string {
  return ["/usr/bin/env", "-i", "NODE_EXTRA_CA_CERTS=\"$NODE_EXTRA_CA_CERTS\"",
          `${DECIDE_ENV}="$${DECIDE_ENV}"`, ...helperArgv(digest, door)].join(" ");
}

/** The command: `decide <door>` (a hook's call on `stdin`) or `decide <door> check`. */
export async function decideCommand(args: readonly string[],
                                    env: Readonly<Record<string, string | undefined>>,
                                    stdin: AsyncIterable<Buffer | string>,
                                    out: { write(text: string): unknown },
                                    err: { write(text: string): unknown }): Promise<number> {
  const refuse = (reason: string) => {
    err.write(`${reason}\n`);
    return REFUSE;
  };
  const [door = "", mode] = args;
  if (!isDoor(door) || (mode !== undefined && mode !== "check") || args.length > 2) {
    return refuse("the owner's rules could not be checked: the hook names no decide door");
  }
  const credential = env[DECIDE_ENV] ?? null;
  if (decideCredential(credential) === null) {
    return refuse("the owner's rules could not be checked: this run has no decide credential");
  }
  if (mode === "check") {
    try {
      out.write(`${JSON.stringify(await hookTools(door, credential!))}\n`);
      return ALLOW;
    } catch (error) {
      return refuse(String(error instanceof Error ? error.message : error));
    }
  }
  let text = "";
  for await (const chunk of stdin) {
    text += String(chunk);
  }
  let call: { tool_name?: unknown; tool_input?: unknown };
  try {
    call = JSON.parse(text) as typeof call;
  } catch {
    return refuse("the owner's rules could not be checked: the hook's call is unreadable");
  }
  const asked = await ask(door, credential!,
                          { tool_name: call.tool_name, tool_input: call.tool_input });
  if ("reason" in asked) {
    return refuse(asked.reason);
  }
  if (asked.answer?.allow !== true) {
    return refuse("the owner's rules could not be checked: the decide door's answer is " +
                  "unreadable");
  }
  out.write("{}\n");
  return ALLOW;
}
