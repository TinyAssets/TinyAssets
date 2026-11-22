/**
 * Code mode's executor (ADR-034 decision 12): one script whose
 * JavaScript calls tools as `await tools.<name>(args)`, its VM in the codemode Worker.
 *
 * - **Where it runs:** pi-codemode's `RemoteCodemodeSandbox`, whose `remote()` reaches the
 *   `CODEMODE_VM` binding's `codemodeExecute` (one sandbox object per execution, named by the
 *   owner's digest and a random id), so the VM never computes on the owner's object, and the
 *   owner config still binds only services. The tools, the deadline and the abort stay with the
 *   caller; every tool call comes back over RPC through `exchange`.
 * - **What a script reaches:** the tools its caller hands it, each held by the caller to its
 *   own grant. The MCP door's `code` tool (the object's `codeRun`) hands it the
 *   caller's profile's other tools, each call the door's own, admitted again against the
 *   caller's grant, its profile and the owner's switch.
 * - **No stub is held:** each execution's `remote()` calls `CODEMODE_VM.codemodeExecute` with a
 *   name of its own, which resolves when the VM has stopped.
 * - **When:** only while the owner's policy turns it on (`codemode.on`, off by default
 *   until its isolation probe passes, `CODEMODE_DEFAULTS`).
 * - **How long:** `CODEMODE_TIMEOUT_MS` of wall time, tool calls included, and the
 *   codemode Worker's interrupt budget of compute between them.
 */
import { randomUUID } from "node:crypto";

import {
  doorRow, profileNames, type Tool, toolsNamed,
} from "../../contract/src/doors";
import { fitted, jsonBytes } from "../../contract/src/encoded";
import { sha256Hex } from "../../contract/src/digest";
import { clockMs } from "./clock";
import { PAGE_BYTES } from "./bodies";

import { type CodemodeRemote, RemoteCodemodeSandbox } from "@earendil-works/pi-codemode/portable";
import type { Account, Call, Door, DoorUnits, ObjectReads } from "./door";
import type { Binding } from "../../contract/src/binding";
import type { Gate } from "./control-gate";
import type { ProfileName } from "../../contract/src/doors";
import { CODE_MODE_OFF } from "../../contract/src/policy-document";
import { callAgain, Refusal } from "../../contract/src/refusal";
import type { OwnerEnv } from "./env";

/** The tools a script calls, as pi-codemode's sandbox takes them. */
type CodemodeTools = NonNullable<
  ConstructorParameters<typeof RemoteCodemodeSandbox>[0]["tools"]>;

/**
 * How long one script may run, in ms; a script past it ends with pi-codemode's `timeout`. It
 * is the only end of a script whose caller has left, since the door's call into this object
 * carries no cancel: until then its VM holds memory in the codemode isolate, which other
 * owners' VMs share, and its tool calls go on. 5 minutes, the longest the connectors are
 * reported to wait for one tool call (`SLOT_WAIT_MS`'s row, `limits-audit.md`); the MCP
 * SDK's and pi's own clients wait 60 s. Its compute is the codemode Worker's own interrupt
 * budget, which no owner sets (`codemode/src/codemode.ts`, tested in `codemode/test/`).
 */
export const CODEMODE_TIMEOUT_MS = 300_000;

/** What the codemode Worker serves the owner (`contract/src/codemode.ts`): code mode's VMs. */
export type CodemodeService = OwnerEnv["CODEMODE_VM"];

/** What code mode's sandbox reaches: the codemode Worker, the owner it runs for, its tools. */
interface CodemodeParts {
  readonly service: CodemodeService;
  readonly owner: () => string;
  readonly tools: CodemodeTools;
}

/**
 * A sandbox whose scripts run in the codemode Worker, each execution named
 * `<owner's SHA-256>/<random id>`: a fixed length, so no principal's length reaches the cap.
 */
export function codemodeSandbox(parts: CodemodeParts): RemoteCodemodeSandbox {
  return new RemoteCodemodeSandbox({
    timeoutMs: CODEMODE_TIMEOUT_MS,
    tools: [...parts.tools],
    remote: (): CodemodeRemote => {
      const name = `${sha256Hex(parts.owner())}/${randomUUID()}`;
      return { execute: (request, exchange) => parts.service.codemodeExecute(name, request,
                                                                              exchange) };
    },
  });
}

/** One code mode script (`codeRun`). */
export interface CodeRequest {
  readonly script: string;
}

/**
 * A script's end, as pi-codemode reports it: its value or its error, its output, how many
 * tool calls ended each way, and which calls of tools that change something landed
 * (`landed`, in order, a run of the same tool and outcome as one entry with its count), so
 * an agent knows what a stopped script did. Its output past what one tool answer holds
 * (`PAGE_BYTES`, `pages.ts`) is cut, said so (`output_cut`); a value past it alone is
 * refused `answer_too_large`, its calls kept (`fittedCode`).
 */
export type CodeResult =
  | { readonly ok: true; readonly value: JsonValue; readonly output: CodeOutput[];
      readonly calls: CodeCalls; readonly landed: Landed[]; readonly output_cut?: OutputCut }
  | { readonly ok: false; readonly error: { readonly kind: string; readonly message: string };
      readonly output: CodeOutput[]; readonly calls: CodeCalls; readonly landed: Landed[];
      readonly output_cut?: OutputCut };

/**
 * A script's output cut to fit one answer, said so: how many items it had, and
 * how many the answer keeps whole (the next one, a text, cut at a character after them).
 */
interface OutputCut {
  readonly items: number;
  readonly kept: number;
}

/** A run of one tool's calls with one outcome, in a script's order. */
interface Landed {
  readonly name: string;
  readonly status: "ok" | "error" | "cancelled";
  readonly count: number;
}

/** A script's value: JSON, as pi-codemode hands it back (an object or array, untyped). */
type JsonValue = null | boolean | number | string | object;
/** One item of a script's output: its text, or an image. */
type CodeOutput = { readonly type: "text"; readonly text: string; readonly console?: true }
  | { readonly type: "image"; readonly data: string; readonly mimeType: string };
/** A script's tool calls, counted by how each ended. */
interface CodeCalls {
  readonly ok: number;
  readonly error: number;
  readonly cancelled: number;
}

/** Code mode's own tool's name, as its door's row names it (`codeRun`). */
function codeTool(): string {
  return doorRow("codeRun").tool!.name;
}

/** Code mode's refusals; while it is off, `CODE_MODE_OFF`, beside its default. */

export const CODE_NOT_SERVED = "tool_not_served: not in this caller's tool profile";

const CODE_TIMED_OUT = "the script is past its time";

/**
 * A script's answer fitted to one tool answer (`pages.ts`): its value and calls
 * whole, its output's items while they fit and the next text cut at a character, said so
 * (`output_cut`). A value that alone passes one answer is refused `answer_too_large`, its
 * calls and what landed kept, so nothing is lost silently.
 */
function fittedCode(answer: CodeResult): CodeResult {
  if (jsonBytes(answer) <= PAGE_BYTES) {
    return answer;
  }
  const cut: OutputCut = { items: answer.output.length, kept: answer.output.length };
  const bare = { ...answer, output: [], output_cut: cut };
  if (jsonBytes(bare) > PAGE_BYTES) {
    return { ok: false, error: { kind: "answer_too_large", message: CODE_ANSWER_TOO_LARGE },
             output: [], calls: answer.calls, landed: answer.landed };
  }
  const output: CodeOutput[] = [];
  let used = jsonBytes(bare);
  for (const item of answer.output) {
    const bytes = jsonBytes(item) + (output.length > 0 ? 1 : 0);
    if (used + bytes > PAGE_BYTES) {
      const meta = jsonBytes({ ...item, text: "" }) + (output.length > 0 ? 1 : 0);
      if (item.type === "text" && used + meta + 8 <= PAGE_BYTES) {
        output.push({ ...item, text: fitted(item.text, PAGE_BYTES - used - meta + 2).text });
      }
      break;
    }
    used += bytes;
    output.push(item);
  }
  const kept = output.length > 0 && output.at(-1) !== answer.output[output.length - 1]
    ? output.length - 1 : output.length;
  return { ...answer, output, output_cut: { items: answer.output.length, kept } };
}

/** A script's value past what one tool answer holds (`pages.ts`), refused whole. */
export const CODE_ANSWER_TOO_LARGE = "answer_too_large: the script's value is past the " +
  `${PAGE_BYTES} characters one answer holds; return less, or write it with a tool and read ` +
  "it back";

/** What a script runs with: the codemode Worker, its owner, and its tools. */
interface ScriptParts {
  readonly service: CodemodeService;
  readonly owner: string;
  readonly script: string;
  /** The caller's tools, each called through its own door (`call`), rechecked there. */
  readonly tools: readonly Tool[];
  readonly call: (tool: Tool, args: unknown) => Promise<unknown>;
}

/**
 * One script run to its end (the MCP door's `code`, `codeRun`): its tools' calls counted by
 * how each ended, the writes that landed in order, a script past its wall time ended as a
 * `timeout`, and the answer fitted to one tool answer (`fittedCode`).
 */
async function runScript(parts: ScriptParts): Promise<CodeResult> {
  // The wall bound, held here too: pi-codemode's own deadline is a timer, which a script
  // calling tools back to back starved in workerd, so each call checks the clock, which
  // moves with every call's I/O, and ends the execution past it.
  const deadline = clockMs() + CODEMODE_TIMEOUT_MS;
  const late = new AbortController();
  const sandbox = codemodeSandbox({
    service: parts.service,
    owner: () => parts.owner,
    tools: parts.tools.map((tool) => ({
      name: tool.name, description: tool.description, inputSchema: tool.input,
      execute: (args: unknown) => {
        if (clockMs() >= deadline) {
          late.abort();
          throw new Error(CODE_TIMED_OUT);
        }
        return parts.call(tool, args);
      } })),
  });
  try {
    const result = await sandbox.execute(parts.script, { signal: late.signal });
    const calls = { ok: 0, error: 0, cancelled: 0 };
    const landed: Landed[] = [];
    const changes = new Set(parts.tools.filter((tool) => !tool.readOnlyHint)
      .map((tool) => tool.name));
    for (const call of result.calls) {
      calls[call.status] += 1;
      const last = landed.at(-1);
      if (!changes.has(call.name)) {
        continue;
      }
      if (last?.name === call.name && last.status === call.status) {
        landed[landed.length - 1] = { ...last, count: last.count + 1 };
      } else {
        landed.push({ name: call.name, status: call.status, count: 1 });
      }
    }
    const error = late.signal.aborted && !result.ok
      ? { kind: "timeout", message: `Execution timed out after ${CODEMODE_TIMEOUT_MS} ms` }
      : result.ok ? null : { kind: result.error.kind, message: result.error.message };
    const answer: CodeResult = error === null
      ? { ok: true, value: (result as { value: JsonValue }).value, output: result.output,
          calls, landed }
      : { ok: false, error, output: result.output, calls, landed };
    return fittedCode(answer);
  } finally {
    await sandbox.close();
  }
}

// -- THE DOORS: the `codemode` rows' handlers (`owner/doors.json`) --------------------------------

/**
 * What the `codemode` doors reach: the owner's policy's code mode and custom tools
 * (`policy.ts`, which only the object imports), the platform's gates, the codemode Worker,
 * and each tool's own door, called as any call of it is.
 */
export interface CodemodeDoorNeeds extends Pick<DoorUnits, "door">,
  Pick<ObjectReads, "control" | "accountAt" | "invoke"> {
  readonly env: Pick<OwnerEnv, "CODEMODE_VM">;
  readonly policy: { codemode(): boolean; customTools(): readonly string[] };
}

/**
 * The tools a caller is served: a run's, the `run` profile; an outside client's, its grant's
 * (`custom`: the owner's policy's choice); the owner's own, `custom`. `code` only while the
 * owner's policy turns code mode on; while it is off, a profile that holds `code` has it
 * `withheld`: never listed, and a call of it answered by its door's refusal, which says why.
 */
function profile(o: CodemodeDoorNeeds, account: Account): Served {
  const profile: ProfileName = account.run !== null ? "run"
    : account.grant?.profile ?? "custom";
  const names = profile === "custom" ? o.policy.customTools() : profileNames(profile);
  const off = !o.policy.codemode() && names.includes(codeTool());
  return { profile, tools: off ? names.filter((n) => n !== codeTool()) : names,
           withheld: off ? [codeTool()] : [] };
}

/** What `profile` answers: the tools a caller is served, and those its switch withholds. */
interface Served {
  readonly profile: ProfileName;
  readonly tools: readonly string[];
  readonly withheld: readonly string[];
}

/**
 * The tools of `account`'s profile, where it is served `code`: refused `code_mode_off` where
 * the switch withholds it, and `tool_not_served` where its profile does not hold it.
 */
function codeServed(o: CodemodeDoorNeeds, account: Account): readonly string[] {
  const { tools, withheld } = profile(o, account);
  if (withheld.includes(codeTool())) {
    throw new Refusal("code_mode_off", CODE_MODE_OFF);
  }
  if (!tools.includes(codeTool())) {
    throw new Refusal("tool_not_served", CODE_NOT_SERVED);
  }
  return tools;
}

/**
 * One tool call of a script: rechecked as `codeRun` says, then the tool's own door. A refusal
 * reaches the script as the error an MCP tool's result would be: its message, then whether to
 * call again, and when (`callAgain`).
 */
async function scripted(o: CodemodeDoorNeeds, binding: Binding, tool: Tool, args: unknown,
                        gate: () => Promise<string | null>): Promise<unknown> {
  try {
    const closed = await gate();
    if (closed !== null) {
      throw new Refusal("unavailable", closed);
    }
    await o.door(() => {
      if (!codeServed(o, o.accountAt("toolProfile", binding)).includes(tool.name)) {
        throw new Refusal("tool_not_served", CODE_NOT_SERVED);
      }
    });
    // The tool's own door, by its row: admitted again, as any call of it is.
    return await o.invoke(tool.door, binding, args);
  } catch (error) {
    if (!(error instanceof Refusal)) {
      console.error(`code mode's ${tool.name} call failed`, error);
      throw new Error("internal error");
    }
    throw new Error(`${error.message}\n${callAgain(error)}`);
  }
}

export const codemodeDoors = Object.freeze({
  /**
   * Code mode at the MCP door: one script calling the caller's profile's other tools as
   * `tools.<name>(args)`, its VM in the codemode Worker (`codemode.ts`), its wall time
   * `CODEMODE_TIMEOUT_MS`. Refused unless the owner's policy has `codemode.on` (off by
   * default, `CODE_MODE_OFF`) and the caller's profile holds `code`. Each call is the tool's own door with
   * the caller's binding, admitted again (grant, run, admission), held to the caller's
   * profile and the switch as they stand then, and to the platform's gates the door read
   * (the control record's kill switch for an outside client, its maintenance gate for a
   * run or the owner: `control-gate.ts`), so a revoke, a narrowed profile, the switch
   * turned off or outside access switched off refuses the script's next call. A refusal
   * reaches the script as an error with its reason; anything else as `internal error`,
   * logged here in full.
   */
  async codeRun(o: CodemodeDoorNeeds, door: Door<CodeRequest>): Promise<CodeResult> {
    const { binding, owner, script, tools, gate } = await o.door(() => {
      const { account, binding, r } = door.check();
      if (typeof r["script"] !== "string" || r["script"].trim() === "") {
        throw new Refusal("invalid", "a script is text");
      }
      const names = codeServed(o, account);
      // The gate its door read: every `/mcp` and `/api` binding carries the client's origin,
      // the owner's own MCP clients' too, so they take the outside gate; a run (the egress
      // door) and the owner's app (no origin) the maintenance gate.
      const gate: Gate = account.origin !== null && account.run === null ? "outside" : "owner";
      return { binding, owner: account.owner, script: r["script"],
               tools: toolsNamed(names.filter((name) => name !== codeTool())), gate };
    });
    const control = o.control();
    const closed = await control.refusal(gate, owner);
    if (closed !== null) {
      throw new Refusal("unavailable", closed);
    }
    return runScript({ service: o.env.CODEMODE_VM, owner, script, tools,
                       call: (tool, args) => scripted(o, binding, tool, args,
                                                      () => control.refusal(gate, owner)) });
  },
  /**
   * The tools this caller is served (the doors list and call no others), and `code` withheld
   * while the owner's policy has code mode off (`profile`).
   */
  toolProfile(o: CodemodeDoorNeeds, { account }: Call<undefined>): Served {
    return profile(o, account);
  },
});
