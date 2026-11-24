/**
 * The Worker↔runner protocol (runner by digest; the rule is `state-format.ts`'s): every
 * command the runner takes, the exit codes it and its loader answer, the paths both sides
 * name, the documents the Worker hands the runner and the answers it reads back. The Worker
 * (`src/control.ts`) and the runner (`runner.ts`) both dispatch from these names, so neither
 * holds a copy of the other's. The documents and answers are this protocol's own types, not
 * the owner contract's: the Worker maps its contract's values into them in one place
 * (`control.ts` `startDocument`, `signinDocument`, `leaseFile`), so an owner contract edit
 * never changes what a live runner of an older deploy reads.
 *
 * `protocol.json` records each version whole (its commands, exit codes, paths, documents
 * and answers), frozen by digest (`state-format.test.ts`); this file, what the Worker writes
 * and what the runner answers are held to its record. The runner reports its own protocol in
 * `state`, which every protocol answers alike (`state-format.test.ts`), so the Worker reads any
 * runner's protocol and whether anything lives on it. The Worker speaks `SPEAKS`: it launches
 * nothing on a runner speaking another, refusing loudly, and adopts its own over one that
 * nothing lives on, whatever that one speaks. Each document it writes holds every field a
 * protocol it speaks reads (one a newer protocol added, one it dropped), so any of their
 * runners reads it. A change ships in two deploys: the first Worker speaks both while its
 * runner speaks the old one, the second runner speaks the new one. A later Worker speaks the
 * new one alone, dropping what only the old one read. The two deploys keep a run that lives
 * across one, or across its rollback, on a Worker that speaks its runner's protocol; where no
 * run can (no owner has a sandbox before the cutover's routes), one deploy carries all three,
 * and a sandbox whose runner outlived it would be refused by name at its next start while
 * anything lives on it, or for good once rebooted (`control.ts` `#speaking`).
 */
import { LEASE_TTL_S } from "../../contract/src/limits";

/** The protocol this runner speaks. */
export const PROTOCOL = 5;
/** The runner protocols the Worker speaks: its runner's, and at most the ones either side. */
export const SPEAKS: readonly number[] = Object.freeze([5]);

/** Every command the runner takes, by its name on the command line. */
export const COMMAND = Object.freeze({
  prepare: "prepare",
  adopt: "adopt",
  signinCheck: "signin-check",
  launch: "launch",
  cancel: "cancel",
  state: "state",
  quietWait: "quiet-wait",
  renewWait: "renew-wait",
  renewed: "renewed",
  recover: "recover",
  loginLaunch: "login-launch",
  loginInput: "login-input",
  loginDrop: "login-drop",
  loginCancel: "login-cancel",
  run: "run",
  loginRun: "login-run",
  sync: "sync",
  decide: "decide",
  hooks: "hooks",
} as const);

export type Command = (typeof COMMAND)[keyof typeof COMMAND];

/** What the Worker sends. */
export const WORKER_COMMANDS = Object.freeze([
  COMMAND.prepare, COMMAND.adopt, COMMAND.signinCheck, COMMAND.launch, COMMAND.cancel,
  COMMAND.state, COMMAND.quietWait, COMMAND.renewWait, COMMAND.renewed, COMMAND.recover,
  COMMAND.loginLaunch, COMMAND.loginInput, COMMAND.loginDrop, COMMAND.loginCancel,
] as const);
export type WorkerCommand = (typeof WORKER_COMMANDS)[number];

/**
 * The commands a run's CLI runs on its events and calls, the hook helper and the agent's
 * hooks' dispatcher: the runner's hook program takes them (`hook-entry.ts`, at `hookProgram`),
 * not the runner's own entry, so each loads only what they use.
 */
export const HOOK_COMMANDS = Object.freeze([COMMAND.decide, COMMAND.hooks] as const);
export type HookCommand = (typeof HOOK_COMMANDS)[number];

/** What the runner runs of its own: a job's detached runner, the sync agent, the hook commands. */
export const INTERNAL_COMMANDS = Object.freeze([COMMAND.run, COMMAND.loginRun, COMMAND.sync,
                                                ...HOOK_COMMANDS] as const);

/**
 * The exit codes. The runner's: done, a usage refused (no such command), "no" (not signed
 * in, not live, refused while something lives), and fenced (the sync agent's, to its
 * runner). The loader's (`template/ta-runner`, which a test holds to these): no runner of
 * that digest here, and bytes that are not their digest; a name that is not a digest is its
 * usage refused. Anything else is a fault.
 */
export const EXIT = Object.freeze({
  ok: 0,
  usage: 2,
  no: 3,
  fenced: 4,
  absent: 24,
  mismatch: 25,
} as const);

/**
 * A runner command's deadline, the Worker's and the runner's own (a status check, the wait
 * for a runner's answer, a store's lock): the lease's TTL. The owner's object settles a run
 * whose runner it has not heard from in `LEASE_TTL_S`, so a command that takes longer has
 * lost its run regardless.
 */
export const COMMAND_MS = LEASE_TTL_S * 1000;

/** The template's loader, which runs a runner by its digest, and its own install command. */
export const LOADER = "/usr/local/bin/ta-runner";
export const INSTALL = "install";

/** The runners the loader installed, `<sha256>/runner.mjs` each. */
export const RUNNERS = "/opt/ta/runner";

/**
 * The hook program of the runner of `digest`, beside it: built into the runner's bytes and
 * written there, root's, by the runner at each run's start (`runner.ts` `writeHookProgram`).
 */
export function hookProgram(digest: string): string {
  return `${RUNNERS}/${digest}/hooks.cjs`;
}

/** The root-only state directory. */
export const STATE_DIR = "/var/lib/ta";
/** Where a runner's bytes are uploaded for the loader to install. */
export const INCOMING = `${STATE_DIR}/incoming`;
/** Where the Worker leaves a job's document and a pasted code. */
export const INBOX = `${STATE_DIR}/inbox`;
/** How a pasted code's name in the inbox ends (`inboxCode`). */
export const CODE_SUFFIX = ".code";
/** The lease's secrets, as the start or sign-in that opened it hands them (`LeaseFile`). */
export const LEASE_FILE = `${STATE_DIR}/lease.json`;

/** A runner's upload, by its digest. */
export function incoming(digest: string): string {
  return `${INCOMING}/${digest}`;
}

/** A job's document in the inbox (`StartDoc`, `SigninDoc`), by the job's id. */
export function inboxDoc(id: string): string {
  return `${INBOX}/${id}.json`;
}

/** A sign-in's pasted code in the inbox, by the sign-in's id. */
export function inboxCode(id: string): string {
  return `${INBOX}/${id}${CODE_SUFFIX}`;
}

/**
 * The runner's two kinds of job, a run and a sign-in: where each keeps its directory (one
 * a job, by its id), and the commands that launch, cancel and run it.
 */
export const JOBS = Object.freeze({
  run: { dir: `${STATE_DIR}/runs`, launch: COMMAND.launch, cancel: COMMAND.cancel,
         own: COMMAND.run },
  signin: { dir: `${STATE_DIR}/signin`, launch: COMMAND.loginLaunch,
            cancel: COMMAND.loginCancel, own: COMMAND.loginRun },
} as const);
export type JobKind = keyof typeof JOBS;

/**
 * What a job's directory holds, and nothing else: its document, moved from the inbox; its
 * state (`state-format.json` records each kind's); its runner's log; the cancel mark; and
 * the socket its runner listens on while it runs (the job channel).
 */
export const JOB_FILES = Object.freeze({
  start: "start.json",
  state: "state.json",
  log: "runner.log",
  cancel: "cancel",
  socket: "job.sock",
} as const);

/** A lease, as a document names it: its id, and the epoch its holder runs under. */
export interface DocLease {
  readonly lease_id: string;
  readonly epoch: number;
}

/**
 * A run's start, as the runner reads it from the inbox: the run, its agent and conversation,
 * the engine's record and the credential it runs on, its input, whose sandbox it runs in and
 * under which lease, the run's config, its doors (`mcp` carries the run's own secret;
 * `mcp_bare` is the one a record sending that secret in a header takes), the agent's own MCP
 * servers its admission kept, and its own hooks by event, an event absent holding none.
 */
export interface StartDoc {
  readonly run: string;
  readonly agent: string;
  readonly conversation: string;
  readonly engine: string;
  readonly credential: "vendor_login_in_sandbox" | "api_key";
  /**
   * The model the run is given. Protocol 5's record keeps `string|null` (frozen): its Workers
   * before #5842 could hand null, and none serves (`probe-model.md`).
   */
  readonly model: string;
  readonly session_id: string | null;
  readonly prompt: string;
  readonly unattended: boolean;
  readonly authority: string;
  readonly lease: DocLease;
  readonly config: {
    readonly generation: number;
    readonly egress: { readonly mode: string;
                       readonly destinations: readonly { host: string; port: number }[] };
    readonly env: Readonly<Record<string, string>>;
    readonly inherit: readonly string[];
  };
  readonly urls: { readonly mcp: string; readonly mcp_bare: string; readonly events: string;
                   readonly signin: string };
  readonly mcp_servers: Readonly<Record<string, DocMcpServer>>;
  readonly hooks: Readonly<Record<string, readonly DocHook[]>>;
}

/** An MCP server of the agent's own, as a start names it: by its URL, or by its command. */
export type DocMcpServer =
  | { readonly url: string }
  | { readonly command: string; readonly args: readonly string[];
      readonly env: Readonly<Record<string, string>> };

/**
 * A hook of the agent's own (`hooks.json`), as a start names it: what it runs (a program and
 * its arguments), on which tool calls (a tool event's), and for how long.
 */
export interface DocHook {
  readonly run: readonly string[];
  readonly match?: { readonly kinds?: readonly string[]; readonly tools?: readonly string[] };
  readonly timeout_s?: number;
}

/**
 * A sign-in's document: the engine signed in or out, whose sandbox, and its doors (its own
 * events, and the presence report's).
 */
export interface SigninDoc {
  readonly id: string;
  readonly engine: string;
  readonly mode: "in" | "out";
  readonly authority: string;
  readonly urls: { readonly events: string; readonly signin: string };
}

/**
 * The lease's secrets, as a start or sign-in that opened the lease hands them: the two the
 * runner reads alone, the sync token (its sync agent's) and the runner's own secret (its
 * event and sign-in posts). The egress token never enters the VM: E2B's transform carries
 * it, outside (`owner/src/network.ts`).
 */
export interface LeaseFile {
  readonly lease: DocLease;
  readonly authority: string;
  readonly tokens: { readonly sync_token: string; readonly runner_token: string };
}

/** `state`'s answer: how many jobs live, whether every live run's CLI is quiet, the protocol. */
export interface StateAnswer {
  readonly live: number;
  readonly quiet: boolean;
  readonly protocol: number;
}

/** `quiet-wait`'s answer: the live runs, and whether they are quiet. */
export interface QuietAnswer {
  readonly live: number;
  readonly quiet: boolean;
}

/** An answer's fields, each with its JSON type. */
export type AnswerFields<T> = Readonly<Record<keyof T, "number" | "boolean">>;

/** The answers the Worker reads, by command: each field's JSON type, which it holds them to. */
export const ANSWERS: {
  readonly [COMMAND.state]: AnswerFields<StateAnswer>;
  readonly [COMMAND.quietWait]: AnswerFields<QuietAnswer>;
} = Object.freeze({
  [COMMAND.state]: Object.freeze({ live: "number", quiet: "boolean", protocol: "number" }),
  [COMMAND.quietWait]: Object.freeze({ live: "number", quiet: "boolean" }),
});
