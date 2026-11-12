/**
 * The sandbox Worker's RPC contract (`tinyassets-sandbox`,
 * `docs/decisions/untangle-2026-11-wave-1.md` A1): the `SANDBOX` interface the owner's object
 * binds (ADR-034 decision 4.2; `engines-replan.md` §2.1 step 3), and the values that cross it.
 * The sandbox Worker implements it (`sandbox/src/index.ts`) and the owner's object calls it
 * (`owner/src/sandbox.ts`, which says what each call does), so neither imports the other's source.
 *
 * - a run's start (`SandboxStart`) and a vendor sign-in's (`SandboxSignin`), each answered
 *   `Started`; the network the start that opens a lease configures (`NetworkPlan`, made by
 *   `owner/src/network.ts` in E2B's own field names), and a live lease's network made again
 *   under a newer egress generation (`NetworkReapply`, answered `NetworkApplied`);
 * - the Worker's own log of the owner's sandboxes (`MeterEvent`), read `METER_BATCH` at a time.
 */

/** What an engine runs on: the vendor's own sign-in in the sandbox, or an egress-keyed key. */
export type CredentialKind = "vendor_login_in_sandbox" | "api_key";

export type EgressMode = "open" | "allowlist" | "ask";

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

/** What the sandbox slice sends, and what the object receipts when it is applied. */
export interface NetworkPlan {
  readonly network: NetworkDocument;
  readonly mode: EgressMode;
  /** The policy generation the document was made under. */
  readonly generation: number;
  /** Destinations refused because E2B cannot filter them by name. */
  readonly unexpressed: readonly Unexpressed[];
  /** `ask` mode: what is not listed is refused, with a refusal receipt at each apply. */
  readonly receipt: boolean;
  /** Whether the network is a data boundary: never, in v1 (`BOUNDARY_NOTE`). */
  readonly boundary: false;
  readonly note: string;
}

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
   * The lease's tokens, only when this start opened it: the sync token (the sync agent) and
   * the runner's own secret (`x-ta-runner` on its event and sign-in posts), which the runner
   * keeps where no CLI can read it. The egress token is not among them: only the network's
   * transform carries it, outside the VM.
   */
  readonly tokens: { readonly sync_token: string; readonly runner_token: string } | null;
  /** The owner's network, its transform carrying the egress token, with `tokens` only. */
  readonly network: NetworkPlan | null;
  readonly config: RunConfig;
  /**
   * The run's MCP door (the record's `{mcp_url}`, carrying the run's own secret), its event
   * door and the sign-in door (both the runner's, by its secret). A record whose MCP
   * template carries `{run_secret}` (a header, `x-ta-run`) takes `mcp_bare` as its
   * `{mcp_url}`, the secret in the header and out of the URL.
   */
  /**
   * Whose sandbox this is, sealed for E2B's metadata (`sealRoute`, slice P): E2B echoes it on
   * its signed lifecycle events, which the doors open to reach this owner's object.
   */
  readonly route: string;
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

/**
 * The vendor's own sign-in started in a sandbox (slice SIGNIN; `owner/src/signins.ts`), or
 * its sign-out: the record's `cli.login` run there by the runner as a user of its own
 * (`ta-runner login-launch`), under the sandbox's lease, which a start that opened it
 * configures (`tokens`, `network`) as a run's would. `region` is the owner's choice among
 * the record's `cli.regions` (its hosts let out), `urls.events` the sign-in's own event door
 * and `urls.signin` the presence report's.
 */
export interface SandboxSignin {
  readonly id: string;
  readonly engine: string;
  readonly region: string | null;
  readonly mode: "in" | "out";
  readonly authority: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly tokens: SandboxStart["tokens"];
  readonly network: NetworkPlan | null;
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
 * A re-apply's answer: `applied`, on the running sandbox now; `at_resume`, its sandbox
 * paused, the network kept and applied as it resumes; `lost`, no sandbox of that lease.
 */
export type NetworkApplied = { readonly state: "applied" | "at_resume" | "lost" };

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
  start(owner: string, start: SandboxStart): Promise<Started>;
  cancel(owner: string, request: { readonly run: string; readonly lease_id: string }):
    Promise<{ readonly cancelled: boolean }>;
  status(owner: string, request: { readonly run: string; readonly lease_id: string }):
    Promise<{ readonly state: "running" | "exited" | "gone" }>;
  /**
   * The lease is over: no run lives under it. The sandbox Worker pauses that sandbox at
   * once unless a run started in it since (ADR-026's zero idle tail; a paused sandbox costs
   * nothing and keeps its memory, files and sign-ins). The Worker keeps asking E2B until
   * the pause lands, and reaps on its own a sandbox idle for a lease's TTL (a release that
   * never reached it), so an idle sandbox is never left to its ceiling.
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
