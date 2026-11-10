/**
 * The Worker↔runner protocol (runner by digest; the rule is `state-format.ts`'s): every
 * command the runner takes, the exit codes it and its loader answer, the state directory's
 * layout both sides name, and the documents the Worker hands the runner and reads back. The
 * Worker (`src/control.ts`) and the runner (`runner.ts`) both dispatch from these names, so
 * neither holds a copy of the other's. `protocol.json` records each version's commands and
 * `state` answer, frozen. The runner reports its own in `state`; the Worker speaks `SPEAKS`
 * and refuses loudly a runner speaking another. A change ships in two deploys: the first
 * Worker speaks both while its runner speaks the old one, the second runner speaks the new
 * one. It imports only the contract's types.
 */
import type { SandboxSignin, SandboxStart } from "../../contract/src/sandbox";

/** The protocol this runner speaks. */
export const PROTOCOL = 1;
/** The runner protocols the Worker speaks: its runner's, and at most the ones either side. */
export const SPEAKS: readonly number[] = Object.freeze([1]);

/** Every command the runner takes, by its name on the command line. */
export const COMMAND = Object.freeze({
  prepare: "prepare",
  adopt: "adopt",
  signinCheck: "signin-check",
  launch: "launch",
  cancel: "cancel",
  status: "status",
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
} as const);

export type Command = (typeof COMMAND)[keyof typeof COMMAND];

/** What the Worker sends. */
export const WORKER_COMMANDS = Object.freeze([
  COMMAND.prepare, COMMAND.adopt, COMMAND.signinCheck, COMMAND.launch, COMMAND.cancel,
  COMMAND.status, COMMAND.state, COMMAND.quietWait, COMMAND.renewWait, COMMAND.renewed,
  COMMAND.recover, COMMAND.loginLaunch, COMMAND.loginInput, COMMAND.loginDrop,
  COMMAND.loginCancel,
] as const);
export type WorkerCommand = (typeof WORKER_COMMANDS)[number];

/** What the runner runs of its own: a job's detached runner, and the sync agent. */
export const INTERNAL_COMMANDS = Object.freeze([COMMAND.run, COMMAND.loginRun,
                                                COMMAND.sync] as const);

/**
 * Retired: commands protocol 1 records that the runner no longer takes, the template's
 * standing checks, which run from a bundle of their own (`probes.ts`) and were never the
 * Worker's. Recorded by mistake; the next protocol's record names the Worker's and the
 * runner's own commands alone.
 */
export const RETIRED_IN_1: readonly string[] = Object.freeze(["login-probe", "matcher-probe",
                                                              "route-probe"]);

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

/** The template's loader, which runs a runner by its digest, and its own install command. */
export const LOADER = "/usr/local/bin/ta-runner";
export const INSTALL = "install";

/** The runners the loader installed, `<sha256>/runner.mjs` each. */
export const RUNNERS = "/opt/ta/runner";
/** The root-only state directory. */
export const STATE_DIR = "/var/lib/ta";
/** Where a runner's bytes are uploaded for the loader to install. */
export const INCOMING = `${STATE_DIR}/incoming`;
/** Where the Worker leaves a job's document and a pasted code. */
export const INBOX = `${STATE_DIR}/inbox`;
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
  return `${INBOX}/${id}.code`;
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

/**
 * A run's start, as the runner reads it from the inbox: the Worker's start but the lease's
 * own secrets, its network and its sealed route, with the control record's scratch
 * retention (`scratch_retention_days`, null when it names none; absent, the same).
 */
export type StartDoc = Omit<SandboxStart, "tokens" | "network" | "route"> & {
  readonly scratch_retention_days?: number | null;
};

/** A sign-in's document: the Worker's sign-in but its lease, secrets, network and route. */
export type SigninDoc = Omit<SandboxSignin, "tokens" | "network" | "lease" | "route">;

/** The lease's secrets, as a start or sign-in that opened the lease hands them. */
export interface LeaseFile {
  readonly lease: SandboxStart["lease"];
  readonly authority: string;
  readonly tokens: NonNullable<SandboxStart["tokens"]>;
}

/** `state`'s answer: how many jobs live, whether every live run's CLI is quiet, the protocol. */
export interface StateAnswer {
  readonly live: number;
  readonly quiet: boolean;
  readonly protocol: number;
}

/** `quiet-wait`'s answer: the live runs, and whether they are quiet. */
export type QuietAnswer = Omit<StateAnswer, "protocol">;

/** `renew-wait`'s answer: how many runs lived when it told them. */
export type RenewAnswer = Pick<StateAnswer, "live">;
