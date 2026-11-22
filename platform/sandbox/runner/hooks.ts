/**
 * The agent's own hooks' dispatcher (D5, `agent_definition_research.md` §4.2): the hook
 * program's `hooks <event>` (`hook-entry.ts`), which a run's CLI runs as the run's user from the root-owned hook file its record
 * names (`definition.hooks`, in the layer its decide hook is in), on each event that file
 * carries, through a shell's check that starts it only where the run has hooks on the event
 * (`engine.ts` `dispatcherCommand`). It knows its run by its user: the one system directory
 * root made for a run of its group (`withRunUser`, `ownRun`), so it reads no variable the agent
 * could set. There it reads the run's own hooks on the event (`hooksFile`, rendered at the
 * start from the agent's `hooks.json`, `runHooks`; none there, none on it), runs
 * each hook the event's call matches, in parallel, as the same user, in the agent's directory,
 * with the event as JSON on stdin (`HookInput`), and answers in the one shape every engine here
 * reads (`definition.hooks`):
 * - **block** (`pre_tool`, `prompt`): exit 2, each reason on stderr after its hook's label. A
 *   hook blocks by exit 2 (its stderr the reason) or by `{"block": "<reason>"}`; one that fails
 *   (cannot start, past its timeout, another exit, an answer that is no JSON object) blocks
 *   too, with why (Fact 8).
 * - **context** (`session_start`, `prompt`): exit 0, `hookSpecificOutput.additionalContext`
 *   under the engine's own name of the event (`hook_event_name`), from `{"context": "<text>"}`.
 * - **nothing**: exit 0 and no output, so the engine's own flow goes on; a hook that failed on
 *   an event that cannot block makes it exit 1, its reasons on stderr, which every engine shows
 *   as a hook's error.
 * Nothing a hook answers can allow a call or change one: the dispatcher writes only the shapes
 * above, so an `allow`, an `updatedInput` or any field but those two is dropped, and the decide
 * hook beside it (R5) decides every call on every engine, whatever each engine's precedence.
 *
 * `hooks check` is its start check: the run's file found and read, and each hook's program
 * found and executable, as the run's user, in the CLI's environment.
 */
import { accessSync, constants, lstatSync, readdirSync, readFileSync, statSync } from "node:fs";
import { delimiter, join, resolve } from "node:path";

import type { HookKind } from "../../contract/src/acts";
import { HOOK_EVENTS, type HookEvent, isHookEvent } from "../../contract/src/agent-definition";

import { hooksFile, type RunHook, type RunHooks } from "./engine";
import { spawnBounded } from "./session";

/**
 * The most bytes of one hook's stdout or stderr the dispatcher reads: it reads an answer whole
 * to parse it, and no engine takes more context than a fraction of this (Claude Code keeps
 * 10,000 characters of it). A hook past it has failed.
 */
export const HOOK_ANSWER_BYTES = 1024 * 1024;
const NOTHING = 0;
const FAILED = 1;
const BLOCK = 2;

/** What a hook reads on stdin. */
export interface HookInput {
  readonly event: HookEvent;
  readonly engine: string;
  readonly session_id: string | null;
  readonly tool?: { readonly kind: HookKind | null; readonly name: string;
                    readonly input: unknown };
  readonly prompt?: string;
}

/** What a hook came to: nothing, a block, context, or a failure, each with its words. */
type Outcome = { readonly none: true } | { readonly block: string }
  | { readonly context: string } | { readonly failed: string };

/**
 * `hook` run with `input` on stdin, as the dispatcher's user, in a process group of its own
 * (`spawnBounded`), stopped past its timeout or once its answer is past `HOOK_ANSWER_BYTES`.
 */
async function runOne(hook: RunHook, cwd: string,
                      env: Readonly<Record<string, string | undefined>>, input: string):
    Promise<{ code: number | null; out: string; err: string; why: string | null }> {
  let out = "";
  let err = "";
  let why: string | null = null;
  const ran = spawnBounded(hook.run, { cwd, env, stdio: ["pipe", "pipe", "pipe"] },
                           Number.POSITIVE_INFINITY);
  const over = (reason: string) => {
    why ??= reason;
    ran.stop();
  };
  const timer = setTimeout(() => over(`ran past its ${hook.timeout_s} s`), hook.timeout_s * 1000);
  const read = (chunk: Buffer, into: "out" | "err") => {
    if (into === "out") {
      out += chunk.toString("utf8");
    } else {
      err += chunk.toString("utf8");
    }
    if (out.length + err.length > HOOK_ANSWER_BYTES) {
      over(`answered past ${HOOK_ANSWER_BYTES} bytes`);
    }
  };
  ran.child.stdout!.on("data", (chunk: Buffer) => read(chunk, "out"));
  ran.child.stderr!.on("data", (chunk: Buffer) => read(chunk, "err"));
  ran.child.stdin!.on("error", () => {});  // a hook that reads none of its input closed it
  ran.child.stdin!.end(input);
  try {
    return { code: await ran.exit, out, err, why };
  } catch (error) {
    return { code: null, out, err, why: `could not start: ${(error as Error).message}` };
  } finally {
    clearTimeout(timer);
  }
}

/** What `hook`'s run came to on `event`, by the format's rule for what each event takes. */
function outcomeOf(event: HookEvent, ran: Awaited<ReturnType<typeof runOne>>): Outcome {
  const takes = HOOK_EVENTS[event];
  if (ran.why !== null) {
    return { failed: ran.why };
  }
  if (ran.code === BLOCK) {
    return takes.block ? { block: ran.err.trim() || "a hook blocked it" } : { none: true };
  }
  if (ran.code !== 0) {
    return { failed: `exited ${String(ran.code)}${ran.err.trim() === "" ? ""
      : `: ${ran.err.trim()}`}` };
  }
  if (ran.out.trim() === "") {
    return { none: true };
  }
  let answer: unknown;
  try {
    answer = JSON.parse(ran.out);
  } catch {
    answer = null;
  }
  if (typeof answer !== "object" || answer === null || Array.isArray(answer)) {
    return { failed: "answered no JSON object" };
  }
  const { block, context } = answer as { block?: unknown; context?: unknown };
  if (takes.block && typeof block === "string" && block.trim() !== "") {
    return { block: block.trim() };
  }
  return takes.context && typeof context === "string" && context !== "" ? { context }
    : { none: true };
}

/** The dispatcher's answer: its exit code, stdout and stderr. */
export interface Dispatched {
  readonly code: number;
  readonly out: string;
  readonly err: string;
}

/**
 * `event` dispatched: the run's hooks on it whose matcher the call's tool name matches, run at
 * once on the input the engine handed (`native`: `tool_name`, `tool_input`, `prompt`,
 * `session_id` and `hook_event_name`, as every engine here hands them), answered as the header
 * says.
 */
export async function dispatch(file: RunHooks, event: HookEvent, native: unknown,
                               env: Readonly<Record<string, string | undefined>>):
    Promise<Dispatched> {
  const call = (typeof native === "object" && native !== null ? native : {}) as
    Record<string, unknown>;
  const text = (value: unknown) => typeof value === "string" ? value : null;
  const name = text(call["tool_name"]);
  const tool = HOOK_EVENTS[event].tool;
  const input: HookInput = {
    event, engine: file.engine, session_id: text(call["session_id"]),
    ...(tool ? { tool: { kind: name === null ? null : file.kinds[name] ?? null,
                         name: name ?? "", input: call["tool_input"] ?? null } } : {}),
    ...(event === "prompt" ? { prompt: text(call["prompt"]) ?? "" } : {}),
  };
  const hooks = file.hooks.filter((hook) => !tool || hook.matcher === null
    || (name !== null && new RegExp(hook.matcher).test(name)));
  const outcomes = await Promise.all(hooks.map(async (hook) => ({
    label: `the agent's ${event} hook ${hook.run[0]!}`,
    outcome: outcomeOf(event, await runOne(hook, file.cwd, env, JSON.stringify(input))) })));
  // Each reason under its hook's label, so the stderr is never JSON: Gemini takes a hook's
  // stderr as its answer when its stdout is empty (`hookRunner.ts`), a `tool_input` there a
  // rewrite.
  const blocks = outcomes.flatMap(({ label, outcome }) => "block" in outcome
    ? [`${label} blocked it: ${outcome.block}`] : "failed" in outcome && HOOK_EVENTS[event].block
      ? [`${label} failed, so it holds: ${outcome.failed}`] : []);
  if (blocks.length > 0) {
    return { code: BLOCK, out: "", err: `${blocks.join("\n")}\n` };
  }
  const failed = outcomes.flatMap(({ label, outcome }) => "failed" in outcome
    ? [`${label} failed: ${outcome.failed}`] : []);
  const contexts = outcomes.flatMap(({ outcome }) => "context" in outcome ? [outcome.context]
    : []);
  const out = contexts.length === 0 ? "" : `${JSON.stringify({ hookSpecificOutput: {
    hookEventName: text(call["hook_event_name"]) ?? event,
    additionalContext: contexts.join("\n\n") } })}\n`;
  return { code: failed.length > 0 ? FAILED : NOTHING, out,
           err: failed.length === 0 ? "" : `${failed.join("\n")}\n` };
}

/** Each of the run's hooks on `event` whose program is not found or not executable, as its user. */
export function unrunnable(event: HookEvent, file: RunHooks,
                           env: Readonly<Record<string, string | undefined>>): string[] {
  return file.hooks.flatMap((hook) => {
    const program = hook.run[0]!;
    const candidates = program.includes("/") ? [resolve(file.cwd, program)]
      : (env["PATH"] ?? "").split(delimiter).filter((dir) => dir !== "")
        .map((dir) => join(dir, program));
    const found = candidates.some((path) => {
      try {
        accessSync(path, constants.X_OK);
        return statSync(path).isFile();
      } catch {
        return false;
      }
    });
    return found ? [] : [`the agent's ${event} hook ${program} is no program it can run`];
  });
}

/**
 * The run's own directory: the one directory under `system` root owns that is of this
 * process's group (a run's system directory, `withRunUser`); any other finding fails loudly.
 */
export function ownRun(system: string, gid: number): string {
  const dirs = readdirSync(system).map((name) => join(system, name)).filter((path) => {
    const stat = lstatSync(path);
    return stat.isDirectory() && stat.uid === 0 && stat.gid === gid;
  });
  if (dirs.length !== 1) {
    throw new Error(`no one run's directory is this user's (${dirs.length} are)`);
  }
  return dirs[0]!;
}

/** The run's own hooks on `event`, in its directory `run`, root's file; null, it has none. */
export function eventHooks(run: string, event: HookEvent): RunHooks | null {
  const path = join(run, hooksFile(event));
  const stat = lstatSync(path, { throwIfNoEntry: false });
  if (stat === undefined) {
    return null;
  }
  if (!stat.isFile() || stat.uid !== 0) {
    throw new Error(`${path} is not root's file`);
  }
  return JSON.parse(readFileSync(path, "utf8")) as RunHooks;
}

/** The command: `hooks <event>` (the engine's input on `stdin`) or `hooks check`. */
export async function hooksCommand(args: readonly string[], system: string,
                                   env: Readonly<Record<string, string | undefined>>,
                                   stdin: AsyncIterable<Buffer | string>,
                                   out: { write(text: string): unknown },
                                   err: { write(text: string): unknown }): Promise<number> {
  const [event, ...rest] = args;
  const fail = (reason: string, code: number) => {
    err.write(`${reason}\n`);
    return code;
  };
  if (rest.length > 0 || (event !== "check" && !isHookEvent(event))) {
    return fail("the agent's hooks: the hook names no event", BLOCK);
  }
  const events = event === "check" ? Object.keys(HOOK_EVENTS) as HookEvent[] : [event];
  let files: [HookEvent, RunHooks | null][];
  try {
    const run = ownRun(system, process.getgid!());
    files = events.map((each) => [each, eventHooks(run, each)]);
  } catch (error) {
    return fail(`the agent's hooks could not be read: ${String(error)}`,
                event !== "check" && HOOK_EVENTS[event].block ? BLOCK : FAILED);
  }
  if (event === "check") {
    const problems = files.flatMap(([each, file]) => file === null ? []
      : unrunnable(each, file, env));
    return problems.length === 0 ? NOTHING : fail(problems.join("\n"), FAILED);
  }
  const file = files[0]![1];
  if (file === null) {
    return NOTHING;
  }
  let text = "";
  for await (const chunk of stdin) {
    text += String(chunk);
  }
  let native: unknown = null;
  try {
    native = JSON.parse(text);
  } catch {
    native = null;
  }
  const answer = await dispatch(file, event, native, env);
  out.write(answer.out);
  err.write(answer.err);
  return answer.code;
}
