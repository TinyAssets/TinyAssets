/**
 * The sandbox Worker's RPC contract (`platform/sandbox`,
 * `docs/decisions/untangle-2026-11-wave-1.md` A1): the `SANDBOX` interface the owner's object
 * binds (ADR-034 decision 4.2; `engines-replan.md` §2.1 step 3), and the values that cross it.
 * The sandbox Worker implements it (`sandbox/src/index.ts`) and the owner's object calls it
 * (`owner/src/sandbox.ts`, which says what each call does), so neither imports the other's
 * source.
 *
 * - a run's start (`SandboxStart`) and a vendor sign-in's (`SandboxSignin`), each answered
 *   `Started`; the network the start that opens a lease configures (`NetworkPlan`, made by
 *   `owner/src/network.ts` in E2B's own field names), and a live lease's network made again
 *   under a newer egress generation (`NetworkReapply`, answered `NetworkApplied`);
 * - the Worker's own log of the owner's sandboxes (`MeterEvent`), read `METER_BATCH` at a time.
 */

import type { AgentHooks, McpServer } from "./agent-definition.ts";
import type { EgressMode } from "./policy-document.ts";

/** What an engine runs on: the vendor's own sign-in in the sandbox, or an egress-keyed key. */
export type CredentialKind = "vendor_login_in_sandbox" | "api_key";
/** Every credential an engine runs on. */
export const CREDENTIAL_KINDS: readonly CredentialKind[] = Object.freeze([
  "vendor_login_in_sandbox", "api_key"]);

/** E2B's `SandboxNetworkRule`, its static transform. */
export interface NetworkRule {
  readonly transform: { readonly headers: Readonly<Record<string, string>> };
}

/** E2B's network configuration, as `Sandbox.create({ network })` and `updateNetwork` take it. */
export interface NetworkDocument {
  readonly allowOut: readonly string[];
  readonly denyOut: readonly string[];
  readonly dnsServers: readonly string[];
  readonly rules: Readonly<Record<string, readonly NetworkRule[]>>;
}

/** A destination the document could not express, and why. */
export interface Unexpressed {
  readonly host: string;
  readonly port: number;
  readonly reason: "port";
}

/**
 * The network the owner's object makes (`owner/src/network.ts`) for a start that opens a
 * lease, a sign-in's or a run's, and for a re-apply. The egress generation it was made under
 * is in the document's door transform alone (`generationOf`), the header E2B stamps and the
 * door admits by.
 */
export interface NetworkPlan {
  readonly network: NetworkDocument;
  readonly mode: EgressMode;
  /** Destinations refused because E2B cannot filter them by name. */
  readonly unexpressed: readonly Unexpressed[];
  /** Whether the network is a data boundary: never, in v1 (`BOUNDARY_NOTE`). */
  readonly boundary: false;
  readonly note: string;
}

/** A run's environment, as the owner's object makes it for the run's start. */
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
  /** The model the run is given (`owner/src/runs.ts` `choose`), by its record's `model_argv`. */
  readonly model: string;
  /** The engine's session id to resume (`resume_argv`), or null for a new conversation. */
  readonly session_id: string | null;
  /** The input, on the engine's stdin or its prompt argument (the record says which). */
  readonly prompt: string;
  /** A run no one waits on: the record's `unattended_argv` applies. */
  readonly unattended: boolean;
  /**
   * Whose sandbox the run runs in: the owner's own work's (`owner`), or one outside
   * client's id. One sandbox an authority: runs of different
   * authority never share a VM, and a client's sandbox syncs only what that client may.
   */
  readonly authority: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  /**
   * The lease's tokens, only when this start opened it: the sync token (the sync agent) and
   * the runner's own secret (`x-ta-runner` on its event and sign-in posts), which the runner
   * keeps where no CLI can read it. The egress token is not among them: only the network's
   * transform carries it, outside the VM.
   */
  readonly tokens: { readonly sync_token: string; readonly runner_token: string } | null;
  /** The owner's network, its transform carrying the egress token, with `tokens` only. */
  readonly network: NetworkPlan | null;
  /**
   * The vendor hosts the run's sandbox network is let out to, beside the network's own: its
   * engine's sign-in and model hosts on the vendor's own sign-in, none on a key. The owner's
   * plan hands them as it hands the network (`owner/src/sandbox.ts`), so the sandbox Worker
   * reads no source record.
   */
  readonly hosts: readonly string[];
  readonly config: RunConfig;
  /**
   * Whose sandbox this is, sealed for E2B's metadata (`sealRoute`): E2B echoes it on its
   * signed lifecycle events, which the doors open to reach this owner's object.
   */
  readonly route: string;
  /**
   * The run's MCP door (its `RUN_DOOR_SERVER`'s URL, carrying the run's own secret), its
   * event door and the sign-in door (both the runner's, by its secret). A record whose MCP
   * template carries `{run_secret}` (a header, `x-ta-run`) takes `mcp_bare` as the door's
   * URL, the secret in the header and out of the URL.
   */
  readonly urls: { readonly mcp: string; readonly mcp_bare: string; readonly events: string;
                   readonly signin: string };
  /**
   * The agent's own MCP servers (`mcp.json`) its admission kept: those the owner's egress lets
   * out (`owner/src/network.ts` `agentServers`), by name.
   */
  readonly mcp_servers: Readonly<Record<string, McpServer>>;
  /** The agent's own hooks (`hooks.json`), by event, as its admission read them. */
  readonly hooks: AgentHooks["hooks"];
}

/** The start codes the owner's object answers by name (`START_CODES`). */
export const SANDBOX_WAITING = "sandbox_waiting";
export const SIGNIN_REQUIRED = "signin_required";

/**
 * Every code a start (a run's or a sign-in's) answers in place of starting, the closed list:
 * - `sandbox_waiting`: no room for one more running sandbox (the platform's line, the
 *   control record's ceiling, or E2B's own refusal), so the job waits until the line rings
 *   its owner, never refused;
 * - `signin_required`: a run on the vendor's own sign-in (`credential`
 *   `vendor_login_in_sandbox`) found no login in its sandbox (the record's `status_argv`, run
 *   there first): a login is never copied between sandboxes, so the owner signs in inside
 *   it, by the vendor's own flow;
 * - `sandbox_unconfigured`: the control record never held the sandbox numbers;
 * - `sandbox_lost`: a joining job found no sandbox to run in.
 */
export const START_CODES = Object.freeze([SANDBOX_WAITING, SIGNIN_REQUIRED,
                                          "sandbox_unconfigured", "sandbox_lost"] as const);
export type StartCode = (typeof START_CODES)[number];

/** The start's answer: started, or one of the start codes. */
export type Started = { readonly started: true }
  | { readonly started: false; readonly code: StartCode };

/**
 * The vendor's own sign-in started in a sandbox (`owner/src/signins.ts`), or its sign-out:
 * the record's `cli.login` run there by the runner as a user of its own (`ta-runner
 * login-launch`), under the sandbox's lease, which a start that opened it configures
 * (`tokens`, `network`) as a run's would. `urls.events` is the sign-in's own event door and
 * `urls.signin` the presence report's.
 */
export interface SandboxSignin {
  readonly id: string;
  readonly engine: string;
  readonly mode: "in" | "out";
  readonly authority: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly tokens: SandboxStart["tokens"];
  readonly network: NetworkPlan | null;
  /** The engine's vendor hosts, let out as a start's (`SandboxStart.hosts`). */
  readonly hosts: readonly string[];
  /**
   * Whose sandbox this is, sealed for E2B's metadata, as a start's (`SandboxStart.route`): a
   * sandbox a sign-in first makes costs as any other, and its lifecycle events reach this
   * owner's object by it.
   */
  readonly route: string;
  readonly urls: { readonly events: string; readonly signin: string };
}

/**
 * A live lease's network made again under a newer egress generation (an egress policy write
 * since its start): the plan's transform carries the generation header and no egress token,
 * which the sandbox Worker keeps as the live sandbox's transform has it.
 */
export interface NetworkReapply {
  readonly authority: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly network: NetworkPlan;
}

/**
 * A re-apply's answers (`NetworkApplied`): `applied`, on the running sandbox now; `at_resume`,
 * its sandbox paused, the network kept and applied as it resumes; `lost`, no sandbox of that
 * lease.
 */
export const NETWORK_STATES = Object.freeze(["applied", "at_resume", "lost"] as const);

/** A re-apply's answer. */
export type NetworkApplied = { readonly state: (typeof NETWORK_STATES)[number] };

/** The most log entries one read takes. */
export const METER_BATCH = 1000;

/** One entry of the sandbox Worker's log. */
export interface MeterEvent {
  readonly seq: number;
  readonly authority: string;
  readonly kind: "start" | "resume" | "pause" | "kill";
  /** When it happened, in ms. */
  readonly at: number;
}

/** The sandbox Worker, as the owner's object binds it (`SANDBOX`). */
export interface SandboxService {
  /** A sign-in started in the authority's sandbox (`SandboxSignin`); the start's answer. */
  signin(owner: string, signin: SandboxSignin): Promise<Started>;
  /** A pasted code to the live sign-in's CLI's stdin; whether it was live to take it. */
  signinInput(owner: string, request: { readonly authority: string; readonly id: string;
                                        readonly code: string }):
    Promise<{ readonly delivered: boolean }>;
  /** A live sign-in stopped. */
  signinCancel(owner: string, request: { readonly authority: string; readonly id: string }):
    Promise<{ readonly cancelled: boolean }>;
  /**
   * A run started in the authority's sandbox (`SandboxStart`); the start's answer. It, and a
   * sign-in's start, throws a refusal (`Refusal`) only while the job has not launched: its
   * own fault before the launch is `sandbox_start_failed`. A plain error is a lost answer or
   * a fault at or after the launch, so the job may run.
   */
  start(owner: string, start: SandboxStart): Promise<Started>;
  /** A run's CLI stopped, under whichever lease it was launched; whether it was live. */
  cancel(owner: string, request: { readonly run: string }):
    Promise<{ readonly cancelled: boolean }>;
  /**
   * The lease is over: no job stands under it. The sandbox Worker pauses that sandbox once
   * nothing lives in it (a stopped job of the lease may still be ending), unless a job
   * launched in it since (the zero idle tail, ADR-039 decision 2.16; a paused sandbox costs
   * nothing and keeps its memory, files and sign-ins). The Worker keeps asking E2B until the
   * pause lands, and reaps on its own a sandbox idle for a lease's TTL (a release that never
   * reached it), so an idle sandbox is never left to its ceiling.
   */
  release(owner: string, request: { readonly lease_id: string }):
    Promise<{ readonly paused: boolean }>;
  /**
   * A live lease's network replaced whole on its sandbox (E2B's network update), under a
   * newer egress generation: the live egress token kept, and every job launched in it kept
   * its vendor hosts. A paused sandbox applies it as it resumes.
   */
  network(owner: string, request: NetworkReapply): Promise<NetworkApplied>;
  /** The owner waits for a sandbox no more: out of the platform's line. */
  unwait(owner: string): Promise<void>;
  /** The owner erased: each of their sandboxes killed, the Worker's state of them deleted. */
  erase(owner: string): Promise<{ readonly killed: number }>;
  /** The log after `after`: at most `METER_BATCH` entries, and the cursor past them. */
  meter(owner: string, request: { readonly after: number }):
    Promise<{ readonly events: readonly MeterEvent[]; readonly next: number }>;
}
