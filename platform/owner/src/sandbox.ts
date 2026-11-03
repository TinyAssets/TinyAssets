/**
 * The `SANDBOX` interface (ADR-034 decision 4.2; `engines-replan.md` §2.1 step 3): what the
 * owner's object asks of slice S's stateless `tinyassets-sandbox` Worker, the only holder
 * of the E2B key, over a service binding. Slice R defines it and calls it; slice S builds
 * the Worker, its config, and its entry in the owner config guard's `SERVICES` (a bound
 * Worker that reaches nothing but E2B).
 *
 * - `start`: resume or create the owner's sandbox and start the in-VM runner for one run, in
 *   the background (E2B's `commands.run(..., { background: true })`), answering at once.
 *   `tokens` and `network` come only with the start that opened the sandbox's lease: the
 *   sandbox is then (re)configured with that network, whose transform carries the egress
 *   token, and the runner's sync agent takes the sync token. A run that joins a live lease
 *   gets neither: its sandbox is configured already. `config` is the run's whole
 *   environment (slice E's `runEnv`, G4's `sandboxEnv`), which the runner sets as it is,
 *   from an empty one. The runner reaches the object only by `urls`: the run's MCP door,
 *   its event door and the sign-in report door (`signin`: each engine that signs in with
 *   the vendor's own login, whether its record's `status_argv` succeeds, and the models it
 *   offers; presence only, never a credential), all on the egress host, where E2B's
 *   transform sets the egress token outside the VM.
 * - `cancel`: stop the run's CLI (SIGTERM, then a kill after a grace; "Stop a run with
 *   SIGTERM", Claude's headless docs). The run settles from the runner's last report, or
 *   `unknown` if the runner is lost.
 * - `status`: whether the run's runner still runs, for slice S's lost-runner checks; the
 *   object's status doors read the run's receipt, never this.
 * - `meter` (slice P): the Worker's own log of the owner's sandboxes started, paused,
 *   resumed and killed, each with its authority and time, after a cursor; the object reads
 *   it into its sandbox time (`sandbox-time.ts`), and the Worker may drop what a cursor
 *   passed. It limits nothing: the founder's cost view.
 *
 * Absent, nothing reaches `running`: admission refuses before it writes anything
 * (`SANDBOX_ABSENT`). Tests put a recording fake behind the interface, in tests only.
 */
import type { CredentialKind } from "../../doors/src/sources";

import type { NetworkPlan } from "./network";
import type { MeterEvent } from "./sandbox-time";

export const SANDBOX_ABSENT = "sandbox_absent: no sandbox is bound, so no run can start";

/** A run's environment, as the lease's config gives it (`SandboxLeases.config`). */
export interface RunConfig {
  readonly generation: number;
  readonly egress: { readonly mode: string;
                     readonly destinations: readonly { host: string; port: number }[] };
  readonly env: Readonly<Record<string, string>>;
  readonly inherit: readonly string[];
}

export interface SandboxStart {
  readonly run: string;
  readonly agent: string;
  readonly conversation: string;
  /** The CLI record the runner invokes, and the credential it runs on. */
  readonly engine: string;
  readonly credential: CredentialKind;
  readonly model: string | null;
  /** The engine's session id to resume (`resume_argv`), or null for a new conversation. */
  readonly session_id: string | null;
  /** The input, on the engine's stdin or its prompt argument (the record says which). */
  readonly prompt: string;
  /** A run no one waits on: the record's `unattended_argv` applies. */
  readonly unattended: boolean;
  /**
   * Whose sandbox the run runs in: the owner's own work's (`owner`), or one outside
   * client's id. One sandbox an authority (slice R's review H-1): runs of different
   * authority never share a VM, and a client's sandbox syncs only what that client may.
   */
  readonly authority: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  /**
   * The lease's tokens, only when this start opened it: the egress token (E2B's transform),
   * the sync token (the sync agent) and the runner's own secret (`x-ta-runner` on its event
   * and sign-in posts), which the runner keeps where no CLI can read it.
   */
  readonly tokens: { readonly egress_token: string; readonly sync_token: string;
                     readonly runner_token: string } | null;
  /** The owner's network, its transform carrying the egress token, with `tokens` only. */
  readonly network: NetworkPlan | null;
  readonly config: RunConfig;
  /**
   * The run's MCP door (the record's `{mcp_url}`, carrying the run's own secret), its event
   * door and the sign-in door (both the runner's, by its secret). A record whose MCP
   * template carries `{run_secret}` (a header, `x-ta-run`) takes `mcp_bare` as its
   * `{mcp_url}`, the secret in the header and out of the URL.
   */
  readonly urls: { readonly mcp: string; readonly mcp_bare: string; readonly events: string;
                   readonly signin: string };
}

/**
 * The start's answer. A start on the vendor's own sign-in (`credential`
 * `vendor_login_in_sandbox`) first runs the record's `status_argv` inside that sandbox, and
 * answers `{started: false, code: "signin_required"}` when no login is there: a login is
 * never copied between sandboxes, so the owner signs in inside it (the vendor's own flow).
 */
export type Started = { readonly started: true } | { readonly started: false; readonly code: string };

/** The sandbox Worker, as the owner's object binds it (`SANDBOX`). */
export interface SandboxService {
  start(owner: string, start: SandboxStart): Promise<Started>;
  cancel(owner: string, request: { readonly run: string; readonly lease_id: string }):
    Promise<{ readonly cancelled: boolean }>;
  status(owner: string, request: { readonly run: string; readonly lease_id: string }):
    Promise<{ readonly state: "running" | "exited" | "gone" }>;
  /** The log after `after`: at most `METER_BATCH` entries, and the cursor past them. */
  meter(owner: string, request: { readonly after: number }):
    Promise<{ readonly events: readonly MeterEvent[]; readonly next: number }>;
}

/** A start's answer, checked: a refusal's code is a fixed code, never prose. */
export function started(value: unknown): Started {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const answer = value as Record<string, unknown>;
    if (answer["started"] === true && Object.keys(answer).length === 1) {
      return { started: true };
    }
    if (answer["started"] === false && typeof answer["code"] === "string"
        && /^[a-z][a-z0-9_]{0,63}$/.test(answer["code"]) && Object.keys(answer).length === 2) {
      return { started: false, code: answer["code"] };
    }
  }
  throw new Error("the sandbox's start answered out of shape");
}
