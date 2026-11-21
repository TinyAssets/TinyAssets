/**
 * The hook helper (R5 P-hooks; d3 §N3, `r5_research.md` §5.3): `ta-runner decide`, which a
 * run's CLI's own pre-tool hook runs, as the run's user, on each tool call it matches (the
 * record's `decide` block, `sources/src/schema.ts`). It reads the call as every CLI hands its
 * hook one (`tool_name`, `tool_input`, on stdin), asks the run's decide door (`runDecide`, by
 * the URL its CLI's environment holds, `DECIDE_ENV`) and answers in the one shape each CLI
 * reads: let it be, exit 0 and `{}`; refused or held for the owner, exit 2 and the door's
 * reason on stderr, which the CLI hands the model. Anything that fails it (no door, no
 * answer within `DECIDE_MS`, an answer it cannot read) is refused the same way, so the hook
 * itself never lets a call through. `decide check` asks the door which tools the hook
 * matches instead and prints them: the runner's start check, and its ask before it writes
 * the hook (`hookTools`).
 */
import type { DoorAnswer } from "../../contract/src/owner";

import { RUNNERS } from "./protocol";

/** The variable a run's CLI's environment carries its decide door's URL in. */
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

/** The door's answer, or why there is none: its refusal's message, or what failed. */
async function ask(url: string, body: unknown):
    Promise<{ answer: Answer | null } | { reason: string }> {
  let response: Response;
  try {
    response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" },
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

/** The patterns of the CLI's tool names its hook matches for the owner's rules. */
export async function hookTools(url: string): Promise<string[]> {
  const asked = await ask(url, { check: true });
  const tools = "answer" in asked ? asked.answer?.tools : null;
  if (!Array.isArray(tools) || !tools.every((tool) => typeof tool === "string")) {
    throw new Error("reason" in asked ? asked.reason : "the decide door's check is unreadable");
  }
  return tools as string[];
}

/** The pattern a hook's matcher holds: exactly one of `tools`. */
export function matcherOf(tools: readonly string[]): string {
  return `^(?:${tools.join("|")})$`;
}

/**
 * The helper's argv: the runner of `digest`, as the loader installed it, by the Node the
 * loader runs it with (this process's), Node's own warnings off, so the reason a refusal
 * writes on stderr is the door's alone.
 */
export function helperArgv(digest: string): string[] {
  return [process.execPath, "--no-warnings", `${RUNNERS}/${digest}/runner.mjs`, "decide"];
}

/** The helper's command line, as a CLI's hook runs it. */
export function helperCommand(digest: string): string {
  return helperArgv(digest).join(" ");
}

/** The command: `decide` (a hook's call on `stdin`) or `decide check`; its exit code. */
export async function decideCommand(args: readonly string[],
                                    env: Readonly<Record<string, string | undefined>>,
                                    stdin: AsyncIterable<Buffer | string>,
                                    out: { write(text: string): unknown },
                                    err: { write(text: string): unknown }): Promise<number> {
  const refuse = (reason: string) => {
    err.write(`${reason}\n`);
    return REFUSE;
  };
  const url = env[DECIDE_ENV];
  if (url === undefined || url === "") {
    return refuse("the owner's rules could not be checked: this CLI has no decide door");
  }
  if (args[0] === "check") {
    try {
      out.write(`${JSON.stringify({ tools: await hookTools(url) })}\n`);
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
  const asked = await ask(url, { tool_name: call.tool_name, tool_input: call.tool_input });
  if ("reason" in asked) {
    return refuse(asked.reason);
  }
  if (asked.answer?.allow !== true) {
    return refuse("the owner's rules could not be checked: the decide door's answer is unreadable");
  }
  out.write("{}\n");
  return ALLOW;
}
