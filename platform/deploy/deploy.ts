/**
 * The platform's deploy kit: the six platform Workers deployed to the production Cloudflare
 * account, the doors' routes handed over at the cutover, the handles canary after each
 * change (AGENTS.md Fact 11), and H2's probes (`h2-probes.ts`).
 *
 * It runs from the manual workflow `.github/workflows/platform-deploy.yml`, from `main`, one
 * mode a run (`MODES`, each a row of steps):
 *
 *   node --experimental-strip-types platform/deploy/deploy.ts --mode <mode> [--public | --yes]
 *        [--plan <file>] [--rotate <secrets>] [--migrate <Workers>] [<the mode's own (`OWN`)>]
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-new <file>
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-add <file>
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-retire <file>
 *
 * - `plan`: `dark`'s checks and plan, never a change.
 * - `dark`: storage, secrets and every Worker with no public route, the control record when
 *   absent, the WorkOS redirect URI and E2B's lifecycle webhook (disabled), then the handles
 *   canary.
 * - `routes`: on a host under the zone, its DNS record; then `dark`, then the doors' routes
 *   handed over one at a time, on whatever host the identity names, the egress custom domain
 *   (waited on until the egress door answers on it),
 *   and the webhook enabled at the identity's URL. Before any change it keeps a record of each
 *   of those as they stood (`RouteRecord`), or, while the host's handover has one, keeps that
 *   (`keepRecord`), and the plan names its id.
 * - `handback`: the record `--record` names, the host's oldest, handed back (the cutover's
 *   rollback): the webhook
 *   disabled and the egress custom domain detached unless the record has them so before, then
 *   the zone's routes put back as the record has them, only what the handover changed, one at
 *   a time, then the host's DNS record deleted if `routes` made it; each read back and held to
 *   the record, then the host's route records deleted. Each is held to the record before any
 *   change too: anything changed since `routes` stops it, named. The handles canary follows
 *   the old stack's restart, so the run ends saying where it runs.
 * - `rollback`: the rollout record `--record` names, which `dark` and `routes` keep before
 *   their first Worker deploy (`RolloutRecord`): the run's Workers taken back to the versions
 *   they served before it, callers first, each read back, stopping at the first the run made
 *   forward-only (`forwardOnly`); the record held whole to Cloudflare before any change. Its
 *   routes are `handback`'s.
 * - `h2`: H2's probes alone, once a probe owner exists.
 * - `pair`: the pinned template with the runner this checkout ships and the probes beside
 *   it, checked together on throwaway sandboxes unless E2B already holds their pair's tag
 *   (`sandbox/template/checks.ts pair`).
 * - `control` (the operator path, `operator.ts`): the control record edited in KV by
 *   `--set`'s edits, from `--replace`'s whole record in place of the live one, never through
 *   the doors; written only when `controlOf` takes it, the record it replaces kept first
 *   and the write read back.
 * - `owner` (the operator path): `--act`'s op asked of the doors for `--owner` through the
 *   operator channel, and their answer said, with the owner's state before and after.
 * - `restore` (the operator path): `--owner`'s restore from `--generation` through the
 *   operator channel: a live owner pinned, held and its present state written, its object
 *   wiped only with `--confirm` the words that names; then begun, stepped until done and the
 *   owner admitted. With no `--generation`, the owner's namespace and finished generations,
 *   as its object holds them, or with `--confirm release-<owner>` its pin given up.
 * - `copyback` (`offsite.ts` `copyBack`): the objects of `--owner`'s namespace that the
 *   files bucket lacks, written back from the off-account copy as it stood at `--at`.
 * - `recover` (`offsite.ts` `recover`): after an account's loss, every namespace of the copy as
 *   it stood at `--at` that no erasure mark names written back, the marks first, and each
 *   one's owner located from its generation and its restore begun.
 * - `erase` (the operator path): `--owner` erased through the operator channel as its own
 *   door erases it, for an owner who asked by other means; only with `--confirm` its words,
 *   `erase-<owner>`, which a run without them names.
 * - `rotation` (the operator path, `operator.ts` `rotate`): after a keyring's rotation
 *   (`keyring-add`, then a deploy with `--rotate`), `--keyring`'s op (`platform.json`
 *   `keyrings`) asked of every owner the directory lists, its progress kept in the control
 *   namespace, until a pass answers `left` 0 for every owner; every answer's kid held to the
 *   stored ring's active kid. Never while the doors hold the import window's key.
 * - `retire`: `--keyring` as `keyring-retire` wrote it (its active kid alone), stored by the
 *   founder, set on its Workers, then the canary, only once its rotation's latest pass swept
 *   every owner (for a keyring the off-account copy holds, `KEEP_DAYS` + 1 days after the
 *   first night that copied the first pass begun after the latest restore from the copy), and
 *   only with `--confirm` its words, `retire-<keyring>-<kids>`, which its apply job names.
 *   Irreversible unless the old ring is kept offline.
 *
 * `--public` is the workflow's `plan` job's: the Cloudflare token and the repository's
 * variables alone, every Cloudflare read and the discovery check, the checks that need a
 * production secret left to the apply job and said so in the plan. `--yes` is the `apply`
 * job's, in the main-only `production` environment, with that environment's secrets, never a
 * host's (Direction 8). With neither, a mode checks and plans with the secrets it is given,
 * and changes nothing. The Cloudflare account is the zone's (`Cloudflare.zone`).
 *
 * The apply job runs the plan it was approved on (`Plan`). The plan job writes its plan to
 * the file `--plan` names, which the workflow hands the apply job as the run's artifact. Before
 * any change, the apply job makes the plan job's plan again from the live state and stops,
 * naming each difference, unless it is that file's plan, of this same run. Then its own steps,
 * which the plan job could not run without the production secrets, are held to that plan line
 * by line as they say each line (`holding`): a line the plan job left to the apply job
 * (`checked`) takes its answer, and any other difference stops the run there.
 *
 * Every lookup is checked: a failed listing or read stops the kit loudly, never reads as
 * "absent". So:
 * - a namespace, bucket or queue is created only when its listing answered and lacks it, and
 *   a locked bucket's one lock rule (`platform.json` `bucket_locks`) set only when its rules
 *   answered and are not that rule alone;
 * - a secret is set only on a Worker whose secret listing answered and lacks it, or that the
 *   account's Workers listing lacks (whether a Worker exists is that listing's word alone),
 *   and only with the version the run deploys there;
 *   the kit never makes a secret's value (`keyring-new` makes a first keyring,
 *   `scripts/platform_setup.py` stores one). A held one is never replaced unless `--rotate`
 *   names it, a keyring only by adding a key (mode `retire` alone takes its old kids out);
 *   Cloudflare never shows a held secret, so the kit cannot check that a rotated keyring
 *   keeps the deployed key;
 * - the control record is written only when the API answered that it is absent;
 * - the WorkOS redirect URI is registered only when WorkOS's listing lacks it (GET, then
 *   POST; never a delete), after the other live checks and before any Cloudflare change, and
 *   only then is the early sign-in check run, which needs it listed;
 * - E2B's lifecycle webhook is listed and checked before any change, found by its name,
 *   registered disabled only when E2B's listing lacks it, its URL set to the identity's when
 *   it differs, its secret changed only with the doors' copy, and enabled only by `routes`
 *   after the handover; a run that registers it while the doors hold their copy goes on only
 *   with `--rotate` naming it, which sets both;
 * - the doors' host's DNS record is made only for a host under the zone whose listing
 *   answered and holds none, and a host holding any address record but the placeholder
 *   (`PLACEHOLDER`) stops `routes` before any change;
 * - nothing is deployed unless every migration lock (`owner/migrations.json`, and the sandbox
 *   Worker's `sandbox/migrations/<class>.json`) marks the first deploy and pins every
 *   migration the code ships, in order (`lockCounts`, by `ci/kit/locks.ts`);
 * - a Worker's first deploy is `wrangler deploy`; later ones upload a version, then deploy it
 *   through the stages (`platform.json` `stages`: at 0% beside the version before, then at
 *   100%), which never touches a route, each deployment read back, and the doors smoked at 0%
 *   once they hold their routes (`workers`); one that cannot serve beside its version before
 *   goes to 100% at once (`atOnce`); `--migrate` names a Worker with a pending Durable Object
 *   migration to deploy whole; each version is tagged with the commit, its locks' counts and
 *   its forward count (`platform.json` `forward`);
 *   nothing is deployed, and no secret set by `retire`, on a Worker serving two versions (a
 *   split), and a deploy records the one each serves first (`rollout`);
 * - a retired Worker (`platform.json`) is deleted, with its Durable Objects, only after every
 *   Worker is deployed and only when the account's Workers listing holds it, never forced:
 *   Cloudflare refuses while another Worker still references it. It is the one way the kit
 *   deletes a Worker;
 * - a route handover moves a pattern another Worker holds in place (`PUT`), adds a missing
 *   one (`POST`), and only then deletes another Worker's narrower pattern, so no path is ever
 *   served by nothing (`handover`).
 *
 * Before anything is changed it checks, live: the owner issuer's discovery names
 * `OWNER_ISSUER` exactly and an https JWKS (an absent field is unknown, and unknown stops
 * the kit); WorkOS's authorize, sent the URL the doors build with a throwaway challenge,
 * redirects to AuthKit's sign-in (`authorizeCheck`); the sandbox's `E2B_TEMPLATE` is this
 * tree's image (`sandbox/template/image.ts`) and has a ready build, and E2B holds the tag of
 * the pair this deploy makes (`pairTag`), which `pair` makes.
 * The access token's exact `iss` and `auth_time` only a real sign-in shows: the stage's first
 * sign-in settles them (d3 §H).
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { fileAt } from "../ci/history.ts";
import { PLATFORM, REPO } from "../ci/paths.ts";
import { migrated } from "../ci/kit/locks.ts";
import { WRANGLER } from "../ci/wrangler.ts";
// The envelope and the E2B client import only by their own names, so Node's type stripping
// runs them here.
import { canonical } from "../contract/src/canonical.ts";
import { closedToAll, CONTROL_KEY, controlOf } from "../contract/src/control.ts";
import { EGRESS_HOST } from "../contract/src/egress.ts";
import { KEY_BYTES, parseRing, type Ring, ringText, ringWith } from "../contract/src/envelope.ts";
import {
  APP_RESOURCE, E2B_HOOK_EVENTS, HOST, UPSTREAM_CALLBACK, VERSION_HEADER, WEB_CLIENT, WEB_REDIRECT,
  ZONE,
} from "../contract/src/identity.ts";
import { IMPORT_KEY } from "../contract/src/import-message.ts";
import {
  LOCATE_PAGE, locateArgs, type LocatedPage, namespacesArgs, OPERATOR_KEYRING,
  type OperatorArgs,
} from "../contract/src/operator-message.ts";
import type { LocatedOwner, NamespacePage } from "../contract/src/files.ts";
import { PLATFORM_CALLER } from "../contract/src/binding.ts";
import {
  eraseWords, type RestoreAnswer, type RestoreHold, type RestoreNames, type RestoreSource,
} from "../contract/src/owner.ts";
import {
  authorizeUrl, SECRET_ORIGIN, SIGN_IN_MAX_AGE_S, type UpstreamConfig, upstreamConfig,
} from "../contract/src/signin.ts";
import { principal } from "../contract/src/text.ts";
import { E2bAdmin } from "../sandbox/e2b/admin.ts";
import {
  bundles, type Bundles, checksArgv, pairTag, pinned, treeImage,
} from "../sandbox/template/pair.ts";
import { type JobRun, maskLine, runOf, set, summarize, TOKEN } from "./actions.ts";
import {
  BATCH, Cloudflare, describeBack, describeStep, type DnsRecord, drift, handback, handover, type Kept,
  type LockRule, type Moved, narrower, type Route, type RouteChange, type Serving, unlike,
} from "./cloudflare.ts";
import {
  AUTHORIZE, BUCKET_LOCKS, BUCKETS, CONFIGS, DOORS, E2B_HOOK_URL, FILES_BUCKET, FORWARD,
  IDENTITY, KEYRINGS, MIGRATES, NAMES, NAMESPACES, OPTIONAL, QUEUES,
  RETIRED, ROTATED, ROUTES, SECRET_INPUTS, SECRETS, SHARED, STAGES, WORKER_FACTS, WORKERS,
} from "./facts.ts";
import { type Before, handedProblems, sideAt, TREE } from "./handed.ts";
import { INPUTS as H2_INPUTS } from "./h2-probes.ts";
import {
  BACK_INPUTS, backFrom, type Begin, copyBack, firstNightAfter, KEEP_DAYS, readableAt, recover,
  type Recovering, type Referenced, RESTORE_KEY, restoreBegan, type Watch, watch,
} from "./offsite.ts";
import {
  type Act, type Ask, askOperator, askOwners, controlEdits, differences, type Edit, edited,
  type Generation, ERASE_LOSES, heldRotation, keyHashes, ownerAct, pages, RESTORE_LOSES,
  restoreFrom, rotate, type RotationRecord, rotationKey, rotationRecord, RUN_MS, WAIT_MS,
} from "./operator.ts";
/**
 * The control record a first deploy writes when there is none: every outside door closed, and
 * the founder's tier numbers. The founder's data, so a file of its own, which the tests read too.
 */
import DARK_CONTROL from "./control.json" with { type: "json" };

type Json = Record<string, unknown>;
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/**
 * E2B's sandbox lifecycle webhook, the doors' one E2B hook (`doors/src/e2b-hooks.ts`), for
 * the events it takes (`contract/src/identity.ts`), its API read in e2b-dev/infra's
 * `spec/openapi.yml` (2026-11-04).
 */
export const E2B_HOOK = Object.freeze({
  name: "tinyassets-lifecycle", url: E2B_HOOK_URL,
  events: E2B_HOOK_EVENTS,
  /** Its signature secret, which the Worker whose config requires it holds a copy of. */
  secret: "E2B_WEBHOOK_SECRET",
});
/** What the plan job says of a check that needs a production secret. */
const LATER = "checked by the apply job before any change";

/**
 * A line whose end only the apply job can read: `subject`, then `outcome`, or in the plan job
 * (`outcome` null) `LATER`. The apply job holds its line to the plan job's by everything
 * before that end (`resolves`).
 */
function checked(subject: string, outcome: string | null): string {
  return `${subject}: ${outcome ?? LATER}`;
}

/**
 * Whether `said`, a line of the apply job, is the plan's `approved` line: the same, or the
 * same subject with the outcome the plan job left to the apply job (`checked`), which the apply
 * job always reads.
 */
function resolves(approved: string, said: string): boolean {
  const later = `: ${LATER}`;
  if (said.endsWith(later)) {
    return false;
  }
  const subject = approved.slice(0, -LATER.length);
  return said === approved || (approved.endsWith(later) && said.length > subject.length
    && said.startsWith(subject));
}

// --- The run ----------------------------------------------------------------------------

/** The outside world the kit acts on: replaced whole by the tests. */
export interface Io {
  readonly fetch: Fetch;
  readonly spawn: typeof spawnSync;
  readonly exec: typeof execFileSync;
  /** The runner and the probes this tree builds, and its image (`sandbox/template/pair.ts`). */
  readonly bundles: () => Bundles;
  readonly treeImage: () => string;
  /** The commit the checkout is at, which each Worker's version is tagged with. */
  readonly commit: () => string;
  /**
   * The text of a repo-relative file at an earlier commit, or null when its tree lacks it; an
   * unknown commit throws (`ci/history.ts` `fileAt`: the plan and apply jobs fetch history).
   */
  readonly fileAt: (commit: string, path: string) => string | null;
  readonly log: (line: string) => void;
  /** The clock: a route record's time, and how long the operator's answer is waited for. */
  readonly now: () => Date;
  readonly sleep: (ms: number) => Promise<void>;
}

export const IO: Io = Object.freeze({
  fetch: (url: string, init?: RequestInit) => fetch(url, init),
  spawn: ((...args: Parameters<typeof spawnSync>) => spawnSync(...args)) as typeof spawnSync,
  exec: ((...args: Parameters<typeof execFileSync>) => execFileSync(...args)) as
    typeof execFileSync,
  bundles,
  treeImage,
  commit: () => execFileSync("git", ["rev-parse", "HEAD"], { cwd: PLATFORM, encoding: "utf8" })
    .trim(),
  fileAt: (commit: string, path: string) => fileAt(commit, path),
  log: (line: string) => console.log(line),
  now: () => new Date(),
  sleep: (ms: number) => new Promise<void>((done) => { setTimeout(done, ms); }),
});

export type Mode = "plan" | "dark" | "routes" | "h2" | "pair" | "handback" | "rollback"
  | "control" | "owner" | "restore" | "copyback" | "recover" | "erase" | "rotation" | "retire";

/** One run, as `parse` reads it: its mode, how far it reaches, and its inputs. */
export interface Run {
  readonly mode: Mode;
  /** `--public`: the plan job's, which holds the Cloudflare token alone. */
  readonly reach: "public" | "secrets";
  /** `--yes`: the apply job's, which changes what its plan says. */
  readonly apply: boolean;
  readonly rotate: ReadonlySet<string>;
  readonly migrate: ReadonlySet<string>;
  /**
   * `--plan`: the plan's file, which the plan job (`--public`) writes and the apply job
   * (`--yes`) is held to.
   */
  readonly plan: string | null;
  /** The workflow run this is (`actions.ts` `runOf`), read once, as the run began. */
  readonly job: JobRun;
  /**
   * `--record`: the run id of the route record `handback` hands back, or of the rollout record
   * `rollback` rolls back.
   */
  readonly record: string | null;
  /** `--set`: `control`'s edits of the control record, in order. */
  readonly edits: readonly Edit[];
  /** `--replace`: the whole record `control`'s edits start from in place of the live one. */
  readonly replace: unknown;
  /** `--owner` and `--act`: the owner `owner` asks the doors an op of, and the op. */
  readonly owner: string | null;
  readonly act: Act | null;
  /** `--generation`: the generation `restore` restores `--owner` from; none asks its source. */
  readonly generation: Generation | null;
  /** `--confirm`: the words that confirm `restore`'s wipe, give its pin up, `erase` or `retire`. */
  readonly confirm: string | null;
  /** `--keyring`: the keyring `rotation` sweeps, or `retire` retires the old kids of. */
  readonly keyring: string | null;
  /**
   * `--at`: the time `copyback` and `recover` read the off-account copy as it stood at; none,
   * as it stands.
   */
  readonly at: Date | null;
  /** The environment as the run began: its inputs, and what the tools it runs are given. */
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly io: Io;
}

/**
 * Every value a step makes for the steps after it, by name. Each step's type names the values
 * it takes and the ones it makes, so a row whose step takes a value no step before it makes
 * does not compile (`Steps`).
 */
export interface Values {
  /** The zone's: Cloudflare's client, the zone's id and account, and wrangler for it. */
  readonly cloudflare: Cloudflare;
  readonly zone: string;
  readonly account: string;
  readonly wrangler: Wrangler;
  /** The doors' upstream, from the owner issuer's live discovery. */
  readonly upstream: UpstreamValues;
  /** The run's one E2B client, from its key. */
  readonly e2b: E2bAdmin;
  /** E2B's listing of the lifecycle hook: null when it holds none. */
  readonly hook: Json | null;
  /**
   * Whether WorkOS is known to list `UPSTREAM_CALLBACK`: never in the plan job, which cannot
   * list it.
   */
  readonly listed: boolean;
  /** The zone's routes as the route plan read them, before any change. */
  readonly table: readonly Route[];
  /** The route changes the run's handover makes, from those. */
  readonly changes: readonly RouteChange[];
  /**
   * Whether the doors hold every route of theirs before any change: a handover of the zone's
   * routes would change nothing.
   */
  readonly routed: boolean;
  /** The doors' host's DNS records before any change (`placedBefore`): null for the zone. */
  readonly placed: readonly DnsRecord[] | null;
  /** The route record `routes` keeps, and its key: null on a run that changes nothing. */
  readonly kept: KeptRecord | null;
  /** The KV namespaces' ids, by binding: the account's, and those this run made. */
  readonly ids: Readonly<Record<string, string>>;
  /** Each Worker's config as it deploys. */
  readonly stage: Stage;
  /** The account's Workers, by name, as its listing answered before any deploy. */
  readonly deployed: readonly string[];
  /** The secrets the run's plan sets, each on its Worker, with the version the run deploys. */
  readonly setting: readonly Setting[];
  /** Each migrated Worker's locks' counts, by lock path (`lockCounts`). */
  readonly migrations: Readonly<Record<string, Readonly<Record<string, number>>>>;
  /**
   * Each Worker as the rollout step read it before any deploy, in deploy order (`WORKERS`):
   * the version it serves, and what the run's version carries (`Rolled`).
   */
  readonly rolled: readonly Rolled[];
  /** The route record `handback` reads, and its handback on the zone as it stands. */
  readonly record: RouteRecord;
  readonly back: readonly RouteChange[];
  /** The host's route records the handback ends, in the order it deletes them (`recordRead`). */
  readonly ending: readonly KeptRecord[];
  /** The doors' custom domains on the egress host the handback detaches. */
  readonly detach: readonly { id: string; service: string }[];
}

/**
 * The run's one reporter: its plan, the job's summary and the masks in its log, each through
 * `actions.ts`, which holds what is GitHub's.
 */
export interface Reporter {
  /**
   * Adds lines to the run's plan, each said before the change it names: on the apply job each
   * is held to the approved plan's as it is said (`holding`).
   */
  readonly say: (...lines: string[]) => void;
  /**
   * Adds lines of what a change showed once made, which no plan can hold (a line naming the
   * run's own record, or what Cloudflare made): reported with the plan, never held to it.
   */
  readonly did: (...lines: string[]) => void;
  /**
   * Masks `value` in the job's log from here on: not a credential, but once a secret, and a
   * wrangler error the kit quotes names `/accounts/<id>/...` in a public log.
   */
  readonly mask: (value: string) => void;
  /** Adds `text` to the job's summary at once, so a run that stops later still shows it. */
  readonly summarize: (text: string) => void;
}

/** What a step is given beside the values: its run, the reporter, and a folder to write in. */
export interface Context extends Reporter {
  readonly run: Run;
  /** A folder outside the checkout, made when first asked for; the run removes it at its end. */
  readonly scratch: () => string;
}

type Made<Out extends keyof Values> = [Out] extends [never] ? void : Pick<Values, Out>;

/** One step of a row: from the values it takes (`In`) to the values it makes (`Out`). */
export interface Step<In extends keyof Values, Out extends keyof Values> {
  readonly run: (ctx: Context, got: Pick<Values, In>) => Made<Out> | Promise<Made<Out>>;
}

/** A step as a row holds it, its values' names checked when the row was built (`Steps`). */
interface Held {
  readonly run: (ctx: Context, got: Pick<Values, never>) =>
    Partial<Values> | void | Promise<Partial<Values> | void>;
}

/** A step taking the values `In` names and making those `Out` names: each named, never inferred. */
function step<In extends keyof Values = never, Out extends keyof Values = never>(
    run: (ctx: Context, got: Pick<Values, NoInfer<In>>) =>
      Made<NoInfer<Out>> | Promise<Made<NoInfer<Out>>>): Step<In, Out> {
  return Object.freeze({ run });
}

/** A row's steps in order, and the values they make (`Have`). */
export class Steps<Have extends keyof Values> {
  static readonly none = new Steps<never>([]);
  readonly list: readonly Held[];

  private constructor(list: readonly Held[]) {
    this.list = list;
  }

  /** These steps, then `next`, which takes only values these make. */
  then<In extends Have, Out extends keyof Values>(next: Step<In, Out>): Steps<Have | Out> {
    return new Steps<Have | Out>([...this.list, next as unknown as Held]);
  }
}

function input(run: Run, name: string): string | undefined {
  return set(run.env, name);
}

/**
 * The apply job's hold on its own plan: each line its steps say, as it is said and so before
 * the change it names, is the approved plan's next line, or that line with the outcome the plan
 * job left to the apply job (`resolves`); once every approved line is said, what the run says
 * after it is what it did (an operator mode's answers, a handback's read-back). Any other line
 * stops the run there, and a run that ends before the approved plan's last line stops loudly.
 */
export interface Hold {
  readonly next: (line: string) => void;
  readonly done: () => void;
}

export function holding(approved: readonly string[]): Hold {
  let at = 0;
  return Object.freeze({
    next: (line: string) => {
      if (at === approved.length) {
        return;
      }
      if (!resolves(approved[at]!, line)) {
        throw new Error(`this job's line ${at + 1} is not the approved plan's, so it stops before ` +
                        `that change${at === 0 ? "" : ", having made no change but what the " +
                          "approved lines before it name"}: ` +
                        `approved "${approved[at]}", and this job's "${line}"`);
      }
      at += 1;
    },
    done: () => {
      if (at < approved.length) {
        throw new Error(`this job ended before the approved plan's line ${at + 1} ("${
          approved[at]}"): what it applied is not the plan approved`);
      }
    },
  });
}

/** The reporter of `run`, its plan's lines gathered in `plan`, each held by `hold` when given. */
function reporterOf(run: Run, plan: string[], hold: Hold | null = null): Reporter {
  return Object.freeze({
    say: (...lines: string[]) => {
      for (const line of lines) {
        hold?.next(line);
        plan.push(line);
      }
    },
    did: (...lines: string[]) => { plan.push(...lines); },
    mask: (value: string) => {
      const line = maskLine(run.env, value);
      if (line !== null) {
        run.io.log(line);
      }
    },
    summarize: (text: string) => summarize(run.env, text),
  });
}

/**
 * The plan as the run reports it, under the run's id, which no line holds: printed, and added
 * to the job's summary.
 */
function report({ summarize: add }: Reporter, plan: readonly string[], run: Run): void {
  const text = `Run ${run.job.id} ${run.apply ? "changed" : "would change"}:\n${plan.map((line) =>
    `- ${line}`).join("\n")}`;
  run.io.log(text);
  add(`${text}\n`);
}

// --- The migration locks ----------------------------------------------------------------

/**
 * How many migrations each of `worker`'s locks pins, by its path, or a throw naming why it
 * may not deploy: the locks' command line (`ci/kit/locks.ts digests`) refuses unless each
 * lock marks the first deploy and pins exactly the migrations the code ships, in order, and
 * unless the Worker states a lock.
 */
export function lockCounts(worker: string, run: Run): Record<string, number> {
  const result = run.io.spawn(process.execPath,
                              [join(PLATFORM, "ci", "kit", "locks.ts"), "digests", worker],
                              { cwd: PLATFORM, encoding: "utf8", env: {} });
  if (result.status !== 0) {
    throw new Error(`${worker}'s migration locks refuse the deploy (exit ${result.status}): ` +
                    String(result.stderr).trim());
  }
  const digests = JSON.parse(String(result.stdout)) as Record<string, unknown>;
  return Object.fromEntries(Object.entries(digests).map(([path, pinned]) => {
    if (!Array.isArray(pinned)) {
      throw new Error(`the migration locks' digests of ${worker} name ${path} with no list`);
    }
    return [path, pinned.length];
  }));
}

/**
 * A version's tag and message (`wrangler versions upload`, `wrangler deploy`): the tag, the
 * commit's first 12 digits and, for a Worker with migration locks, `-m` and each lock's
 * count in its path's order (`-m3`, `-m2.1`), which a rollback below it would strand, then,
 * when the Worker's forward count (`FORWARD`) is above 0, `-f` and that count, which a
 * rollback below it could not follow; the message, the whole commit.
 */
export function versionFlags(commit: string, counts: Readonly<Record<string, number>>,
                             forward: number): string[] {
  const pinned = pinnedCounts(counts);
  return ["--tag", `${tagCommit(commit)}${pinned.length === 0 ? "" : `-m${pinned.join(".")}`}${
    forward === 0 ? "" : `-f${forward}`}`, "--message", commit];
}

/** The part of `commit` a version's tag names it by: its first 12 digits. */
function tagCommit(commit: string): string {
  return commit.slice(0, 12);
}

/** Each lock's count, in its path's order: a tag's `-m` part. */
function pinnedCounts(counts: Readonly<Record<string, number>>): number[] {
  return Object.keys(counts).sort().map((path) => counts[path]!);
}

/**
 * A version's tag as `versionFlags` writes it, read back: the commit's digits it names, the
 * counts after its `-m` (null when it names none), and its forward count (0 when it names
 * none). Null for a tag it did not write.
 */
function taggedAs(tag: string | null):
    { commit: string; counts: number[] | null; forward: number } | null {
  const parts = tag === null ? null
    : /^([0-9a-f]{12})(?:-m(\d+(?:\.\d+)*))?(?:-f([1-9]\d*))?$/.exec(tag);
  return parts === null ? null
    : { commit: parts[1]!, counts: parts[2] === undefined ? null : parts[2].split(".").map(Number),
        forward: Number(parts[3] ?? 0) };
}

// --- WorkOS and the owner issuer --------------------------------------------------------

/** The doors' upstream: the issuer, and its `UPSTREAM` record. */
export interface UpstreamValues {
  readonly issuer: string;
  readonly record: UpstreamConfig;
}

/**
 * The doors' `UPSTREAM` record, held to the parser the doors read it with
 * (`contract/src/signin.ts`), so a record the doors would refuse stops the kit before any
 * deploy: User Management's sign-in for the environment's client (ADR-032), its authorize
 * and authenticate endpoints fixed WorkOS URLs, and the JWKS the owner issuer's discovery
 * names, that discovery held to `issuer` exactly (an absent or non-https `jwks_uri` is
 * unknown, and unknown stops the kit). The access token's expected `iss` is derived here
 * alone (`token_issuer`); a wrong one is the sign-in's 502, which names the issuer the
 * token carries.
 */
export async function upstreamFrom(issuer: string | undefined, clientId: string | undefined,
                                   fetcher: Fetch): Promise<UpstreamValues> {
  if (typeof issuer !== "string" || !/^https:\/\/\S+$/.test(issuer)) {
    throw new Error("OWNER_ISSUER is the owner issuer's https URL");
  }
  if (typeof clientId !== "string" || clientId === "") {
    throw new Error("WORKOS_CLIENT_ID is not set");
  }
  if (!/^[A-Za-z0-9_-]{1,256}$/.test(clientId)) {
    throw new Error("WORKOS_CLIENT_ID is a WorkOS client id (letters, digits, _ and -)");
  }
  const url = `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
  const response = await fetcher(url, { headers: { Accept: "application/json" } });
  if (response.status !== 200) {
    throw new Error(`${url} answered ${response.status}`);
  }
  const discovery = await response.json() as Json;
  if (discovery["issuer"] !== issuer) {
    // Any other spelling refuses every sign-in: stop before deploying it.
    throw new Error(`the issuer's discovery names ${JSON.stringify(discovery["issuer"])}, ` +
                    `not OWNER_ISSUER ${JSON.stringify(issuer)}`);
  }
  const jwks = discovery["jwks_uri"];
  if (typeof jwks !== "string" || !/^https:\/\/\S+$/.test(jwks)) {
    throw new Error("the issuer's discovery names no https jwks_uri: unknown");
  }
  const record = { client_id: clientId,
                   authorization_endpoint: `${SECRET_ORIGIN}/user_management/authorize`,
                   token_endpoint: `${SECRET_ORIGIN}/user_management/authenticate`,
                   jwks_uri: jwks,
                   // WorkOS's docs and SDK fixtures (research, 2026-11-06); the stage's first sign-in proves it.
                   token_issuer: `${SECRET_ORIGIN}/user_management/${clientId}`,
                   user_delete: `${SECRET_ORIGIN}/user_management/users/{sub}`,
                   authorize_params: { provider: "authkit" } };
  return { issuer, record: upstreamConfig(JSON.stringify(record)) };
}

/** Where WorkOS sends a browser whose `redirect_uri` the client does not list. */
const REDIRECT_UNLISTED = "https://error.workos.com/redirect-uri-invalid";

/**
 * The early sign-in check, before any person signs in: a `GET` of the WorkOS authorize URL
 * the doors build (`authorizeUrl`, User Management's; never the doors' own `/authorize`)
 * with a throwaway state and S256 challenge (its verifier dropped, so no code it could end
 * in is redeemable), which must answer a 302 to the owner issuer's host, AuthKit's sign-in
 * page. A refused client or redirect (`error.workos.com/sso/client-id-invalid`,
 * `…/redirect-uri-invalid`, an `error=` answer) stops the kit, naming where it was sent.
 *
 * `unlisted`: `UPSTREAM_CALLBACK` is not known to be on the client's list yet (the plan job cannot
 * list it, and a run without `--yes` leaves it missing), so a `redirect-uri-invalid` answer
 * is left to the apply job (`checked`), which registers it and then checks again, not a stop;
 * every other refusal still stops.
 */
export async function authorizeCheck(upstream: UpstreamValues, unlisted: boolean,
                                     fetcher: Fetch): Promise<string> {
  const { state, challenge } = throwaway();
  const link = authorizeUrl(upstream.record, { state, code_challenge: challenge,
                                              redirect_uri: UPSTREAM_CALLBACK });
  const answer = await fetcher(link, { redirect: "manual" });
  const location = answer.headers.get("Location");
  if (answer.status !== 302 || location === null) {
    throw new Error(`WorkOS's authorize answered ${answer.status}, not a redirect to the ` +
                    "sign-in page");
  }
  let to: URL;
  try {
    to = new URL(location, link);
  } catch {
    throw new Error("WorkOS's authorize redirected to no URL");
  }
  const host = new URL(upstream.issuer).host;
  const subject = `WorkOS's authorize of the doors' client and ${UPSTREAM_CALLBACK}`;
  if (unlisted && `${to.origin}${to.pathname}` === REDIRECT_UNLISTED) {
    return checked(subject, null);
  }
  if (to.protocol !== "https:" || to.host !== host || to.searchParams.has("error")
      || /error/i.test(to.pathname)) {
    throw new Error(`WorkOS's authorize refused the doors' client or ${UPSTREAM_CALLBACK}: it ` +
                    `sent the browser to ${to.origin}${to.pathname}, not ${host}'s sign-in`);
  }
  return checked(subject, `it takes them, sending the browser to ${host}'s sign-in`);
}

/**
 * The WorkOS redirect URI `UPSTREAM_CALLBACK`, registered when missing: GET, then POST; never
 * a delete.
 */
export async function registerRedirect(key: string, apply: boolean, fetcher: Fetch):
    Promise<"present" | "missing" | "registered"> {
  const headers = { Authorization: `Bearer ${key}`, "Content-Type": "application/json" };
  const listed = await fetcher(`${SECRET_ORIGIN}/user_management/redirect_uris`, { headers });
  if (listed.status !== 200) {
    throw new Error(`WorkOS listed its redirect URIs with ${listed.status}`);
  }
  const { data } = await listed.json() as { data?: unknown };
  if (!Array.isArray(data)) {
    throw new Error("WorkOS's redirect URI listing holds no list");
  }
  if (data.some((row) => (row as Json | null)?.["uri"] === UPSTREAM_CALLBACK)) {
    return "present";
  }
  if (!apply) {
    return "missing";
  }
  const made = await fetcher(`${SECRET_ORIGIN}/user_management/redirect_uris`, {
    method: "POST", headers, body: JSON.stringify({ uri: UPSTREAM_CALLBACK }) });
  if (made.status !== 200 && made.status !== 201) {
    throw new Error(`WorkOS refused the redirect URI with ${made.status}`);
  }
  return "registered";
}

// --- E2B: the pair and the lifecycle webhook --------------------------------------------

/**
 * The pair this checkout deploys: the pinned template, the runner the sandbox Worker ships
 * and the probes beside it, built as its build makes them (`sandbox/template/pair.ts`).
 */
function shippedPair(io: Io): string {
  const { runner, probes } = io.bundles();
  return pairTag(pinned(), runner.sha, probes.sha);
}

/**
 * Whether this run sets E2B's copy of the webhook secret: only when it sets the doors' copy
 * (missing on the doors, or named in `--rotate`), so the two never drift apart.
 */
export function hookSetsSecret(setting: readonly Setting[]): boolean {
  return setting.some((each) => each.name === E2B_HOOK.secret && each.sets === true);
}

/**
 * E2B's lifecycle webhook as its listing holds it, found by its name, whatever URL it is sent
 * to, or null when it holds none: so a hook the identity's host has moved off is the one
 * moved, never left beside a second. A failed listing, or two hooks of the name, throws.
 */
export async function heldHook(e2b: E2bAdmin): Promise<Json | null> {
  const hooks = await e2b.webhooks();
  const ours = hooks.filter((hook) => (hook as Json | null)?.["name"] === E2B_HOOK.name) as Json[];
  if (ours.length > 1) {
    throw new Error(`E2B holds ${ours.length} webhooks named ${E2B_HOOK.name}: keep one`);
  }
  return ours[0] ?? null;
}

/** What a run asks of E2B's lifecycle webhook (`lifecycleHook`). */
export interface HookWanted {
  /** The signature secret a missing hook is registered with, a held one given on `setSecret`. */
  readonly secret: string;
  /** Whether this run also sets the doors' E2B_WEBHOOK_SECRET (`hookSetsSecret`). */
  readonly setSecret: boolean;
  /** Whether it is enabled (mode `routes`). */
  readonly enable: boolean;
}

/**
 * E2B's lifecycle webhook (`heldHook`). Missing, it is registered with its secret, disabled
 * unless `enable`; the run that does so sets the doors' copy of the secret too (the secrets
 * step). Held, it is changed only where the run asks: its signature secret only with
 * `setSecret`, when this run also sets the doors' E2B_WEBHOOK_SECRET (E2B never shows a
 * hook's secret, so the two change together or not at all); its URL and its events when
 * they differ from the identity's; and it is enabled with `enable`. It is never deleted, and
 * never disabled here: only a handback disables it, back to the state its route record kept. Without `apply` it only
 * lists and checks. A failed listing or write throws.
 */
export async function lifecycleHook(e2b: E2bAdmin, { secret, setSecret, enable }: HookWanted,
                                    apply: boolean): Promise<string> {
  const hook = await heldHook(e2b);
  if (hook === null) {
    const state = enable ? "enabled" : "disabled";
    if (!apply) {
      return `missing: registered, ${state}`;
    }
    await e2b.addWebhook({ name: E2B_HOOK.name, url: E2B_HOOK.url, events: [...E2B_HOOK.events],
                           enabled: enable, signatureSecret: secret });
    return `registered, ${state}`;
  }
  const events = Array.isArray(hook["events"]) ? [...hook["events"] as string[]].sort() : [];
  const change: Json = {
    ...(setSecret ? { signatureSecret: secret } : {}),
    ...(hook["url"] !== E2B_HOOK.url ? { url: E2B_HOOK.url } : {}),
    ...(JSON.stringify(events) !== JSON.stringify([...E2B_HOOK.events].sort())
      ? { events: [...E2B_HOOK.events] } : {}),
    ...(enable && hook["enabled"] !== true ? { enabled: true } : {}),
  };
  const state = hook["enabled"] === true || enable ? "enabled" : "disabled";
  const what = Object.keys(change).length === 0 ? "unchanged"
    : `${Object.keys(change).map((field) => field === "signatureSecret" ? "its secret"
      : field === "url" ? `its URL (${String(hook["url"])} before)`
      : field === "events" ? "its events" : "enabled").join(", ")} set`;
  if (!apply || Object.keys(change).length === 0) {
    return `present, ${what}, ${state}`;
  }
  await e2b.changeWebhook(String(hook["id"]), change);
  return `present, ${what}, ${state}`;
}

// --- Secrets ----------------------------------------------------------------------------

/**
 * A keyring as the gateway and the files Worker read it (`contract/src/envelope.ts`
 * `parseRing`), the secret it is held as named in its refusal: a ring either Worker would
 * refuse stops the deploy before any change.
 */
export function keyring(text: string | undefined, name: string): Ring {
  const ring = parseRing(text);
  if (ring === null) {
    throw new Error(`${name} is {active, keys} as JSON, each key 32 bytes in base64, its ` +
                    "active key among its keys");
  }
  return ring;
}

/**
 * What to set: each Worker's missing secrets, and those `rotate` names (the shared routing
 * key on both or neither; a keyring only as an addition). `held` is each Worker's listing,
 * empty for a Worker the account's Workers listing lacks.
 */
export function secretPlan(held: Readonly<Record<string, readonly string[]>>,
                           rotate: ReadonlySet<string>,
                           values: ((name: string) => string | undefined) | null):
    { worker: string; name: string }[] {
  const has = (worker: string, name: string) => (held[worker] ?? []).includes(name);
  for (const name of SHARED) {
    const holders = Object.keys(SECRETS).filter((worker) => SECRETS[worker]!.includes(name));
    if (new Set(holders.map((worker) => has(worker, name))).size > 1 && !rotate.has(name)) {
      throw new Error(`only one of ${holders.map((worker) => `the ${worker}`).join(" and ")} ` +
                      `holds ${name}: pass --rotate ${name} to set both again`);
    }
  }
  // `values` null: the plan job, which holds no keyring; the apply job checks the ring.
  for (const name of KEYRINGS) {
    if (rotate.has(name) && values !== null) {
      const ring = keyring(values(name), name);
      if (ring.keys.size < 2) {
        throw new Error(`a keyring is rotated by adding a key: the new ${name} holds ` +
                        "the old keys and a new active one (keyring-add)");
      }
    }
  }
  const plan: { worker: string; name: string }[] = [];
  for (const [worker, names] of Object.entries(SECRETS)) {
    for (const name of names) {
      if (!has(worker, name) || rotate.has(name)) {
        plan.push({ worker, name });
      }
    }
  }
  return plan;
}

// --- Wrangler ---------------------------------------------------------------------------

/** One wrangler command, its stdin given: its stdout, or a throw. */
export type Wrangler = (args: string[], stdin?: string) => string;

/** Wrangler in the platform's folder, for the zone's `account`, with the run's environment. */
export function wranglerFor(run: Run, account: string): Wrangler {
  return (args, stdin) => {
    const result = run.io.spawn(process.execPath, [WRANGLER, ...args], {
      cwd: PLATFORM, input: stdin, encoding: "utf8",
      env: { ...run.env, CLOUDFLARE_ACCOUNT_ID: account, WRANGLER_SEND_METRICS: "false" },
    });
    if (result.status !== 0) {
      // Its stderr alone: wrangler never echoes a secret's value.
      throw new Error(`wrangler ${args.slice(0, 3).join(" ")} failed (exit ${result.status}): ` +
                      `${String(result.stderr).trim()}`);
    }
    return String(result.stdout);
  };
}

/**
 * What wrangler 4.154 prints before the id of the version each command makes, `versions
 * upload`'s and `deploy`'s (`deploy-helpers`' `versions-upload.ts` and `deploy.ts`).
 */
const PRINTS = Object.freeze({ "versions upload": "Worker Version ID",
                               deploy: "Current Version ID" });

/** The version id `made` printed (`PRINTS`), or a throw. */
export function versionOf(output: string, made: keyof typeof PRINTS): string {
  const id = new RegExp(`${PRINTS[made]}:\\s*([0-9a-f-]{36})`).exec(output)?.[1];
  if (id === undefined) {
    throw new Error(`wrangler ${made} printed no ${PRINTS[made]}`);
  }
  return id;
}

/**
 * The deployed config of `worker`: its checked-in config with the namespace ids it names
 * filled and, on the Worker its record gives the upstream (`WORKER_FACTS`), the upstream's
 * values set; nothing else changes, so what the guards hold is what deploys. It is written
 * outside the Worker's folder, so its paths are made absolute: `main` and the doors' app
 * files' `assets.directory`, which wrangler reads relative to the config file, and the
 * folder the sandbox's custom build runs in (`build.cwd`), which is otherwise wrangler's own
 * working directory.
 */
export function deployedConfig(worker: string, ids: Readonly<Record<string, string>>,
                               upstream: UpstreamValues): Json {
  const config = structuredClone(CONFIGS.get(worker)) as Json;
  config["main"] = join(PLATFORM, worker, String(config["main"]));
  const assets = config["assets"] as Json | undefined;
  if (assets !== undefined) {
    assets["directory"] = join(PLATFORM, worker, String(assets["directory"]));
  }
  const build = config["build"] as Json | undefined;
  if (build !== undefined) {
    build["cwd"] = join(PLATFORM, worker, String(build["cwd"] ?? "."));
  }
  delete config["$schema"];
  for (const kv of (config["kv_namespaces"] ?? []) as { binding: string; id: string }[]) {
    const id = ids[kv.binding];
    if (id === undefined) {
      throw new Error(`${worker}: no namespace for ${kv.binding}`);
    }
    kv.id = id;
  }
  if (WORKER_FACTS[worker]!.upstream) {
    config["vars"] = { ...(config["vars"] as Json), OWNER_ISSUER: upstream.issuer,
                       UPSTREAM: JSON.stringify(upstream.record) };
  }
  return config;
}

/**
 * The same with the routes its record gives it (mode `routes`): what `wrangler triggers
 * deploy` reads, and what `wrangler deploy` must never see, since it would attach them.
 */
export function routedConfig(worker: string, ids: Readonly<Record<string, string>>,
                             upstream: UpstreamValues): Json {
  return { ...deployedConfig(worker, ids, upstream), routes: WORKER_FACTS[worker]!.routes };
}

/** Each Worker's config as it deploys, by folder, written when first asked for (a plan: never). */
export interface Stage {
  /** The config `worker` deploys with (`deployedConfig`). */
  readonly config: (worker: string) => string;
  /** Its config with its routes (`routedConfig`). */
  readonly routed: (worker: string) => string;
}

/** The stage over `ids` and `upstream`, writing in `scratch()`, a folder outside the checkout. */
function stageOf(scratch: () => string, ids: Readonly<Record<string, string>>,
                 upstream: UpstreamValues): Stage {
  const write = (worker: string, file: string, config: Json): string => {
    mkdirSync(join(scratch(), worker), { recursive: true });
    const path = join(scratch(), worker, file);
    writeFileSync(path, JSON.stringify(config, null, 2));
    return path;
  };
  const written = new Map<string, string>();
  return Object.freeze({
    config: (worker: string) => written.get(worker) ?? written.set(worker, write(
      worker, "wrangler.json", deployedConfig(worker, ids, upstream))).get(worker)!,
    routed: (worker: string) => write(worker, "routed.json", routedConfig(worker, ids, upstream)),
  });
}

/**
 * The doors' own first-party sign-in link, which the platform's canary starts: the doors'
 * app's authorization (`doors/app/api.js`), with a fresh S256 challenge whose verifier is
 * dropped, so no code it could end in is ever redeemable.
 */
export function firstPartyLink(): string {
  const { state, challenge } = throwaway();
  const query = new URLSearchParams({
    response_type: "code", client_id: WEB_CLIENT, redirect_uri: WEB_REDIRECT,
    state, code_challenge: challenge, code_challenge_method: "S256",
    resource: APP_RESOURCE });
  return `${AUTHORIZE}?${query}`;
}

/**
 * A fresh state and S256 challenge, the challenge's verifier dropped here, so no code a flow
 * started with them could end in is ever redeemable (`firstPartyLink`, `authorizeCheck`).
 */
export function throwaway(): { state: string; challenge: string } {
  return { state: randomBytes(16).toString("base64url"),
           challenge: createHash("sha256").update(randomBytes(32).toString("base64url"))
             .digest("base64url") };
}

// --- The route record -------------------------------------------------------------------

/**
 * What `routes` keeps before it changes anything, so `handback` can undo it (the import
 * research's rollback, Fact 13): the before-state of every outward change `routes` makes that
 * `dark` does not, and the zone, the run and its time. Those changes, from its row:
 * - the zone's routes (`handOver`, and `customDomain`'s `wrangler triggers deploy`, which sets
 *   the doors' routes to the config's, the same patterns): every route as the route plan read
 *   it, each pattern and its Worker, and the changes the handover makes;
 * - the doors' host's DNS record (`hostRecord`): whether the host held one before (the zone's
 *   own, the site's, or a placeholder, `PLACEHOLDER`), and, written once it is made, the one
 *   `routes` made for a host under the zone that held none (`made`), so a handback deletes
 *   exactly it. `routes` stops before any change while any other address record holds the
 *   host: the routes would take its requests, and no handback could give them back;
 * - the egress custom domain (`customDomain`): the Worker it served, or none, and, written once
 *   the attach is done, the address records it made (`created`), so a handback removes exactly
 *   those if the detach leaves them. `routes` stops before any change while it is another
 *   Worker's or an address record (A, AAAA, CNAME) holds the host, since wrangler's deploy
 *   overrides both outside a terminal (`publishCustomDomains`, `override_existing_origin` and
 *   `override_existing_dns_record`) and no handback could give either back;
 * - E2B's lifecycle webhook (`hook(true)`): whether it was held and enabled. Its secret and
 *   events are not kept (no secret is): a handback disables it, and a disabled hook sends
 *   nothing.
 * The rest of the row is `dark`'s (storage, secrets, the Workers, the control record, the
 * WorkOS redirect URI), which `routes` runs only after a `dark` run (it needs the control
 * namespace): it serves no one without the routes, the domain and the hook, so a handback
 * leaves it, as `dark` has no undo. The record holds no secret and not the account id. It is
 * kept in the control namespace, the
 * kit's own record store (the control record is written there): the kit writes it itself, before its first change, over the client and token it
 * already holds, so a refused write stops the run before anything is changed, and `handback`
 * reads it with that same token, with no host and no GitHub API in between. Its key is the
 * run's id (`actions.ts` `runOf`), and the job summary names it. It is the handover's: every
 * later routes run on the host, a re-run attempt or not, keeps it while it stands
 * (`keepRecord`), and the handback that names it deletes it (`recordsEnded`).
 */
export interface RouteRecord {
  readonly kind: typeof RECORD_KIND;
  readonly zone: string;
  readonly zone_id: string;
  readonly run: string;
  readonly attempt: string | null;
  readonly at: string;
  readonly routes: readonly Kept[];
  readonly moved: readonly Moved[];
  /**
   * The egress host and the Worker its custom domain served, or null for none; and, once a
   * run that attached it has read them, the host's address records the attach made (null
   * until then).
   */
  readonly domain: { readonly hostname: string; readonly service: string | null;
                     readonly created: readonly DnsRecord[] | null };
  /**
   * The doors' host; whether it held its DNS record before routes; and, once the run that
   * made one has written it in, the record it made (null until then, and for a host that
   * held one).
   */
  readonly host: { readonly name: string; readonly held: boolean;
                   readonly made: readonly DnsRecord[] | null };
  /** E2B's lifecycle webhook: whether it was held, and enabled. */
  readonly hook: { readonly url: string; readonly present: boolean; readonly enabled: boolean };
}

const RECORD_KIND = "tinyassets-routes";
/** A route record's key in the control namespace, after its run's id. */
const RECORD_PREFIX = "deploy:routes:";
const RECORD_ID = /^[A-Za-z0-9-]{1,64}$/;

/**
 * The record the run `job` keeps at `at` of `table` and its handover `steps` on the zone
 * `zoneId`, the egress domain's Worker `domain`, the host's DNS records `placed`
 * (`placedBefore`) and E2B's `hook` as listed.
 */
function routeRecord(job: JobRun, at: Date, zoneId: string, table: readonly Route[],
                     steps: readonly RouteChange[], domain: string | null,
                     placed: readonly DnsRecord[] | null, hook: Json | null): RouteRecord {
  return {
    kind: RECORD_KIND, zone: ZONE, zone_id: zoneId, run: job.id, attempt: job.attempt,
    at: at.toISOString(),
    routes: table.map((route) => ({ pattern: route.pattern, script: route.script ?? null })),
    moved: steps.map((each) => ({ method: each.method, pattern: each.pattern })),
    domain: { hostname: EGRESS_HOST, service: domain, created: null },
    host: { name: HOST, held: placed === null || placed.length > 0, made: null },
    hook: { url: E2B_HOOK.url, present: hook !== null, enabled: hook?.["enabled"] === true },
  };
}

/** Whether `value` is a list of DNS records as a route record keeps them. */
function dnsList(value: unknown): value is DnsRecord[] {
  return Array.isArray(value) && (value as Partial<DnsRecord>[]).every((each) =>
    typeof each?.id === "string" && typeof each.type === "string"
    && typeof each.content === "string");
}

/**
 * `value`, as KV holds the record `key`, read as a route record of the zone `zoneId`: its every
 * field in shape, each pattern once, each change it names one its routes allow (a moved or
 * deleted pattern among them, an added one not). Anything else throws: a bad record is never
 * handed back.
 */
export function recordOf(value: unknown, key: string, zoneId: string): RouteRecord {
  const bad = (why: string) => new Error(`the record ${key} ${why}: nothing is changed`);
  const held = value as Partial<Record<keyof RouteRecord, unknown>> | null;
  if (typeof held !== "object" || held === null || held.kind !== RECORD_KIND) {
    throw bad("is not a route record");
  }
  if (held.zone !== ZONE || held.zone_id !== zoneId) {
    throw bad(`is of another zone (${String(held.zone)}, ${String(held.zone_id)})`);
  }
  if (typeof held.run !== "string" || typeof held.at !== "string"
      || !(held.attempt === null || typeof held.attempt === "string")
      || !Array.isArray(held.routes) || !Array.isArray(held.moved)) {
    throw bad("is out of shape");
  }
  const routes = held.routes as Partial<Kept>[];
  if (!routes.every((route) => typeof route?.pattern === "string" && route.pattern !== ""
                    && (route.script === null || typeof route.script === "string"))) {
    throw bad("holds a route out of shape");
  }
  const patterns = routes.map((route) => route.pattern!);
  if (new Set(patterns).size !== patterns.length) {
    throw bad("holds a pattern twice");
  }
  for (const each of held.moved as Partial<Moved>[]) {
    const recorded = typeof each?.pattern === "string" && patterns.includes(each.pattern);
    if (!(each?.method === "POST" ? typeof each.pattern === "string" && !recorded
      : (each?.method === "PUT" || each?.method === "DELETE") && recorded)) {
      throw bad(`names a change its routes do not allow (${JSON.stringify(each)})`);
    }
  }
  const domain = held.domain as Partial<RouteRecord["domain"]> | null | undefined;
  if (typeof domain !== "object" || domain === null || domain.hostname !== EGRESS_HOST
      || !(domain.service === null || typeof domain.service === "string")
      || !(domain.created === null || dnsList(domain.created))
      || (domain.created !== null && domain.service !== null)) {
    throw bad("holds no egress domain in shape");
  }
  const host = held.host as Partial<RouteRecord["host"]> | null | undefined;
  if (typeof host !== "object" || host === null || host.name !== HOST
      || typeof host.held !== "boolean" || !(host.made === null || dnsList(host.made))
      || (host.made !== null && host.held)) {
    throw bad("holds no host record in shape");
  }
  const hook = held.hook as Partial<RouteRecord["hook"]> | null | undefined;
  if (typeof hook !== "object" || hook === null || hook.url !== E2B_HOOK.url
      || typeof hook.present !== "boolean" || typeof hook.enabled !== "boolean"
      || (hook.enabled && !hook.present)) {
    throw bad("holds no lifecycle webhook in shape");
  }
  return held as RouteRecord;
}

/**
 * The egress host's custom domain before `routes` attaches it: the doors', or none. Another
 * Worker's, or an address record on the host, stops `routes` before any change: wrangler's
 * deploy overrides both, and no handback could give either back.
 */
async function egressBefore({ cloudflare, account, zone: id }:
                              Pick<Values, "cloudflare" | "account" | "zone">):
    Promise<string | null> {
  const held = await cloudflare.domains(account, EGRESS_HOST);
  const others = held.filter((domain) => domain.service !== DOORS);
  if (others.length > 0) {
    throw new Error(`${EGRESS_HOST} is ${others.map((domain) => domain.service).join(", ")}'s ` +
                    "custom domain: routes would take it, and a handback could not give it " +
                    "back; nothing is changed");
  }
  if (held.length > 0) {
    return DOORS;
  }
  const records = await cloudflare.dnsRecords(id, EGRESS_HOST);
  if (records.length > 0) {
    throw new Error(`${EGRESS_HOST} holds ${records.length} DNS record(s) (${records.map(
      (record) => record.type).join(", ")}): the doors' custom domain would replace them, and ` +
      "a handback could not give them back; nothing is changed");
  }
  return null;
}

/**
 * The DNS record a host under the zone holds for the doors' routes on it: the reserved
 * `100::`, proxied (Cloudflare's originless setups), which serves nothing but the routes.
 */
export const PLACEHOLDER: Omit<DnsRecord, "id"> = Object.freeze({ type: "AAAA",
                                                                  content: "100::" });

/**
 * The doors' host's DNS records before `routes`: null for the zone, whose record is the
 * site's; else the placeholders it holds (`PLACEHOLDER`, an earlier routes run's or one made
 * by hand), which `routes` leaves, or none, so `routes` makes one (`hostRecord`). Any other
 * address record on the host stops `routes` before any change: the routes would take its
 * requests from whatever it serves, and no handback could give them back.
 */
async function placedBefore({ cloudflare, zone: id }: Pick<Values, "cloudflare" | "zone">):
    Promise<readonly DnsRecord[] | null> {
  if (HOST === ZONE) {
    return null;
  }
  const records = await cloudflare.dnsRecords(id, HOST);
  const others = records.filter((record) => record.type !== PLACEHOLDER.type
    || record.content !== PLACEHOLDER.content);
  if (others.length > 0) {
    throw new Error(`${HOST} holds ${others.length} DNS record(s) the doors' routes would take ` +
                    `the requests of (${others.map((record) => `${record.type} ${
                      record.content}`).join(", ")}), and a handback could not give them ` +
                    "back; nothing is changed");
  }
  return records;
}

// --- The rollout record -----------------------------------------------------------------

/**
 * What a deploy (`dark`, `routes`) keeps before its first Worker deploy, so mode `rollback`
 * can take the run's Workers back: each Worker in the run's deploy order with the version it
 * served before (none for a first deploy) and that version's tag, and what the run's version
 * of it carries that a version before cannot take back (`forwardOnly`): its locks' counts, a
 * whole deploy (`--migrate`), the names of the secrets the run sets on it, and its forward
 * count (`platform.json` `forward`). The run's own versions are known by their tag, the run's
 * commit (`versionFlags`). It holds no secret and not the account id, beside the route record
 * in the control namespace, under the run's id, which a re-run attempt keeps; the job summary
 * names it.
 */
export interface RolloutRecord {
  readonly kind: typeof ROLLOUT_KIND;
  readonly run: string;
  readonly attempt: string | null;
  readonly at: string;
  readonly commit: string;
  readonly workers: readonly Rolled[];
}

/** One Worker of a rollout record (`RolloutRecord`). */
export interface Rolled {
  /** Its script name. */
  readonly name: string;
  /** The version it served before the run, and that version's tag; null for a first deploy. */
  readonly before: { readonly version: string; readonly tag: string | null } | null;
  /** The run's version's locks' counts, by lock path (`lockCounts`). */
  readonly counts: Readonly<Record<string, number>>;
  /** Whether the run deployed it whole (`--migrate`). */
  readonly whole: boolean;
  /** The secrets the run set on it, by name. */
  readonly secrets: readonly string[];
  /** The run's version's forward count (`FORWARD`). */
  readonly forward: number;
}

const ROLLOUT_KIND = "tinyassets-rollout";
/** A rollout record's key in the control namespace, after its run's id. */
const ROLLOUT_PREFIX = "deploy:rollout:";

/**
 * Why `rolled` goes to 100% at once, its stages below it skipped (`STAGES`), or null when it
 * takes them: why its version before can neither serve beside the run's in a split nor be
 * gone back to, which ending a split asks. The version before is none (a first deploy); the
 * run deployed it whole (a Durable Object class change, which Cloudflare uploads as no version
 * and rolls back across never); the run set its secrets, which the version before holds
 * otherwise (each version serves with its own, and Cloudflare refuses that deployment, 10220,
 * which the kit never forces); or the run's forward count is above the version before's
 * tag's, so it writes, or is called, in a way the version before cannot follow
 * (`platform.json` `forward`). A tag that names no count to hold the run's to is unknown, and
 * goes at once too.
 */
export function atOnce(rolled: Rolled): string | null {
  if (rolled.before === null) {
    return "the run deployed it first: no version comes before it";
  }
  if (rolled.whole) {
    return "the run deployed it whole (--migrate), and Cloudflare rolls back across no " +
           "Durable Object class change";
  }
  if (rolled.secrets.length > 0) {
    return `the run set ${rolled.secrets.join(", ")} on it, which its version before holds ` +
           "otherwise (Cloudflare refuses that rollback, and the kit never forces one)";
  }
  const tagged = taggedAs(rolled.before.tag);
  const tag = rolled.before.tag ?? "with nothing";
  if (rolled.forward > 0 && (tagged === null || rolled.forward > tagged.forward)) {
    return tagged === null
      ? `its version before is tagged ${tag}, which names no forward count to hold the run's ` +
        `-f${rolled.forward} to: unknown`
      : `the run's version is marked -f${rolled.forward} and its version before ${
        tagged.forward === 0 ? "with none" : `-f${tagged.forward}`}: it writes, or is called, ` +
        "in a way its version before cannot follow (platform.json forward)";
  }
  return null;
}

/**
 * Why a rollback may not take `rolled` back to the version it served before the run, or null
 * when it may: what sends it to 100% at once (`atOnce`), or the run's locks pinning more
 * migrations than the version before's tag names, so every object the newer one migrated
 * would refuse to start on it (`contract/src/migrate.ts`); a tag naming no counts to hold the
 * run's to is unknown, and stops it too.
 */
export function forwardOnly(rolled: Rolled): string | null {
  const alone = atOnce(rolled);
  if (alone !== null) {
    return alone;
  }
  const tagged = taggedAs(rolled.before!.tag);
  const tag = rolled.before!.tag ?? "with nothing";
  const pinned = pinnedCounts(rolled.counts);
  if (pinned.length === 0) {
    return null;
  }
  const counts = tagged?.counts ?? null;
  if (counts === null || counts.length !== pinned.length) {
    return `its version before is tagged ${tag}, which names no migration counts to hold the ` +
           `run's -m${pinned.join(".")} to: unknown`;
  }
  return pinned.some((count, at) => count > counts[at]!)
    ? `the run's version pins -m${pinned.join(".")} and its version before -m${
      counts.join(".")}: every object the newer one migrated would refuse to start on it`
    : null;
}

/** What a plan line says of a rule's answer (`decided`). */
interface Says {
  /** The line when the rule answers null. */
  readonly otherwise: string;
  /** The line when it answers `reason`. */
  readonly then: (reason: string) => string;
  /** What follows when the run sets an optional secret that decides it. */
  readonly so: string;
}

/**
 * `rule`'s answer for `rolled` (`atOnce`, `forwardOnly`) as a plan line says it, `setting`
 * the plan's secrets on it. When only optional ones the plan job cannot tell of (`Setting`)
 * could change the answer, the line ends in what the apply job reads (`checked`).
 */
function decided(rolled: Rolled, setting: readonly Setting[],
                 rule: (rolled: Rolled) => string | null, says: Says): string {
  const unsure = setting.filter((each) => OPTIONAL.includes(each.name));
  const reason = rule({ ...rolled, secrets: setting.filter((each) =>
    !OPTIONAL.includes(each.name)).map((each) => each.name) });
  if (reason !== null) {
    return says.then(reason);
  }
  if (unsure.length === 0) {
    return says.otherwise;
  }
  const set = unsure.filter((each) => each.sets === true).map((each) => each.name);
  return checked(`${says.otherwise} unless the run sets ${unsure.map((each) =>
    each.name).join(", ")} on it`, unsure.some((each) => each.sets === null) ? null
    : set.length === 0 ? "it sets none of them" : `it sets ${set.join(", ")}, so ${says.so}: ${
      rule(rolled)}`);
}

/**
 * What `rolled`'s line of a deploy's plan says: where it stands, and where a rollback stops,
 * `setting` the plan's secrets on it (`decided`).
 */
function rolledLine(rolled: Rolled, setting: readonly Setting[]): string {
  if (rolled.before === null) {
    return `${rolled.name}: deployed by this run first, so a rollback stops at it`;
  }
  const at = `${rolled.name}: serving ${rolled.before.version} (${rolled.before.tag ??
    "untagged"}); a rollback`;
  return decided(rolled, setting, forwardOnly, {
    otherwise: `${at} takes it back there`, then: (reason) => `${at} stops at it: ${reason}`,
    so: "it stops at it" });
}

/** Whether `value` is a list of strings, each once. */
function onceEach(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((each) => typeof each === "string")
    && new Set(value).size === value.length;
}

/**
 * `value`, as KV holds the record `key`, read as a rollout record: its every field in shape,
 * each Worker once. Anything else throws: a bad record is never rolled back.
 */
export function rolloutOf(value: unknown, key: string): RolloutRecord {
  const bad = (why: string) => new Error(`the record ${key} ${why}: nothing is changed`);
  const held = value as Partial<Record<keyof RolloutRecord, unknown>> | null;
  if (typeof held !== "object" || held === null || held.kind !== ROLLOUT_KIND) {
    throw bad("is not a rollout record");
  }
  if (typeof held.run !== "string" || typeof held.at !== "string"
      || !(held.attempt === null || typeof held.attempt === "string")
      || typeof held.commit !== "string" || !/^[0-9a-f]{40}$/.test(held.commit)
      || !Array.isArray(held.workers)) {
    throw bad("is out of shape");
  }
  for (const each of held.workers as Partial<Record<keyof Rolled, unknown>>[]) {
    const before = each?.before as Partial<Record<"version" | "tag", unknown>> | null | undefined;
    const counts = each?.counts as Record<string, unknown> | null | undefined;
    if (typeof each?.name !== "string" || typeof each.whole !== "boolean" || !onceEach(each.secrets)
        || !Number.isInteger(each.forward) || Number(each.forward) < 0
        || !(before === null || (typeof before === "object" && typeof before.version === "string"
          && (before.tag === null || typeof before.tag === "string")))
        || typeof counts !== "object" || counts === null
        || !Object.values(counts).every((count) => Number.isInteger(count) && Number(count) >= 0)) {
      throw bad(`holds a Worker out of shape (${JSON.stringify(each)})`);
    }
  }
  if (!onceEach((held.workers as Rolled[]).map((each) => each.name))) {
    throw bad("holds a Worker twice");
  }
  return held as RolloutRecord;
}

// --- The steps --------------------------------------------------------------------------

/** No Worker deploys a migration its lock does not pin, checked before anything is changed. */
const locks = step<never, "migrations">(({ run, say }) => {
  const workers = migrated();
  const migrations = Object.fromEntries(workers.map((worker) =>
    [worker, lockCounts(worker, run)]));
  say(`the migration locks pin every migration the ${workers.join(" and ")} Workers ship`);
  return { migrations };
});

/** The zone and its account, which Cloudflare's client and wrangler are given. */
const zone = step<never, "cloudflare" | "zone" | "account" | "wrangler">(async ({ run, mask }) => {
  const cloudflare = new Cloudflare(input(run, "CLOUDFLARE_API_TOKEN")!, run.io.fetch);
  const { id, account } = await cloudflare.zone(ZONE);
  mask(account);
  return { cloudflare, zone: id, account, wrangler: wranglerFor(run, account) };
});

/** The doors' upstream, from the owner issuer's live discovery. */
const upstream = step<never, "upstream">(async ({ run, say }) => {
  const values = await upstreamFrom(input(run, "OWNER_ISSUER"), input(run, "WORKOS_CLIENT_ID"),
                                    run.io.fetch);
  const { client_id: client, jwks_uri: jwks } = values.record;
  say(`set the doors' OWNER_ISSUER to ${values.issuer}, held to its discovery, and their ` +
      `UPSTREAM to the WorkOS client ${client} and the JWKS ${jwks}`);
  return { upstream: values };
});

const e2b = step<never, "e2b">(({ run }) => ({
  e2b: new E2bAdmin(input(run, "E2B_API_KEY")!, run.io.fetch) }));

/**
 * The pinned template, held to this tree's image (`image.ts`): a tree whose image is not built
 * and pinned yet (the pin PR follows the template's build) is planned, and refused by an apply.
 */
function pinnedTree({ run, say }: Context): string {
  const pin = pinned();
  const tree = run.io.treeImage();
  if (tree !== pin) {
    const why = `the pinned template ${pin} is not this tree's image ${tree}: build it ` +
      "(sandbox-template.yml, build) and pin its passed pair";
    if (run.apply) {
      throw new Error(`${why}; nothing is deployed`);
    }
    say(why);
  }
  return pin;
}

/** The pinned template is this tree's image, and E2B has a ready build of it. */
const templateReady = step<"e2b">(async (ctx, { e2b: admin }) => {
  const pin = pinnedTree(ctx);
  if (!await admin.ready(pin)) {
    throw new Error(`E2B has no ready build of ${pin}: build it (sandbox-template.yml)`);
  }
  ctx.say(checked(`E2B's ready build of ${pin}`, "present"));
});

/** The plan job's: the pinned template is this tree's image; its build is the apply job's. */
const templateLater = step((ctx) => {
  ctx.say(checked(`E2B's ready build of ${pinnedTree(ctx)}`, null));
});

/** What a plan says of the pair's standing checks in the deploy's E2B team. */
const pairLine = (pair: string, outcome: string | null) =>
  checked(`the pair ${pair}'s standing checks in this E2B team`, outcome);

/** The pair this deploy makes (`shippedPair`), passed. */
const pairReady = step<"e2b">(async ({ run, say }, { e2b: admin }) => {
  const pair = shippedPair(run.io);
  if (await admin.ready(pair)) {
    say(pairLine(pair, "passed"));
  } else if (!run.apply) {
    say(pairLine(pair, "not passed yet: the pair job runs its check (--mode pair) before the " +
                       "apply job"));
  } else {
    throw new Error(`the pair ${pair} has not passed its standing checks in this E2B team: ` +
                    "run the pair job (--mode pair) first; nothing is deployed");
  }
});

const pairLater = step(({ run, say }) => {
  say(pairLine(shippedPair(run.io), null));
});

/** E2B's lifecycle webhook, listed and checked before anything is changed. */
const hookListed = step<"e2b", "hook">(async (_ctx, { e2b: admin }) => ({
  hook: await heldHook(admin) }));

/**
 * The WorkOS redirect URI, listed, and registered when missing (additive, never a delete)
 * on an applied run, before any Cloudflare change: the early sign-in check needs it listed.
 */
const redirect = step<never, "listed">(async ({ run, say }) => {
  const state = await registerRedirect(input(run, "WORKOS_API_KEY")!, run.apply, run.io.fetch);
  say(checked(`WorkOS redirect URI ${UPSTREAM_CALLBACK}`, state));
  return { listed: state !== "missing" };
});

const redirectLater = step<never, "listed">(({ say }) => {
  say(checked(`WorkOS redirect URI ${UPSTREAM_CALLBACK}`, null));
  return { listed: false };
});

/**
 * The early sign-in check: a callback the run could not list, or found missing, is one a
 * redirect-uri-invalid answer leaves to `dark` to register.
 */
const authorize = step<"upstream" | "listed">(async ({ run, say }, got) => {
  say(await authorizeCheck(got.upstream, !got.listed, run.io.fetch));
});

/** The doors' routes' handover, from the zone's routes as they stand, and the host's record. */
const routePlan = step<"cloudflare" | "zone", "table" | "changes" | "placed" | "routed">(
  async (_ctx, got) => {
    const table = await got.cloudflare.routes(got.zone);
    const changes = handover(table, DOORS, ROUTES);
    return { table, changes, placed: await placedBefore(got), routed: changes.length === 0 };
  });

/** Whether the doors hold their routes (`routed`), from the zone's routes as they stand. */
const routedNow = step<"cloudflare" | "zone", "routed">(async (_ctx, got) => ({
  routed: handover(await got.cloudflare.routes(got.zone), DOORS, ROUTES).length === 0 }));

/** The control namespace's id, or undefined while no dark run has made it. */
async function controlNamespace(cloudflare: Cloudflare, account: string):
    Promise<string | undefined> {
  return (await cloudflare.namespaces(account)).get(NAMESPACES["CONTROL"]!);
}

/** The route record `routes` keeps, and where it is kept: its key in the control namespace. */
export interface KeptRecord {
  readonly record: RouteRecord;
  readonly namespace: string;
  readonly key: string;
}

type Recording = "cloudflare" | "zone" | "account" | "table" | "changes" | "placed";

/**
 * The route records of the doors' host (`HOST`) in the control namespace, oldest first (by
 * `at`): each the state before the handover a routes run began on the host, held until a
 * handback ends it (`recordsEnded`). A routes run keeps the oldest and writes none while one
 * is held (`keepRecord`), so a later one is a kit's from before that rule: kept while the
 * oldest stood, it holds the handover's own state as its before. A record naming another host
 * is that host's handover; a listed key whose value is gone was deleted since the listing (KV
 * lists eventually); any other value out of shape throws (`recordOf`). The run's own key
 * (`own`) is read whether listed or not: a listing just after a write can lack it, and a
 * re-run attempt that missed its first attempt's record would write over it.
 */
async function hostRecords({ cloudflare, account, zone: id }:
                             Pick<Values, "cloudflare" | "account" | "zone">,
                           namespace: string, own: string): Promise<KeptRecord[]> {
  const held: KeptRecord[] = [];
  for (const key of new Set([...await cloudflare.keys(account, namespace, RECORD_PREFIX), own])) {
    const value = await cloudflare.value(account, namespace, key);
    const host = (value as { host?: { name?: unknown } } | null | undefined)?.host?.name;
    if (value === undefined || (typeof host === "string" && host !== HOST)) {
      continue;
    }
    held.push({ record: recordOf(value, key, id), namespace, key });
  }
  return held.sort((a, b) => a.record.at < b.record.at ? -1 : a.record.at > b.record.at ? 1 : 0);
}

/** How a plan names a held route record: its run, attempt and time. */
function heldLine({ record }: KeptRecord): string {
  return `run ${record.run}'s (attempt ${record.attempt ?? "-"}, ${record.at})`;
}

/**
 * The route record (`RouteRecord`), kept before any change. The host's handover has one
 * record, the state before its first routes run: while the control namespace holds one of
 * the host (`hostRecords`), every routes run keeps the oldest as it is (a re-run attempt its
 * own first attempt's, a later run the record of the run that began the handover), and it is
 * the one a handback names. Only with none held is this run's written. A record that cannot
 * be written stops the run, nothing changed. `hook` is E2B's listing of the lifecycle hook, or
 * undefined in the plan job, which cannot list it and never writes.
 */
async function keepRecord({ run, say, summarize: add }: Context, got: Pick<Values, Recording>,
                          hook: Json | null | undefined): Promise<Pick<Values, "kept">> {
  const domain = await egressBefore(got);
  const key = `${RECORD_PREFIX}${run.job.id}`;
  const title = NAMESPACES["CONTROL"]!;
  const namespace = await controlNamespace(got.cloudflare, got.account);
  if (namespace === undefined) {
    if (run.apply) {
      throw new Error(`routes keeps its route record in ${title}, which a dark run makes: run ` +
                      "dark first; nothing is changed");
    }
    say(`record the zone's routes as this run's route record: ${title} is not made yet, so the ` +
        "apply job stops until a dark run makes it");
    return { kept: null };
  }
  const [first, ...later] = await hostRecords(got, namespace, key);
  if (first !== undefined) {
    say(`keep the route record ${first.key}, ${heldLine(first)}, the state before the first ` +
        `routes run on ${HOST}, as it is, and write none${later.length === 0 ? ""
          : ` (${later.map((each) => each.key).join(", ")}, kept while it stood, hold the ` +
            "handover's own state; a handback of it ends them too)"}: hand back with --mode ` +
        `handback --record ${first.record.run}`);
    return { kept: first };
  }
  say(checked(`record the zone's ${got.table.length} routes and the handover's ${
    got.changes.length} changes, the egress domain (${domain ?? "none"}) and E2B's lifecycle ` +
    `webhook as this run's route record in ${title}, before any change: hand back with --mode ` +
    "handback --record <this run's id>; the webhook it records", hook === undefined ? null
    : hook === null ? "absent" : hook["enabled"] === true ? "enabled" : "disabled"));
  // The plan job (no listing of the hook) never applies.
  if (!run.apply || hook === undefined) {
    return { kept: null };
  }
  const record = routeRecord(run.job, run.io.now(), got.zone, got.table, got.changes, domain,
                             got.placed, hook);
  const status = await got.cloudflare.put(got.account, namespace, key, record);
  if (status !== 200) {
    throw new Error(`writing the route record ${key} failed (${status}): nothing is changed`);
  }
  // A copy beside the plan, in the job summary, for the founder to read.
  add(`Route record ${key}:\n\n\`\`\`json\n${JSON.stringify(record, null, 2)}\n\`\`\`\n\n`);
  return { kept: { record, namespace, key } };
}

const recorded = step<Recording | "hook", "kept">((ctx, got) => keepRecord(ctx, got, got.hook));
const recordedLater = step<Recording, "kept">((ctx, got) => keepRecord(ctx, got, undefined));

/** The DNS records `records`, as a plan names them on `name`. */
function named(records: readonly Omit<DnsRecord, "id">[], name: string): string {
  return records.map((each) => `${each.type} ${name} (${each.content})`).join(", ");
}

/**
 * The doors' host's DNS record, made when the host is a name under the zone that holds none:
 * a Worker route on a subdomain takes nothing without a proxied record (Cloudflare's routes
 * page). Made once the route record is kept and before anything is deployed, then written
 * into it, so a handback deletes exactly it; a refused make (the token's DNS Edit) stops the
 * run with only the route record written. The record it makes is the handover's from here.
 */
const hostRecord = step<"cloudflare" | "zone" | "account" | "placed" | "kept", "kept">(
  async ({ run, say, did }, got) => {
    if (got.placed === null) {
      return { kept: got.kept };
    }
    // A route record that has the host holding none before routes makes a placeholder on it
    // now the handover's: one an earlier run or attempt made before it was written in, or the
    // one added by hand after a refused make (the checklist's fallback), or a new one made when
    // the one it made is gone since (deleted by hand); its handback deletes it.
    const bare = got.kept !== null && !got.kept.record.host.held
      && (got.kept.record.host.made === null || got.placed.length === 0);
    say(got.placed.length > 0
      ? bare ? `${HOST} holds ${named(got.placed, HOST)}, and held none when the route record ` +
               "was kept: the record keeps it as the handover's, for a handback to delete"
        : `${HOST} holds its DNS record already, ${named(got.placed, HOST)}: none is made`
      : `make the DNS record ${named([PLACEHOLDER], HOST)}, proxied, for the ` +
        "doors' routes on it, before anything is deployed: the route record keeps it, for a " +
        "handback to delete exactly it");
    if (!run.apply) {
      return { kept: got.kept };
    }
    if (got.kept === null) {
      throw new Error("an applied routes run keeps its route record before any change");
    }
    const { record, namespace, key } = got.kept;
    if (!bare) {
      return { kept: got.kept };
    }
    const made = got.placed.length > 0 ? got.placed
      : [await got.cloudflare.makeDns(got.zone, HOST, PLACEHOLDER)];
    const written = { ...record, host: { ...record.host, made } };
    const status = await got.cloudflare.put(got.account, namespace, key, written);
    if (status !== 200) {
      throw new Error(`writing the DNS record made on ${HOST} into ${key} failed (${status}): ` +
                      "run routes again, which reads and writes it once more");
    }
    did(`record in ${key} the DNS record made on ${HOST}: ${named(made, HOST)}`);
    return { kept: { record: written, namespace, key } };
  });

/** Each KV namespace by its title, made on an applied run when the account's listing lacks it. */
const namespaces = step<"cloudflare" | "account", "ids">(async ({ run, say }, got) => {
  const titles = await got.cloudflare.namespaces(got.account);
  const ids: Record<string, string> = {};
  for (const [binding, title] of Object.entries(NAMESPACES)) {
    const id = titles.get(title);
    if (id !== undefined) {
      ids[binding] = id;
      continue;
    }
    say(`create the KV namespace ${title} (${binding})`);
    if (run.apply) {
      ids[binding] = await got.cloudflare.makeNamespace(got.account, title);
    }
  }
  return { ids: Object.freeze(ids) };
});

/** Storage of one kind, `names` each made when the account's listing (`list`) lacks it. */
function storage(noun: string, names: readonly string[],
                 list: (cloudflare: Cloudflare, account: string) => Promise<ReadonlySet<string>>,
                 make: (cloudflare: Cloudflare, account: string, made: string) => Promise<void>):
    Step<"cloudflare" | "account", never> {
  return step<"cloudflare" | "account">(async ({ run, say }, got) => {
    const held = await list(got.cloudflare, got.account);
    for (const each of names.filter((one) => !held.has(one))) {
      say(`create the ${noun} ${each}`);
      if (run.apply) {
        await make(got.cloudflare, got.account, each);
      }
    }
  });
}

const buckets = storage("R2 bucket", BUCKETS,
                        async (cloudflare, account) => new Set(await cloudflare.buckets(account)),
                        (cloudflare, account, bucket) => cloudflare.makeBucket(account, bucket));

/** A bucket's one lock rule: every object kept for `days`. */
function keptRule(days: number): LockRule {
  return { id: `kept-${days}-days`, enabled: true, prefix: "",
           condition: { type: "Age", maxAgeSeconds: days * 86_400 } };
}

/** Whether the lock rule `held`, as the API lists it, keeps what `rule` keeps. */
function keeps(held: Readonly<Record<string, unknown>>, rule: LockRule): boolean {
  const condition = held["condition"] as { type?: unknown; maxAgeSeconds?: unknown } | undefined;
  return held["enabled"] === true && (held["prefix"] ?? "") === rule.prefix
    && condition?.type === rule.condition.type
    && condition.maxAgeSeconds === rule.condition.maxAgeSeconds;
}

/**
 * Each locked bucket's one rule (`BUCKET_LOCKS`), set when the bucket holds no rule or another:
 * a bucket the plan would make holds none yet.
 */
const bucketLocks = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const held = new Set(await got.cloudflare.buckets(got.account));
  for (const [bucket, days] of Object.entries(BUCKET_LOCKS)) {
    const rule = keptRule(days);
    const rules = held.has(bucket) ? await got.cloudflare.bucketLocks(got.account, bucket) : [];
    if (rules.length === 1 && keeps(rules[0]!, rule)) {
      continue;
    }
    say(`lock every object of the R2 bucket ${bucket} for ${days} days${
      rules.length === 0 ? "" : `, in place of its ${rules.length} lock rules`}`);
    if (run.apply) {
      await got.cloudflare.lockBucket(got.account, bucket, [rule]);
    }
  }
});

const queues = storage("queue", QUEUES,
                       async (cloudflare, account) => new Set((await cloudflare.queues(account))
                         .keys()),
                       (cloudflare, account, queue) => cloudflare.makeQueue(account, queue));

/** Each Worker's config as it deploys (`Stage`). */
const staged = step<"ids" | "upstream", "stage">((ctx, got) => ({
  stage: stageOf(ctx.scratch, got.ids, got.upstream) }));

type Secrets = "cloudflare" | "account";

/**
 * A secret the run's plan sets on a Worker. Whether it does is known to both jobs, from the
 * Workers' listings and `--rotate`, except for an optional one (`OPTIONAL`: a shared app's,
 * the canary clock's), which it sets only when the run is given it: only the apply job holds
 * the values, so the plan job plans it as the apply job's to tell (`sets` null).
 */
export interface Setting {
  readonly worker: string;
  readonly name: string;
  /** Whether the run sets it: null in the plan job for an optional secret. */
  readonly sets: boolean | null;
}

/** What a plan says of `each`: an optional secret's line ends in what only the apply job reads. */
function settingLine(each: Setting): string {
  const line = `set the secret ${each.name} on ${NAMES[each.worker]} with its version`;
  return OPTIONAL.includes(each.name)
    ? checked(`${line} if the run is given it`, each.sets === null ? null
      : each.sets ? "it is" : "it is not, so it stays unset")
    : line;
}

/**
 * Each Worker's missing secrets (and the rotated), which ride the version the run deploys
 * (`workers`): `secrets.required` refuses a deploy without them. Whether a Worker exists is
 * the account's Workers listing's word: a missing one holds none. `values` is the run's
 * secrets, or null in the plan job, which holds none. A held secret is never set again unless
 * `--rotate` names it, so a run sets only what it planned from the listings.
 *
 * `hook` is E2B's listing of the lifecycle hook, or undefined in the plan job. With none at
 * E2B the run registers one with production's E2B_WEBHOOK_SECRET, which a held copy on the
 * doors may not be: that run goes on only with `--rotate` naming it, which sets both, so
 * nothing the plan job cannot list decides what the run sets.
 */
async function setSecrets({ run, say }: Context, got: Pick<Values, Secrets>,
                          values: ((name: string) => string | undefined) | null,
                          hook: Json | null | undefined):
    Promise<Pick<Values, "deployed" | "setting">> {
  const deployed = await got.cloudflare.workers(got.account);
  const held: Record<string, string[]> = {};
  for (const worker of Object.keys(SECRETS)) {
    held[worker] = deployed.includes(NAMES[worker]!)
      ? await got.cloudflare.secrets(got.account, NAMES[worker]!) : [];
  }
  const holders = Object.keys(held).filter((worker) => held[worker]!.includes(E2B_HOOK.secret));
  if (hook === null && holders.length > 0 && !run.rotate.has(E2B_HOOK.secret)) {
    throw new Error(`E2B holds no lifecycle webhook, and ${holders.map((worker) =>
      NAMES[worker]).join(" and ")} ${holders.length === 1 ? "holds" : "hold"} ${
      E2B_HOOK.secret}, which the webhook this run registers must match: dispatch again with ` +
      `--rotate ${E2B_HOOK.secret} to set both; no secret is set and no Worker deployed`);
  }
  const setting = secretPlan(held, run.rotate, values).map(({ worker, name }): Setting => ({
    worker, name, sets: !OPTIONAL.includes(name) || (values === null ? null
      : values(name) !== undefined) }));
  say(...setting.map(settingLine));
  return { deployed, setting };
}

const secrets = step<Secrets | "hook", "deployed" | "setting">((ctx, got) =>
  setSecrets(ctx, got, (name) => input(ctx.run, name), got.hook));
const secretsLater = step<Secrets, "deployed" | "setting">((ctx, got) =>
  setSecrets(ctx, got, null, undefined));

/**
 * The rollout record (`RolloutRecord`), kept before the first Worker deploy: each Worker the
 * account's Workers listing holds read as the one version it serves, and that version's tag.
 * A Worker that serves two (a split) stops the run before any deploy (`Cloudflare.serving`).
 * The run's Workers are held to what those versions hand and take (`servedHold`), before the
 * record and on every attempt. Written on an applied run when the run's record is not there
 * yet, and kept as it is when it is (a re-run attempt keeps the versions its first attempt
 * read); a refused write stops the run before any deploy. The plan names, Worker by Worker,
 * where a rollback of the run stops.
 * The Workers as read now are what the deploy goes on from (`workers`).
 */
const rollout = step<"cloudflare" | "account" | "ids" | "deployed" | "setting" | "migrations",
                     "rolled">(
  async ({ run, say, summarize: add }, got) => {
    const title = NAMESPACES["CONTROL"]!;
    const key = `${ROLLOUT_PREFIX}${run.job.id}`;
    const namespace = got.ids["CONTROL"];
    const workers: Rolled[] = [];
    const lines: string[] = [];
    for (const worker of WORKERS) {
      const name = NAMES[worker]!;
      let before: Rolled["before"] = null;
      if (got.deployed.includes(name)) {
        const version = await got.cloudflare.serving(got.account, name);
        before = { version, tag: await got.cloudflare.tag(got.account, name, version) };
      }
      const setting = got.setting.filter((each) => each.worker === worker);
      const rolled: Rolled = { name, before, counts: got.migrations[worker] ?? {},
                               whole: run.migrate.has(worker),
                               secrets: setting.filter((each) => each.sets === true)
                                 .map((each) => each.name),
                               forward: FORWARD[worker]! };
      workers.push(rolled);
      lines.push(rolledLine(rolled, setting));
    }
    say(servedHold(run, workers));
    // Read after the split check, so a re-run attempt stops on a split as its first would.
    const held = namespace === undefined ? undefined
      : await got.cloudflare.value(got.account, namespace, key);
    if (held !== undefined) {
      const record = rolloutOf(held, key);
      say(`keep this run's rollout record as attempt ${record.attempt ?? "-"} wrote it at ` +
          `${record.at}, before its first deploy: roll back with --mode rollback --record ` +
          "<this run's id>");
      return { rolled: workers };
    }
    say(`record each Worker's version as this run's rollout record in ${title}, before its ` +
        "first deploy: roll back with --mode rollback --record <this run's id>", ...lines);
    if (!run.apply) {
      return { rolled: workers };
    }
    const record: RolloutRecord = { kind: ROLLOUT_KIND, run: run.job.id, attempt: run.job.attempt,
                                    at: run.io.now().toISOString(), commit: run.io.commit(),
                                    workers };
    await putOrStop(got, namespace!, key, JSON.stringify(record));
    add(`Rollout record ${key}:\n\n\`\`\`json\n${JSON.stringify(record, null, 2)}\n\`\`\`\n\n`);
    return { rolled: workers };
  });

/**
 * The run's Workers held to what the versions the stage serves hand them and take from them
 * (`handed.ts`), each serving version read from git at the commit its tag names: from each
 * Worker's 100% until a later one's, that one's version before talks to it (`WORKERS`). Any
 * refusal stops the run before any change, each named; so does a serving version whose tag names
 * no commit, or one this checkout lacks: what it hands is unknown. A Worker no version of which
 * serves (`rolled`'s first deploy) holds nothing. The plan's line names each commit read.
 */
function servedHold(run: Run, rolled: readonly Rolled[]): string {
  const sides = new Map<string, Before>();
  const read: string[] = [];
  const then = (worker: string): Before | null => {
    const { name, before } = rolled[WORKERS.indexOf(worker)]!;
    if (before === null) {
      return null;
    }
    const commit = taggedAs(before.tag)?.commit;
    if (commit === undefined) {
      throw new Error(`${name} serves ${before.version}, tagged ${before.tag ?? "with nothing"}, ` +
                      "which names no commit: what it hands and takes is unknown, so nothing " +
                      "is changed");
    }
    if (!sides.has(commit)) {
      sides.set(commit, sideAt(commit, run.io.fileAt));
    }
    read.push(`${name} at ${commit}`);
    return sides.get(commit)!;
  };
  const problems = handedProblems(then, TREE);
  if (problems.length > 0) {
    throw new Error(`the run's Workers and the versions the stage serves refuse what the other ` +
                    `hands across the deploy, so nothing is changed: ${problems.join("; ")}`);
  }
  return "hold the run's Workers to what the serving versions hand and take " +
    `(contract/handed.json, contract/doors.json), read from git: ${read.length === 0
      ? "none serves yet" : read.join(", ")}: none refuses the other`;
}

/** The doors' smoke at a share below 100 (`canary.ts --smoke`), as a plan names it. */
const SMOKE = "the canary's doors checks, each request with Cloudflare's version override for " +
  `it and each answer held to name it in ${VERSION_HEADER}`;

/**
 * Who reaches a Worker's version at a share below 100, and so whether it is smoked there: the
 * doors once they hold their routes (`smoked`); the doors before (`unrouted`), when no
 * request reaches them; any other Worker (`bound`), which only RPC reaches, where no version
 * override does.
 */
type Reach = "smoked" | "unrouted" | "bound";

const REACH: Readonly<Record<Reach, string>> = Object.freeze({
  smoked: `held ${STAGES.soakMs / 1000} s and smoked there (${SMOKE})`,
  unrouted: "not smoked: the doors do not hold their routes yet, so no request reaches them",
  bound: "not smoked: only RPC reaches it, and no version override reaches RPC",
});

/** A deployment's versions, as a line says them: each id and its share, in order. */
function sharesLine(serving: readonly Serving[]): string {
  return serving.map((each) => `${each.version} at ${each.percentage}%`).join(" and ") || "none";
}

/** Whether `now`, a deployment as Cloudflare lists it, serves `want`, in its order. */
function servesAs(now: readonly Serving[], want: readonly Serving[]): boolean {
  return now.length === want.length && now.every((each, at) =>
    each.version === want[at]!.version && each.percentage === want[at]!.percentage);
}

/**
 * How long a deployment just made is read back for, and how often. Cloudflare's deployments
 * listing answered the gateway's deployment at 100%, which wrangler had just made, with the
 * deployment before it (run 45065434026, 2026-11-22), and listed it by the next run's plan;
 * its docs give the listing no read-after-write, only that a deployment "takes up to a few
 * seconds" to reach every location (`version-skew.mdx`).
 */
const READ_BACK_MS = 60_000;
const READ_BACK_EVERY_MS = 2_000;

/**
 * The Worker `name`'s deployment read (`Cloudflare.deployment`) until Cloudflare lists it
 * serving exactly `want`, in its order, every `READ_BACK_EVERY_MS`; past `READ_BACK_MS` a
 * throw naming the last read-back, `stop` after it. It reads only: nothing is deployed again.
 */
async function readBack(run: Run, { cloudflare, account }: Pick<Values, "cloudflare" | "account">,
                        name: string, want: readonly Serving[], stop: string): Promise<void> {
  const until = run.io.now().getTime() + READ_BACK_MS;
  for (;;) {
    const now = await cloudflare.deployment(account, name);
    if (servesAs(now, want)) {
      return;
    }
    if (run.io.now().getTime() >= until) {
      throw new Error(`${name}'s deployment read back as ${sharesLine(now)}, not ${
        sharesLine(want)}, ${READ_BACK_MS / 1000} s after it was made${stop}`);
    }
    await run.io.sleep(READ_BACK_EVERY_MS);
  }
}

/**
 * What a deploy's plan says of `rolled` (`decided`, `setting` the plan's secrets on it): its
 * stages (`STAGES`), with `reach` saying whether its shares below 100 are smoked, or 100% at
 * once and why (`atOnce`).
 */
function deployLine(rolled: Rolled, setting: readonly Setting[], reach: Reach): string {
  const whole = rolled.before === null || rolled.whole;
  const below = STAGES.shares.slice(0, -1).map((share) => `${share}%`).join(", then ");
  return decided(rolled, setting, atOnce, {
    otherwise: `upload a version of ${rolled.name} and deploy it at ${below}, listed first ` +
      `beside its version before, ${REACH[reach]}, then at 100%, each read back, touching no ` +
      "route",
    then: (reason) => `${whole ? `deploy ${rolled.name}` : `upload a version of ${
      rolled.name} and deploy it`} at 100% at once, read back, touching no route, since ${
      reason}`,
    so: "it goes to 100% at once" });
}

/**
 * Each Worker deployed, bound first, touching no route, each deployment read back
 * (`readBack`) before anything follows it. A first deploy (the account's Workers
 * listing lacked it: `rolled`) is `wrangler deploy`; later ones upload a version, then deploy
 * it through the stages (`platform.json` `stages`): at each share below 100 beside the version
 * it serves (`rolled`), the new one listed first, so a Durable Object assigned it keeps it as
 * the share rises; then at 100%. Each is `wrangler versions deploy`, which leaves the Worker's
 * routes as they are and syncs the settings its config gives that no version carries
 * (logpush, tail consumers, observability). At each share below 100 the doors, once they hold
 * their routes (`routed`), are held `soak_s`, then smoked (`canary.ts --smoke`); a failed
 * smoke stops the run there, every request but an override's still served by their version
 * before, which a rollback takes them back to. Worker by Worker, so each is uploaded only once
 * the Workers it binds serve their new versions. A Worker that cannot serve beside its version
 * before goes to 100% at once (`atOnce`), in its place in the order. Wrangler 4.154 refuses a versions upload with a pending Durable Object
 * migration: `--migrate <worker>` deploys it whole. The secrets the run sets on a Worker go up
 * with its version (`--secrets-file`; one the file omits is kept from the version before),
 * never by `wrangler secret put`, which deploys the code already there at once; each version
 * is tagged (`versionFlags`).
 */
const workers = step<"cloudflare" | "account" | "wrangler" | "stage" | "setting" | "migrations"
                     | "rolled" | "routed">(async ({ run, say, did, scratch }, got) => {
  const commit = run.io.commit();
  const stop = `: the run stops here (--mode rollback --record ${run.job.id} takes it back)`;
  for (const [at, worker] of WORKERS.entries()) {
    const rolled = got.rolled[at]!;
    const reach: Reach = worker !== IDENTITY ? "bound" : got.routed ? "smoked" : "unrouted";
    say(deployLine(rolled, got.setting.filter((each) => each.worker === worker), reach));
    if (!run.apply) {
      continue;
    }
    const config = got.stage.config(worker);
    const flags = ["--config", config,
                   ...versionFlags(commit, got.migrations[worker] ?? {}, FORWARD[worker]!),
                   ...secretsFile(run, scratch, worker, got.setting)];
    const landed = async (want: Serving[]) => {
      await readBack(run, got, rolled.name, want, stop);
      did(`${rolled.name} serves ${sharesLine(want)}, read back`);
    };
    if (rolled.before === null || rolled.whole) {
      const id = versionOf(got.wrangler(["deploy", ...flags]), "deploy");
      await landed([{ version: id, percentage: 100 }]);
      continue;
    }
    const id = versionOf(got.wrangler(["versions", "upload", ...flags]), "versions upload");
    const deploy = async (want: Serving[]) => {
      got.wrangler(["versions", "deploy", ...want.map((each) =>
        `${each.version}@${each.percentage}`), "--yes", "--config", config]);
      await landed(want);
    };
    for (const share of atOnce(rolled) === null ? STAGES.shares.slice(0, -1) : []) {
      await deploy([{ version: id, percentage: share },
                    { version: rolled.before.version, percentage: 100 - share }]);
      if (reach === "smoked") {
        await run.io.sleep(STAGES.soakMs);
        const exit = canaryExit(run, ["--smoke", id]);
        if (exit !== 0) {
          throw new Error(`the doors' smoke of ${id} at ${share}% failed (exit ${exit})${stop}`);
        }
        did(`the doors' smoke of ${id} at ${share}% passed`);
      }
    }
    await deploy([{ version: id, percentage: 100 }]);
  }
});

/**
 * The `--secrets-file` of the secrets `setting` names on `worker`, their values the run's, or
 * no flag when it names none. The file holds those names alone, as JSON (wrangler expands `$`
 * in a dotenv file), mode 0600, in the run's scratch, which the run removes at its end, a
 * throw's included (`perform`).
 */
function secretsFile(run: Run, scratch: () => string, worker: string,
                     setting: Values["setting"]): string[] {
  const names = setting.filter((each) => each.worker === worker && each.sets === true)
    .map((each) => each.name);
  if (names.length === 0) {
    return [];
  }
  const file = join(scratch(), `${worker}.secrets.json`);
  writeFileSync(file, JSON.stringify(Object.fromEntries(names.map((name) => {
    const value = input(run, name);
    if (value === undefined) {
      throw new Error(`${name} is not set, and the plan sets it on ${NAMES[worker]}`);
    }
    return [name, value];
  }))), { mode: 0o600 });
  return ["--secrets-file", file];
}

/**
 * Each retired Worker the account holds (its Workers listing), deleted with its Durable
 * Objects once every Worker is deployed, so nothing of this tree binds it.
 */
const retired = step<"cloudflare" | "account" | "deployed">(async ({ run, say }, got) => {
  for (const name of RETIRED.filter((each) => got.deployed.includes(each))) {
    say(`delete the retired Worker ${name} and its Durable Objects (no Worker of this tree ` +
        "binds it)");
    if (run.apply) {
      await got.cloudflare.deleteWorker(got.account, name);
    }
  }
});

/** `text` written to `key` as it is, or a loud stop. */
async function putOrStop({ cloudflare, account }: Pick<Values, "cloudflare" | "account">,
                         namespace: string, key: string, text: string): Promise<void> {
  const status = await cloudflare.putText(account, namespace, key, text);
  if (status !== 200) {
    throw new Error(`writing ${key} failed (${status})`);
  }
}

/**
 * The control record, written closed only when the API answered there is none. One there that
 * no reader takes (`controlOf`: an operator's bad write, or a key the readers no longer hold)
 * stops the run loudly, since every gate reads it closed until `control --replace` mends it.
 */
const control = step<"cloudflare" | "account" | "ids">(async ({ run, say }, got) => {
  const id = got.ids["CONTROL"];
  const held = id === undefined ? undefined
    : await got.cloudflare.text(got.account, id, CONTROL_KEY);
  if (held === undefined) {
    say("write the control record closed (outside off, canary none, inbound off, the " +
        "founder's tier numbers): the API answered there is none");
    if (run.apply) {
      await putOrStop(got, id!, CONTROL_KEY, JSON.stringify(DARK_CONTROL));
    }
  } else if (controlOf(held) === null) {
    throw new Error(`the control record is one no reader takes: ${held}; every gate reads it ` +
                    "closed until the control mode's --replace gives the whole record");
  }
});

// --- The operator path (`operator.ts`) -----------------------------------------------------

/** Where a `control` run keeps the record it replaced, in the control namespace, by run id. */
const KEPT_PREFIX = "deploy:control:";

/**
 * Mode `control`: the control record edited in KV, never through the doors, so the kill
 * switch works when they don't. The edits apply to the live record, or to `--replace`'s whole
 * record in its place, which a missing or malformed live record needs; the result is written
 * only when `controlOf`, every reader's parser, takes it and it differs. Before the write
 * the live record is kept under the run's id (a re-run attempt keeps what its first kept);
 * after it, the record is read back and held to what was written.
 */
const controlEdit = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const title = NAMESPACES["CONTROL"]!;
  const namespace = await controlNamespace(got.cloudflare, got.account);
  if (namespace === undefined) {
    throw new Error(`the control record is kept in ${title}, which a dark run makes: run dark ` +
                    "first; nothing is changed");
  }
  // The live record's bytes, and the record they are when they are JSON.
  const held = await got.cloudflare.text(got.account, namespace, CONTROL_KEY);
  let live: unknown = held;
  try {
    live = held === undefined ? undefined : JSON.parse(held);
  } catch {
    // Not JSON: the bytes themselves, which only --replace replaces.
  }
  if (run.replace === undefined && (held === undefined || controlOf(held) === null)) {
    throw new Error(`the control record is ${held === undefined ? "missing"
      : `one no reader takes: ${held}`}; give the whole record with --replace; nothing is ` +
                    "changed");
  }
  const record = edited(run.replace ?? live, run.edits);
  const text = JSON.stringify(record);
  const control = controlOf(text);
  if (control === null) {
    throw new Error(`the control record as edited is one no reader takes (controlOf): ${text}; ` +
                    "nothing is changed");
  }
  const changes = differences(live, record);
  if (changes.length === 0) {
    say("the control record stands as asked: no difference");
    return;
  }
  const kept = `${KEPT_PREFIX}${run.job.id}`;
  say(...changes.map((change) => `set the control record's ${change}`),
      ...closedToAll(control) ? ["the record then admits no owner: closed to all"] : [],
      held === undefined ? "keep no previous record: there is none"
        : `keep the previous record's bytes in ${title} under this run's id, to put back with ` +
          "--replace");
  if (!run.apply) {
    return;
  }
  if (held !== undefined
      && await got.cloudflare.text(got.account, namespace, kept) === undefined) {
    await putOrStop(got, namespace, kept, held);
  }
  await putOrStop(got, namespace, CONTROL_KEY, text);
  const back = await got.cloudflare.text(got.account, namespace, CONTROL_KEY);
  if (back !== text) {
    throw new Error(`the control record read back is not the one written: ${back ?? "none"}`);
  }
  say("read the control record back: it is the one written");
});

/** `op` on `owner` with `args` asked of the doors through the operator channel: its result. */
async function asked(run: Run, got: Pick<Values, "cloudflare" | "account">, op: string,
                     owner: string, args: OperatorArgs): Promise<unknown> {
  const answer = await askOperator(got.cloudflare, got.account,
                                   keyring(input(run, OPERATOR_KEYRING), OPERATOR_KEYRING), op,
                                   owner, args, run.io);
  if ("refused" in answer) {
    throw new Error(`the doors refused ${op} for ${owner} (${answer.refused.code}): ${
      answer.refused.message}`);
  }
  return answer.result;
}

/**
 * Mode `owner`: `--act`'s op asked of the doors for `--owner` through the operator channel,
 * and their answer said, between the owner's state (`owner.state`) before and after it; a
 * refusal stops the run, named. The plan job only plans it: it holds no `OPERATOR_KEYRING`.
 */
const ownerAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const { op, args } = run.act!;
  const owner = run.owner!;
  say(`ask the doors ${op} ${canonical(args)} for ${owner}, through the operator channel, ` +
      "between its state (owner.state) before and after");
  if (!run.apply) {
    return;
  }
  const state = async () => JSON.stringify(await asked(run, got, "owner.state", owner, {}));
  say(`${owner} before ${op}: ${await state()}`);
  say(`the doors answered ${op} for ${owner}: ${
    JSON.stringify(await asked(run, got, op, owner, args))}`);
  say(`${owner} after ${op}: ${await state()}`);
});

/**
 * Mode `restore` (`owner/src/restore.ts`), each op through the operator channel, in order, a
 * refusal or a failed check stopping the run, named, and the owner never admitted. The plan
 * job only plans it.
 * - With no `--generation`: `restore.source` asked for `--owner` and its answer said; with
 *   `--confirm release-<owner>`, its pin given up (`restore.release`), the owner as it was.
 * - With one: the owner's source read; a live owner (or a failed restore) pinned to it and
 *   held, `restore.pin` asked until the owner's present state is written as a fresh
 *   generation, and then, only with `--confirm` the words the pin's answer names
 *   (`wipe-<owner>-<that generation>`), its object wiped (`restore.wipe`). Without them the
 *   run ends there, saying them: the confirmation is a second run, by the operator who read
 *   the generation it names. Then the restore begun, stepped until done, and the owner
 *   admitted (`owner.admit`: the object first, then the directory). An owner already restored
 *   from that generation is only admitted again.
 */
const restoreAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const owner = run.owner!;
  const from = run.generation;
  const ask = (op: string, args: OperatorArgs = {}) => asked(run, got, op, owner, args);
  if (from === null) {
    const release = run.confirm === `release-${owner}`;
    if (run.confirm !== null && !release) {
      throw new Error(`--confirm ${run.confirm} with no --generation is release-${owner}`);
    }
    const op = release ? "restore.release" : "restore.source";
    say(`ask the doors ${op} for ${owner}, through the operator channel; for a lost object ` +
        "(one that names no namespace), find the namespaces R2 holds that name it");
    if (!run.apply) {
      return;
    }
    const answer = await ask(op);
    say(`the doors answered ${op} for ${owner}: ${JSON.stringify(answer)}`);
    const source = answer as RestoreSource;
    if (!release && source.namespace === null && source.restore === null && source.begins) {
      const { own } = await namespacesOf(run, got, owner);
      say(own.size === 0 ? `R2 holds no namespace naming ${owner}: none is restored from`
        : `R2 holds ${own.size} namespaces naming ${owner}, newest first: ${[...own]
          .sort(([, a], [, b]) => b.at - a.at).map(([namespace, { gen, at }]) =>
            `${namespace}/${gen} (${new Date(at).toISOString()})`).join(", ")}: run again with ` +
          "--generation <namespace>/<gen>, and every other is held by the one it restores");
    }
    return;
  }
  say(`restore ${owner} from generation ${from.gen} of namespace ${from.namespace}, into its ` +
      "own object only while it is pending and empty (a live owner held first, its present " +
      "state written as a fresh generation, and its object wiped once --confirm names that " +
      "generation), then admit it, through the operator channel; for a restore no pin holds " +
      "(a lost object's), every other namespace R2 holds naming the owner found " +
      "(restore.namespaces, restore.locate) and held by the one it restores (restore.hold) " +
      "before it is admitted: " + RESTORE_LOSES);
  if (!run.apply) {
    return;
  }
  const source = await ask("restore.source") as RestoreSource;
  const held = source.restore;
  // A restore no pin held (a lost object's) has no hold in the bucket of the owner's other
  // namespaces: R2's are found, and each held by the one restored before the owner is admitted.
  const found = (source.begins || held?.step === "done") && (held?.fresh ?? null) === null
    ? await namespacesOf(run, got, owner) : null;
  const holdOthers = async () => {
    const others = [...found?.own ?? []].filter(([namespace]) => namespace !== from.namespace);
    for (const [namespace, { gen }] of others) {
      await ask("restore.hold", { namespace, gen });
    }
    if (found !== null) {
      say(`held each of the ${others.length} other namespaces R2 holds naming ${owner} by ` +
          `${from.namespace}, so its erasure takes them`);
    }
  };
  if (held?.step === "done" && held.gen === from.gen) {
    await holdOthers();
    say(`${owner} was restored from ${from.gen} already: the doors answered owner.admit: ${
      JSON.stringify(await ask("owner.admit"))}`);
    return;
  }
  if (found?.erased.has(from.namespace)) {
    throw new Error(`the namespace ${from.namespace} is erased (its mark, or its owner's ` +
                    "erasure): nothing is restored from it");
  }
  if (!source.begins) {
    const until = run.io.now().getTime() + RUN_MS;
    const pin = { namespace: from.namespace, gen: from.gen };
    let hold = await ask("restore.pin", pin) as RestoreHold;
    while (!hold.written) {
      if (run.io.now().getTime() >= until) {
        throw new Error(`${owner}'s present state was not written within ${RUN_MS / 60_000} ` +
                        "minutes, and the owner stays held: run again to carry it on, or give " +
                        `the pin up with --confirm release-${owner} and no --generation`);
      }
      say(`${owner} is held; its present state is being written as a fresh generation`);
      hold = await ask("restore.pin", pin) as RestoreHold;
    }
    if (run.confirm === null) {
      say(`${owner} is held, its present state written as generation ${hold.fresh}, its ` +
          `undo: to wipe its object and restore ${from.gen}, run again with ` +
          `--confirm ${hold.confirm}; to give the pin up, --confirm release-${owner} and no ` +
          "--generation");
      return;
    }
    if (run.confirm !== hold.confirm) {
      throw new Error(`--confirm ${run.confirm} is not the wipe's words for ${owner}: ` +
                      `${hold.confirm}`);
    }
    await ask("restore.wipe", { confirm: run.confirm });
    say(`wiped ${owner}'s object for its restore; generation ${hold.fresh ?? hold.gen} is ` +
        "its undo");
  }
  await ask("restore.begin", { namespace: from.namespace, gen: from.gen });
  await holdOthers();
  for (;;) {
    const at = await ask("restore.step") as RestoreAnswer;
    if (at.step === "failed") {
      throw new Error(`the restore of ${owner} failed its check, and the owner is not ` +
                      `admitted: ${at.failed}`);
    }
    if (at.done) {
      say(`restored ${owner} from ${at.gen}: ${at.rows} rows, ${at.chunks} chunks read back, ` +
          `the owner ${at.state} as at the generation`);
      break;
    }
    say(`the restore of ${owner} is at its ${at.step} step`);
  }
  say(`the doors answered owner.admit for ${owner}: ${JSON.stringify(await ask("owner.admit"))}`);
});

/**
 * Mode `copyback` (R8, `offsite.ts` `copyBack`): the objects of `--owner`'s namespace that
 * the files bucket lacks, written back from the off-account copy as it stood at `--at` (none:
 * as it stands), the restore record first. The namespace is the one the owner's own object
 * answers through the operator channel (`restore.source`). So an erasure the copy can't show
 * is applied before anything: the doors refuse an erased owner at the channel's gate (the
 * directory's record), at its tombstone and while it is erasing, and an object that names no
 * namespace (a lost one, or a new account's) is refused here, with nothing read or written.
 * The copy back itself refuses a namespace the files bucket marks erased (`offsite.ts`
 * `erasures`), the erasure's record that outlives the account.
 * Then mode `restore` restores a generation from it, should the owner's database go back too.
 * The plan job only plans it.
 */
const copyBackAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const owner = run.owner!;
  readableAt(run.at, run.io.now());
  say(`copy back into ${FILES_BUCKET} the objects of ${owner}'s namespace that it lacks and ` +
      "the owner's object names (restore.names: a row of it each, so nothing its own " +
      "collection deleted comes back), as the off-account copy " +
      `${run.at === null ? "stands" : `stood at ${run.at.toISOString()}`}, never one it holds, ` +
      `after writing the restore record ${RESTORE_KEY}: the namespace the owner's own object ` +
      "names (restore.source through the operator channel, which the doors refuse for an " +
      "erased or erasing owner), and none the files bucket marks erased (its marks read from " +
      "R2 and the copy first, a loud stop when they can't be), each object written read back " +
      "byte for byte");
  if (run.reach === "public") {
    return;
  }
  const { namespace } = await asked(run, got, "restore.source", owner, {}) as RestoreSource;
  if (namespace === null) {
    throw new Error(`${owner}'s object names no namespace, so nothing says which of the copy's ` +
                    "is its own (a lost object, or any after an account loss, which mode " +
                    "recover restores: checklist.md, \"A restore from the copy\"); nothing is " +
                    "read or written");
  }
  const control = (await got.cloudflare.namespaces(got.account)).get(NAMESPACES["CONTROL"]!);
  if (control === undefined) {
    throw new Error(`the account holds no ${NAMESPACES["CONTROL"]}: no dark deploy made it`);
  }
  if (!run.apply) {
    say(`${owner}'s object names the namespace ${namespace}`);
    return;
  }
  say(...await copyBack(backFrom(run.env, got.account), FILES_BUCKET, namespace, run.at,
                        () => namedBy(run, got, owner),
                        { cloudflare: got.cloudflare, account: got.account, namespace: control },
                        run.io));
});

/**
 * Mode `recover` (`offsite.ts` `recover`), after an account's loss, in the new account once a
 * dark deploy made it, with the copy's read key and a day's R2 write pair, as `copyback`: every
 * namespace of the off-account copy as it stood at `--at` (none: as it stands) that no erasure
 * mark names and that holds a root written back, the restore record and the marks R2 lacks
 * first; then each one's owner located from its newest generation (`restore.locate`, addressed
 * to the platform) and its restore begun (`restore.begin`), through the operator channel. Mode
 * `restore` carries each on. The plan job only plans it.
 */
const recoverAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  readableAt(run.at, run.io.now());
  say(`write back into ${FILES_BUCKET}, as the off-account copy ${run.at === null ? "stands"
    : `stood at ${run.at.toISOString()}`}, every object it lacks of each namespace that no ` +
      "erasure mark names and that holds a root, never one it holds, after writing the restore " +
      `record ${RESTORE_KEY} and then every mark R2 lacks (the marks read from R2 and the copy ` +
      "first, a loud stop when they can't be), each read back byte for byte; then locate each " +
      "namespace's owner from its newest generation (restore.locate, addressed to the " +
      "platform) and begin that owner's restore from it (restore.begin), through the operator " +
      "channel: two namespaces naming one owner stop the run before any restore begins; a " +
      "namespace a marked one holds is left out, one a written one holds is not begun, and one " +
      "whose owner takes no restore (live in this account) is held by the owner's own " +
      "(restore.hold), so its erasure takes it, and the run stops naming it");
  if (run.reach === "public") {
    return;
  }
  const control = (await got.cloudflare.namespaces(got.account)).get(NAMESPACES["CONTROL"]!);
  if (control === undefined) {
    throw new Error(`the account holds no ${NAMESPACES["CONTROL"]}: no dark deploy made it`);
  }
  if (!run.apply) {
    return;
  }
  say(...await recover(backFrom(run.env, got.account), FILES_BUCKET, run.at,
                       recovering(run, got),
                       { cloudflare: got.cloudflare, account: got.account, namespace: control },
                       run.io));
});

/**
 * What mode `recover` asks the doors (`Recovering`), a batch push of `BATCH` messages at a
 * time, stopping loudly past `RUN_MS` (a run again goes on): the namespaces' owners,
 * `LOCATE_PAGE` a message addressed to the platform, each owner's restore begun, and each
 * namespace not begun held by its owner's own.
 */
function recovering(run: Run, got: Pick<Values, "cloudflare" | "account">): Recovering {
  const ring = keyring(input(run, OPERATOR_KEYRING), OPERATOR_KEYRING);
  const until = run.io.now().getTime() + RUN_MS;
  const ask = async (op: string, asks: readonly Ask[]) => {
    const answers = [];
    for (const page of pages(asks, BATCH)) {
      if (run.io.now().getTime() >= until) {
        throw new Error(`stopped after ${RUN_MS / 60_000} minutes, before ${op} was asked of ` +
                        "every one: run again to go on (what is written is not written again, " +
                        "and a begin asked again answers as it stands)");
      }
      answers.push(...await askOwners(got.cloudflare, got.account, ring, op, page, run.io));
    }
    return answers;
  };
  const each = async (op: string, begins: readonly Begin[]) =>
    (await ask(op, begins.map(({ owner, namespace, gen }) =>
      ({ owner, args: { namespace, gen } })))).map((answer) => answer === null
      ? `not answered in ${WAIT_MS / 1000} s` : "refused" in answer
      ? `refused (${answer.refused.code}): ${answer.refused.message}` : null);
  return {
    locate: async (namespaces) => {
      const spans = pages(namespaces, LOCATE_PAGE);
      const answers = await ask("restore.locate", spans.map((span) =>
        ({ owner: PLATFORM_CALLER, args: locateArgs(span) })));
      return new Map(spans.flatMap((span, at) => {
        const answer = answers[at] ?? null;
        if (answer === null || "refused" in answer) {
          throw new Error(`the doors ${answer === null ? `did not answer in ${WAIT_MS / 1000} s`
            : `refused (${answer.refused.code}: ${answer.refused.message})`} restore.locate of ` +
            `the ${span.length} namespaces from ${span[0]}: nothing is begun`);
        }
        const { located } = answer.result as LocatedPage;
        return span.map((namespace) => [namespace, located[namespace] ?? null] as const);
      }));
    },
    begin: (begins) => each("restore.begin", begins),
    hold: (holds) => each("restore.hold", holds),
  };
}

/**
 * The namespaces the files bucket holds that name `owner`, each with its newest generation and
 * when that was written, for a lost object, whose object names none (`own`), and those found
 * erased (`erased`): every namespace R2 holds (`restore.namespaces`, a page at a time) located
 * (`restore.locate`, `recovering`), the doors erasing on the way each naming an owner they
 * record erased.
 */
async function namespacesOf(run: Run, got: Pick<Values, "cloudflare" | "account">,
                            owner: string):
    Promise<{ own: ReadonlyMap<string, LocatedOwner>; erased: ReadonlySet<string> }> {
  const namespaces: string[] = [];
  let cursor: string | null = null;
  do {
    const page = await asked(run, got, "restore.namespaces", PLATFORM_CALLER,
                             namespacesArgs(cursor)) as NamespacePage;
    namespaces.push(...page.namespaces);
    cursor = page.cursor;
  } while (cursor !== null);
  const located = namespaces.length === 0 ? new Map()
    : await recovering(run, got).locate(namespaces);
  const own = new Map<string, LocatedOwner>();
  const erased = new Set<string>();
  for (const [namespace, found] of located) {
    if (found === "erased") {
      erased.add(namespace);
    } else if (found?.owner === owner) {
      own.set(namespace, found);
    }
  }
  return { own, erased };
}

/** What `owner`'s object names in its namespace, page by page (`restore.names`). */
async function namedBy(run: Run, got: Pick<Values, "cloudflare" | "account">, owner: string):
    Promise<Referenced> {
  const chunks = new Set<string>();
  const generations = new Set<string>();
  for (let from: number | null = 0; from !== null;) {
    const page = await asked(run, got, "restore.names", owner, { from }) as RestoreNames;
    page.chunks.forEach((name) => chunks.add(name));
    page.generations.forEach((gen) => generations.add(gen));
    from = page.next;
  }
  return { chunks, generations };
}

/**
 * Mode `erase`: `--owner` erased through the operator channel (`owner.erase`), for an owner
 * who asked by other means: its object marked as its own door marks it, then the same
 * erasure (`owner/src/erasure.ts`). Irreversible, so only with `--confirm` its words
 * (`eraseWords`), which the doors hold it to again; without them the run ends saying them,
 * and with others it stops. The plan job only plans it.
 */
const eraseAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const owner = run.owner!;
  const words = eraseWords(owner);
  if (run.confirm === null) {
    say(`erase ${owner} only with its words: run again with --confirm ${words}`);
    return;
  }
  if (run.confirm !== words) {
    throw new Error(`--confirm ${run.confirm} is not the erasure's words for ${owner}: ${words}`);
  }
  say(`erase ${owner} through the operator channel, as its own door erases it; a lost object ` +
      `only once R2 holds no namespace naming it: ${ERASE_LOSES}`);
  if (!run.apply) {
    return;
  }
  // A lost object (pending, naming no namespace, no pin's record) marks none of the namespaces
  // R2 holds of it: restored first, its own then holds every other, and its erasure takes all.
  const source = await asked(run, got, "restore.source", owner, {}) as RestoreSource;
  if (source.state === "pending" && source.namespace === null
      && (source.restore?.fresh ?? null) === null) {
    const { own } = await namespacesOf(run, got, owner);
    if (own.size > 0) {
      throw new Error(`${owner}'s object is lost and R2 holds ${own.size} namespaces naming it, ` +
                      "which its erasure would not reach: restore it first (mode restore), " +
                      "then erase it");
    }
  }
  say(`the doors answered owner.erase for ${owner}: ${
    JSON.stringify(await asked(run, got, "owner.erase", owner, { confirm: words }))}`);
});

// --- Key rotation (`operator.ts` `rotate`) ------------------------------------------------

/**
 * The control and directory namespaces' ids, or a loud stop while no dark run made them; and,
 * with `window`, a loud stop while the doors hold the import window's key: an imported owner
 * has data in its object and no directory record until the window's admit writes it, so no
 * rotation reaches it.
 */
async function rotationNamespaces({ cloudflare, account }: Pick<Values, "cloudflare" | "account">):
    Promise<{ control: string; directory: string }> {
  const listed = await cloudflare.namespaces(account);
  const [control, directory] = ["CONTROL", "DIRECTORY"].map((binding) =>
    listed.get(NAMESPACES[binding]!));
  if (control === undefined || directory === undefined) {
    throw new Error(`the account holds no ${NAMESPACES["CONTROL"]} or ${NAMESPACES["DIRECTORY"]}` +
                    ": no dark deploy made them");
  }
  if (!(await cloudflare.workers(account)).includes(NAMES["doors"]!)) {
    throw new Error(`the account holds no ${NAMES["doors"]}: no dark deploy made it`);
  }
  if ((await cloudflare.secrets(account, NAMES["doors"]!)).includes(IMPORT_KEY)) {
    throw new Error(`${NAMES["doors"]} holds ${IMPORT_KEY}: the import window is open, and an ` +
                    "owner it imports has no directory record until its admit, so no rotation " +
                    "would reach it; nothing is asked");
  }
  return { control, directory };
}

/** `--keyring` as the run's production secrets hold it. */
const storedRing = (run: Run): Ring => keyring(input(run, run.keyring!), run.keyring!);

/**
 * Mode `rotation`: `--keyring`'s op asked of every owner the directory lists, through the
 * operator channel (`operator.ts` `rotate`), its progress in the control namespace. The plan
 * job only plans it: it holds no keyring, so no active kid. A run with the secrets and no
 * `--yes` says where the record stands and asks nothing.
 */
const rotationSweep = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const name = run.keyring!;
  const { op } = ROTATED[name]!;
  const { control, directory } = await rotationNamespaces(got);
  say(`ask the doors ${op} for every owner the directory lists, through the operator channel, ` +
      `a page of ${BATCH} at a time, keeping the pass's progress in ${NAMESPACES["CONTROL"]} ` +
      `under deploy:rotation:${name}:<the stored ring's active kid>: every answer's kid held to ` +
      "that kid, and an owner still holding a blob under another kid left owing, asked first " +
      `by the next run (${LATER})`);
  if (run.reach === "public") {
    return;
  }
  const stored = storedRing(run);
  if (!run.apply) {
    const record = await heldRotation(got.cloudflare, got.account, control, name, stored);
    const pass = record.passes.at(-1);
    say(pass === undefined ? `no pass of ${name}'s rotation to ${stored.active} has begun`
      : `pass ${record.passes.length} of ${name}'s rotation to ${stored.active}: after ${
        pass.through ?? "no owner yet"}, ${pass.owing.length} owing, ${pass.swept === null
        ? "not swept" : `swept at ${new Date(pass.swept * 1000).toISOString()}`}`);
    return;
  }
  await rotate({ cloudflare: got.cloudflare, account: got.account,
                 operator: keyring(input(run, OPERATOR_KEYRING), OPERATOR_KEYRING), keyring: name,
                 op, stored, directory, control, waiting: run.io, say });
});

/** What mode `retire` holds a keyring's retirement to (`retirement`). */
export interface Retiring {
  readonly keyring: string;
  /** The keyring as stored, which `keyring-retire` wrote: its active kid alone. */
  readonly stored: Ring;
  /** The rotation's record under the stored ring's active kid, or undefined when none is kept. */
  readonly record: RotationRecord | undefined;
  /**
   * For a keyring the off-account copy holds blobs under (`platform.json` `keyrings`): the copy
   * as it stands (`watch`), when a restore from it last began writing R2 (`restoreBegan`, null
   * when none is recorded), and its first night after a time (`firstNightAfter`). Null for a
   * keyring the copy never holds.
   */
  readonly copy: {
    readonly watch: Watch;
    readonly restored: number | null;
    readonly nightAfter: (at: Date) => Promise<{ readonly first: Date | null;
                                                 readonly ever: boolean }>;
  } | null;
  readonly now: Date;
}

/**
 * A keyring's retirement, or a loud stop naming why not: the kids it retires and the words that
 * confirm it, with what its gates read. It holds only when:
 * 1. the stored ring is the rotation record's ring less every kid but the active one;
 * 2. the record's latest pass swept every owner to `left` 0;
 * 3. for a keyring the off-account copy holds, the copy is not overdue, and `KEEP_DAYS` + 1
 *    days have passed since T, the first night that copied R2 after the first pass that swept
 *    and began after the latest restore from the copy (with none recorded, the first that
 *    swept), or the copy never passed a night, so it holds no blob of the keyring. That pass
 *    left no root in R2 under a retiring kid; T uploaded each root it rewrapped and hid each
 *    one gone since, so every version under a retiring kid is gone by then (`offsite.ts`: the
 *    lock runs from upload, and never outlasts the lifecycle's `KEEP_DAYS` and its daily
 *    pass). After it only a restore from the copy puts such a root back in R2, and the next
 *    night uploads it again under a fresh `KEEP_DAYS`: so the restore's record moves the gate
 *    to a pass begun after it.
 */
export async function retirement({ keyring: name, stored, record, copy, now }: Retiring):
    Promise<{ readonly kids: readonly string[]; readonly words: string;
              readonly lines: readonly string[] }> {
  const key = rotationKey(name, stored.active);
  if (record === undefined) {
    throw new Error(`${NAMESPACES["CONTROL"]} holds no rotation record ${key}: rotate first, ` +
                    "then store the ring keyring-retire writes; nothing is changed");
  }
  const kids = Object.keys(record.keys).filter((kid) => kid !== record.active).sort();
  const kept = keyHashes(stored)[stored.active];
  if (kids.length === 0) {
    throw new Error(`the rotation record ${key} holds no kid but ${record.active}: nothing to ` +
                    "retire; nothing is changed");
  }
  if (stored.keys.size !== 1 || kept !== record.keys[record.active]) {
    throw new Error(`the stored ${name} is not the rotation record's ring less ${
      kids.join(", ")}: store the ring keyring-retire wrote; nothing is changed`);
  }
  const latest = record.passes.at(-1);
  if (latest?.swept === null || latest === undefined) {
    throw new Error(`pass ${record.passes.length} of ${key} has not swept every owner to left 0: ` +
                    "run mode rotation until it does; nothing is changed");
  }
  const at = (seconds: number) => new Date(seconds * 1000).toISOString();
  const lines = [`pass ${record.passes.length} of ${key} swept every owner to left 0 at ${
    at(latest.swept)}`];
  if (copy !== null) {
    if (copy.watch.state === "overdue") {
      throw new Error(`the off-account copy is overdue (${copy.watch.line}): its versions under ` +
                      `${kids.join(", ")} expire only after nights pass; nothing is changed`);
    }
    const settled = record.passes.findIndex((pass) => pass.swept !== null
      && pass.began! >= (copy.restored ?? 0));
    if (settled < 0) {
      throw new Error(`a restore from the off-account copy began at ${at(copy.restored!)}, after ` +
                      `every pass of ${key} that swept began: it may have put roots under ${
                        kids.join(", ")} back, so run mode rotation for a pass begun after it; ` +
                      "nothing is changed");
    }
    const swept = record.passes[settled]!.swept!;
    const night = await copy.nightAfter(new Date(swept * 1000));
    if (night.first === null) {
      if (night.ever) {
        throw new Error(`no night of the off-account copy has passed since pass ${settled + 1} ` +
                        `swept at ${at(swept)}: retire waits ${KEEP_DAYS + 1} days after the ` +
                        "first that does; nothing is changed");
      }
      lines.push("the off-account copy has never passed a night, so it holds no blob under " +
                 `${kids.join(", ")}`);
    } else {
      const gate = new Date(night.first.getTime() + (KEEP_DAYS + 1) * 86_400_000);
      if (now < gate) {
        throw new Error(`the off-account copy's versions under ${kids.join(", ")} expire by ${
          gate.toISOString()}, ${KEEP_DAYS + 1} days after its night of ${
          night.first.toISOString()}: retire waits until then; nothing is changed`);
      }
      lines.push(`the off-account copy's versions under ${kids.join(", ")} expired at ${
        gate.toISOString()}, ${KEEP_DAYS + 1} days after its first night after pass ${
        settled + 1} swept`);
    }
  }
  return { kids, words: `retire-${name}-${kids.join(",")}`, lines };
}

/**
 * Mode `retire`: `--keyring`, stored as `keyring-retire` wrote it, set on each Worker that holds
 * it, once `retirement` holds and only with `--confirm` its words; without them the run ends
 * saying them. Irreversible unless the old ring is kept offline, so the plan job only plans it,
 * and the apply job names the words before a second run applies them.
 */
const retireAsk = step<"cloudflare" | "account" | "wrangler">(async ({ run, say }, got) => {
  const name = run.keyring!;
  const holders = Object.keys(SECRETS).filter((worker) => SECRETS[worker]!.includes(name));
  const { control } = await rotationNamespaces(got);
  say(`retire every kid of ${name} but its active one: set the stored ${name} on ${
    holders.map((worker) => NAMES[worker]).join(" and ")} only once its rotation's record shows ` +
      "every owner swept to left 0 after the off-account copy's versions under the kids expired, " +
      `and only with its words, which the apply job names (${LATER}). Irreversible unless the ` +
      "old ring is kept offline");
  if (run.reach === "public") {
    return;
  }
  const stored = storedRing(run);
  const key = rotationKey(name, stored.active);
  const held = await got.cloudflare.value(got.account, control, key);
  const record = held === undefined ? undefined : rotationRecord(held, key);
  const reach = { fetch: run.io.fetch, env: run.env };
  const now = run.io.now();
  const { kids, words, lines } = await retirement({
    keyring: name, stored, record, now,
    copy: !ROTATED[name]!.copied ? null : {
      watch: await watch(reach, now),
      restored: restoreBegan(await got.cloudflare.value(got.account, control, RESTORE_KEY)),
      nightAfter: (at) => firstNightAfter(reach, at) } });
  say(...lines);
  const deployed = await got.cloudflare.workers(got.account);
  for (const worker of holders) {
    if (!deployed.includes(NAMES[worker]!)
        || !(await got.cloudflare.secrets(got.account, NAMES[worker]!)).includes(name)) {
      throw new Error(`${NAMES[worker]} holds no ${name}: retire only shrinks a deployed ring; ` +
                      "nothing is changed");
    }
    // `wrangler secret put` deploys the version it makes at once: a holder mid-split stops it.
    await got.cloudflare.serving(got.account, NAMES[worker]!);
  }
  if (run.confirm === null) {
    say(`retire ${kids.join(", ")} from ${name} only with its words: keep the old ring file ` +
        `offline, then run again with --confirm ${words}`);
    return;
  }
  if (run.confirm !== words) {
    throw new Error(`--confirm ${run.confirm} is not the retirement's words for ${name}: ${words}`);
  }
  for (const worker of holders) {
    say(`set the secret ${name} on ${NAMES[worker]}, ${kids.join(", ")} retired`);
    if (run.apply) {
      got.wrangler(["secret", "put", name, "--name", NAMES[worker]!], input(run, name));
    }
  }
});

/**
 * What `--confirm` takes: a wipe's, a release's or an erasure's words, each naming the owner,
 * or a retirement's, naming the keyring and its kids.
 */
const CONFIRM = /^(?:wipe|release|erase|retire)-\S+$/;

/**
 * E2B's lifecycle webhook, registered or changed once the doors hold E2B_WEBHOOK_SECRET:
 * disabled in `dark`, enabled only after the routes, since an enabled hook sends the whole
 * team's events to whatever serves its path.
 */
function hook(enable: boolean, where: string): Step<"e2b" | "setting", never> {
  return step<"e2b" | "setting">(async ({ run, say }, got) => {
    const result = await lifecycleHook(got.e2b, {
      secret: input(run, E2B_HOOK.secret)!, setSecret: hookSetsSecret(got.setting), enable,
    }, run.apply);
    say(checked(`E2B lifecycle webhook ${E2B_HOOK.url}${where}`, result));
  });
}

/** The plan job's word on E2B's lifecycle webhook, which it cannot reach, where `hook` says it. */
function hookLater(where: string): Step<never, never> {
  return step(({ say }) => {
    say(checked(`E2B lifecycle webhook ${E2B_HOOK.url}${where}`, null));
  });
}

/** The doors' routes handed over one at a time, no path ever served by nothing. */
const handOver = step<"cloudflare" | "zone" | "changes">(async ({ run, say }, got) => {
  say(...got.changes.map((each) => describeStep(each, DOORS)));
  if (run.apply) {
    await got.cloudflare.reroute(got.zone, got.changes, (each) => describeStep(each, DOORS));
  }
});

/**
 * How long the kit waits for the egress door to answer on its host after the attach, how often
 * it asks, and each request's limit: the stage's host had its own certificate about two
 * minutes after the attach (2026-11-22).
 */
const ANSWER_MS = 10 * 60_000;
const ANSWER_EVERY_MS = 15_000;
const ANSWER_REQUEST_MS = 30_000;

/**
 * The sandbox's egress host, the doors' custom domain (`wrangler triggers deploy`), then the
 * host's own answer waited on (`answering`): wrangler's deploy returns without waiting, and
 * the host had no TLS for its name for about two minutes after the stage's attach.
 */
const customDomain = step<"cloudflare" | "zone" | "account" | "wrangler" | "stage" | "kept">(
  async ({ run, say, did }, got) => {
    say(`add the custom domain ${EGRESS_HOST} for ${DOORS} (wrangler triggers deploy), then ` +
        `wait up to ${ANSWER_MS / 60_000} minutes for the egress door's refusal on it`);
    if (!run.apply) {
      return;
    }
    if (got.kept === null) {
      throw new Error("an applied routes run keeps its route record before any change");
    }
    got.wrangler(["triggers", "deploy", "--config", got.stage.routed(IDENTITY)]);
    // The host held no address record before (`egressBefore`), so each it holds now is the
    // attach's: kept in the record, for a handback to remove exactly if the detach leaves it.
    const { record, namespace, key } = got.kept;
    if (record.domain.service === null && record.domain.created === null) {
      const created = await got.cloudflare.dnsRecords(got.zone, EGRESS_HOST);
      const status = await got.cloudflare.put(got.account, namespace, key,
                                              { ...record, domain: { ...record.domain, created } });
      if (status !== 200) {
        throw new Error(`writing the custom domain's DNS records into ${key} failed (${
          status}): run routes again, which reads and writes them once more`);
      }
      did(`record in ${key} the DNS records the custom domain made: ${
        created.length === 0 ? "none" : created.map((each) => each.type).join(", ")}`);
    }
    did(await answering(run));
  });

/**
 * A failed request's words: Node's fetch says only "fetch failed", and its cause which of
 * DNS, the connection or TLS failed. The kit's wait and the canary both name a failure so.
 */
export function fetchFailure(error: unknown): string {
  const { message, cause } = error as Error;
  return `${message}${cause instanceof Error ? ` (${cause.message})` : ""}`;
}

/**
 * Whether `answer` is the egress door's own refusal, a `404` naming its code: the doors serve
 * the host, not a Cloudflare error page (a `522`, a challenge). The kit's wait and the
 * canary's egress check both read it.
 */
export async function egressRefusal(answer: Response): Promise<boolean> {
  if (answer.status !== 404) {
    await answer.body?.cancel();
    return false;
  }
  const body = await answer.json().catch(() => null) as { error?: { code?: unknown } } | null;
  return typeof body?.error?.code === "string";
}

/**
 * The egress door's refusal on its host, asked every `ANSWER_EVERY_MS` until it answers, or a
 * throw past `ANSWER_MS` naming the last failure. fetch checks the certificate for the host;
 * `Connection: close` makes each attempt a fresh connection, its name resolved again (undici
 * keeps an answered socket for the next request otherwise), as the canary's process is.
 */
async function answering(run: Run): Promise<string> {
  const url = `https://${EGRESS_HOST}/`;
  const until = run.io.now().getTime() + ANSWER_MS;
  for (;;) {
    let failed: string;
    try {
      const answer = await run.io.fetch(url, { redirect: "manual", headers: { Connection: "close" },
                                               signal: AbortSignal.timeout(ANSWER_REQUEST_MS) });
      if (await egressRefusal(answer)) {
        return `${url} answers with the egress door's refusal`;
      }
      failed = `HTTP ${answer.status}, not the egress door's refusal`;
    } catch (error) {
      failed = fetchFailure(error);
    }
    if (run.io.now().getTime() >= until) {
      throw new Error(`${url} did not answer with the egress door's refusal within ${
        ANSWER_MS / 60_000} minutes of the custom domain's attach: ${failed}. Run routes ` +
        "again once it does");
    }
    await run.io.sleep(ANSWER_EVERY_MS);
  }
}

/**
 * Fact 11: after a deploy, a route change, a rollback or a retire, the platform's canary
 * (`canary.ts`), reading its own keys. With no side it holds whichever serves `/mcp`: before
 * `routes` the old stack, where it checks that the old stack still answers and the doors'
 * checks wait. `routes` asks for the doors' side: the doors must answer `/mcp` and its
 * resource document, and the old stack must not.
 *
 * Its keyed checks follow the control record as it stands, in every mode: while a record read
 * well formed admits no owner (`closedToAll`, as the dark record), no canary owner can sign in
 * or pass the doors' gate, so they wait (`--closed`); otherwise a canary owner is owed, and a
 * missing key fails.
 */
function canary(...side: [] | ["--side", "doors"]): readonly Held[] {
  return Steps.none.then(zone).then(step<"cloudflare" | "account">(async ({ run }, got) => {
    const namespace = await controlNamespace(got.cloudflare, got.account);
    const held = namespace === undefined ? undefined
      : await got.cloudflare.text(got.account, namespace, CONTROL_KEY);
    const control = held === undefined ? null : controlOf(held);
    const args = [...side, ...control !== null && closedToAll(control) ? ["--closed"] : []];
    const exit = canaryExit(run, args);
    if (exit !== 0) {
      throw new Error(`the platform canary${args.length === 0 ? "" : ` (${args.join(" ")})`} ` +
                      `failed (exit ${exit})`);
    }
  })).list;
}

/** The platform canary (`canary.ts`) run with `args`, its output the job's: its exit. */
function canaryExit(run: Run, args: readonly string[]): number | null {
  return run.io.spawn(process.execPath, ["--experimental-strip-types",
                                         join(PLATFORM, "deploy", "canary.ts"), ...args],
                      { stdio: "inherit", cwd: REPO }).status;
}

/** H2's probes alone: they change nothing, and need a routed door and a probe owner. */
const h2 = step(({ run }) => {
  run.io.exec(process.execPath, ["--experimental-strip-types",
                                 join(PLATFORM, "deploy", "h2-probes.ts")],
              { stdio: "inherit", env: { ...run.env } });
});

/**
 * The pair check (the workflow's `pair` job, before the apply job): the pair this checkout
 * deploys (`shippedPair`), unless E2B already holds its tag; `checks.ts pair` runs the
 * standing checks and tags the pair, given only what it needs (never the deploy's other
 * secrets). A failure, or no tag after it, throws.
 */
const pairCheck = step<"e2b">(async ({ run }, { e2b: admin }) => {
  const tag = shippedPair(run.io);
  if (await admin.ready(tag)) {
    run.io.log(`the pair ${tag} passed its standing checks in this E2B team`);
    return;
  }
  const checked = run.io.spawn(process.execPath, checksArgv("pair"), {
    stdio: "inherit",
    env: { PATH: run.env["PATH"] ?? "", HOME: run.env["HOME"] ?? "",
           E2B_API_KEY: input(run, "E2B_API_KEY")! } });
  if (checked.status !== 0 || !await admin.ready(tag)) {
    throw new Error(`the pair ${tag} failed its standing checks: nothing is deployed`);
  }
  run.io.log(`the pair ${tag} passed its standing checks, run now`);
});

type Handing = "cloudflare" | "zone" | "account" | "record";

/**
 * The route record `--record` names, read and held to its shape; missing or bad stops it. It
 * must be the host's oldest (`hostRecords`), the state before the handover's first routes run:
 * one kept after it holds the handover's own state, and its handback would leave the host
 * served, so naming it stops the run, naming the oldest. The handback ends the handover: once
 * it reads back as the record has it, every record of the host is deleted (`recordsEnded`).
 */
const recordRead = step<"cloudflare" | "zone" | "account", "record" | "ending">(
  async ({ run, say }, got) => {
    const key = `${RECORD_PREFIX}${run.record}`;
    const namespace = await controlNamespace(got.cloudflare, got.account);
    const value = namespace === undefined ? undefined
      : await got.cloudflare.value(got.account, namespace, key);
    if (namespace === undefined || value === undefined) {
      throw new Error(`there is no route record ${key} in ${NAMESPACES["CONTROL"]}: name the ` +
                      "id a routes plan gives (a handback that read back deletes its records, " +
                      "copied into its job summary); nothing is changed");
    }
    const record = recordOf(value, key, got.zone);
    const held = await hostRecords(got, namespace, key);
    const first = held[0];
    if (first !== undefined && first.record.at < record.at) {
      throw new Error(`the record ${key} was kept at ${record.at}, after the route record ${
        first.key}, ${heldLine(first)}, the state before the first routes run on ${HOST}: it ` +
        "holds the handover's own state, and its handback would leave the host served. Hand " +
        `back with --record ${first.record.run}; nothing is changed`);
    }
    // Later ones first, so a handback stopped among the deletes leaves the one it names.
    const ending = [...held.filter((each) => each.key !== key).reverse(),
                    { record, namespace, key }];
    say(`the route record ${key}: the zone's ${record.routes.length} routes as run ${
      record.run} read them at ${record.at}, and the handover's ${record.moved.length} changes`,
        `once the handback reads back as the record has it, delete ${ending.map((each) =>
          each.key).join(", then ")} from ${NAMESPACES["CONTROL"]}: the handback ends the ` +
        `handover ${ending.length === 1 ? "it records" : "they record"} (each copied into ` +
        "this run's job summary first)");
    return { record, ending };
  });

/**
 * The route records the handback ends (`ending`), deleted once it has read back as its record
 * has it: so the next routes run on the host keeps none and records the host as it stands. A
 * copy of each goes in the job summary first.
 */
const recordsEnded = step<"cloudflare" | "account" | "ending">(
  async ({ run, did, summarize: add }, got) => {
    if (!run.apply) {
      return;
    }
    for (const { record, namespace, key } of got.ending) {
      add(`Route record ${key}, ended by this handback:\n\n\`\`\`json\n${
        JSON.stringify(record, null, 2)}\n\`\`\`\n\n`);
      await got.cloudflare.deleteKeys(got.account, namespace, [key]);
    }
    did(`deleted ${got.ending.map((each) => each.key).join(", ")}`);
  });

/**
 * The record's handback on the zone and the egress domain as they stand: the doors' custom
 * domain to detach when the record has none, and the routes.
 */
const backPlan = step<Handing, "back" | "detach">(async ({ say }, { cloudflare, zone: id,
                                                                   account, record }) => {
  const domains = await cloudflare.domains(account, record.domain.hostname);
  const detach = record.domain.service === null
    ? domains.filter((domain) => domain.service === DOORS) : [];
  const routes = await cloudflare.routes(id);
  // Held to the record before any change: what changed after routes is named, never undone.
  const differences = drift(record.routes, record.moved, routes, DOORS);
  if (record.domain.service === null) {
    differences.push(...domains.filter((domain) => domain.service !== DOORS).map((domain) =>
      `${record.domain.hostname} is ${domain.service}'s custom domain, and routes attached it ` +
      `to ${DOORS}`));
  }
  if (differences.length > 0) {
    throw new Error(`changed since routes, so nothing is handed back: ${differences.join("; ")}. ` +
                    "Put each back as the record has it, then run the handback again");
  }
  const scope = new Set([...record.routes, ...record.moved].map((each) => each.pattern));
  say(...routes.filter((route) => !scope.has(route.pattern)).map((route) =>
    `leave the route ${route.pattern} (${route.script ?? "no Worker"}'s): neither the record ` +
    "nor the handover holds it"));
  return { back: handback(record.routes, record.moved, routes, DOORS), detach };
});

/**
 * The handback's first change, the reverse of `routes`' order: the hook disabled when `routes`
 * enabled it (the record has it absent or disabled) and it is enabled, since it sends the
 * team's events to whatever serves its path.
 */
const hookBack = step<"e2b" | "hook" | "record">(async ({ run, say }, got) => {
  const before = got.record.hook;
  const url = before.url;
  const disable = !before.enabled && got.hook?.["enabled"] === true ? String(got.hook["id"]) : null;
  say(checked(`E2B lifecycle webhook ${url}`, before.enabled ? "enabled before routes, left"
    : disable === null ? "not enabled already"
    : `disable it, as it was ${before.present ? "before routes"
      : "absent before routes (a disabled hook sends nothing)"}`));
  if (run.apply && disable !== null) {
    await got.e2b.changeWebhook(disable, { enabled: false });
  }
});

const hookBackLater = step<"record">(({ say }, got) => {
  say(checked(`E2B lifecycle webhook ${got.record.hook.url}`, null));
});

/**
 * The handback's other changes: the doors' custom domain detached, then the routes one at a
 * time, no path the record served ever served by nothing. Last, the DNS record the attach
 * made, if the detach left it, and the one `routes` made on the doors' host: each serves no
 * one once the routes are back, so a refused delete (the token's DNS Edit) never holds the
 * routes back.
 */
const handBack = step<Handing | "back" | "detach">(async ({ run, say }, got) => {
  const { record } = got;
  say(record.domain.service !== null
    ? `the custom domain ${record.domain.hostname}: ${record.domain.service}'s before routes, left`
    : got.detach.length === 0 ? `the custom domain ${record.domain.hostname}: detached already`
    : `detach the custom domain ${record.domain.hostname} from ${DOORS}, as it was before routes`);
  say(...(got.back.length === 0
    ? ["the zone's routes are the record's already: nothing to hand back"]
    : got.back.map(describeBack)));
  if (record.domain.service === null) {
    say(record.domain.created === null
      ? `the DNS records the custom domain made on ${record.domain.hostname} were not recorded ` +
        "(routes stopped between its attach and its record), so none is deleted, and the " +
        "read-back holds the host to no address record, as before routes"
      : record.domain.created.length === 0
        ? `the custom domain made no DNS record on ${record.domain.hostname}`
        : `delete the DNS record(s) the custom domain made, ${record.domain.created.map((each) =>
          `${each.type} ${record.domain.hostname} (${each.content})`).join(", ")}, where the ` +
          "detach leaves them");
  }
  const { host } = record;
  if (!host.held) {
    say(host.made === null
      ? `the DNS record routes made on ${host.name} was not recorded (routes stopped between ` +
        "its make and its record), so none is deleted, and the read-back holds the host to no " +
        "address record, as before routes"
      : `delete the DNS record routes made on ${host.name} for the doors' routes, ${
        named(host.made, host.name)}, where it is still there`);
  }
  if (run.apply) {
    for (const domain of got.detach) {
      await got.cloudflare.detach(got.account, domain.id, record.domain.hostname);
    }
    await got.cloudflare.reroute(got.zone, got.back, describeBack);
    // The address records the attach made, only those and only as recorded, if the detach
    // left them; then the host's, only the one routes made.
    for (const left of await createdLeft(got)) {
      await got.cloudflare.deleteDns(got.zone, left, record.domain.hostname);
    }
    for (const left of await madeLeft(got)) {
      await got.cloudflare.deleteDns(got.zone, left, host.name);
    }
  }
});

/**
 * The DNS records `routes` made on the doors' host, as the record holds them, still there:
 * matched by id, type and content, so no other record is ever taken.
 */
async function madeLeft({ cloudflare, zone: id, record }: Pick<Values, Handing>):
    Promise<DnsRecord[]> {
  const { made, name } = record.host;
  if (made === null || made.length === 0) {
    return [];
  }
  const now = await cloudflare.dnsRecords(id, name);
  return made.filter((each) => now.some((held) => held.id === each.id
    && held.type === each.type && held.content === each.content));
}

/**
 * The address records the custom domain made, as the record holds them, still on the host:
 * matched by id, type and content, so no other record is ever taken.
 */
async function createdLeft({ cloudflare, zone: id, account, record }: Pick<Values, Handing>):
    Promise<DnsRecord[]> {
  if (record.domain.service !== null || record.domain.created === null
      || record.domain.created.length === 0) {
    return [];
  }
  // Never a record a custom domain still holds: a detach that did not take leaves it, and
  // the read-back names the domain.
  if ((await cloudflare.domains(account, record.domain.hostname)).length > 0) {
    return [];
  }
  const now = await cloudflare.dnsRecords(id, record.domain.hostname);
  return record.domain.created.filter((made) => now.some((each) => each.id === made.id
    && each.type === made.type && each.content === made.content));
}

/**
 * After a handback, each thing read back and held to the record: the routes, the egress
 * domain when the record has none, and the hook when the record has it not enabled. Any
 * difference stops the run, naming each.
 */
const handedBack = step<Handing | "e2b">(async ({ run, say }, got) => {
  if (!run.apply) {
    return;
  }
  const { cloudflare, record } = got;
  const differences = unlike(record.routes, record.moved, await cloudflare.routes(got.zone));
  if (record.domain.service === null) {
    const domains = await cloudflare.domains(got.account, record.domain.hostname);
    differences.push(...domains.map((domain) => `${record.domain.hostname} is ${
      domain.service}'s custom domain (the record has none)`));
    differences.push(...(await createdLeft(got)).map((left) => `the DNS record ${left.type} ${
      record.domain.hostname} (${left.content}) the custom domain made is still there`));
    // The record proves the host held no address record before routes (`egressBefore`): with
    // the attach's never recorded, any there now is named, never passed.
    if (record.domain.created === null) {
      differences.push(...(await cloudflare.dnsRecords(got.zone, record.domain.hostname))
        .map((left) => `${record.domain.hostname} holds the DNS record ${left.type} (${
          left.content}), and held no address record before routes: the custom domain's own, ` +
          "not recorded, for a hand to remove"));
    }
  }
  const { host } = record;
  differences.push(...(await madeLeft(got)).map((left) => `the DNS record ${left.type} ${
    host.name} (${left.content}) routes made is still there`));
  // The record proves the host held no address record before routes (`placedBefore`): with
  // the one routes made never recorded, any there now is named, never passed.
  if (!host.held && host.made === null) {
    differences.push(...(await cloudflare.dnsRecords(got.zone, host.name)).map((left) =>
      `${host.name} holds the DNS record ${left.type} (${left.content}), and held none before ` +
      "routes: the one routes made, not recorded, for a hand to remove"));
  }
  if (!record.hook.enabled) {
    const hookNow = await heldHook(got.e2b);
    if (hookNow?.["enabled"] === true) {
      differences.push(`E2B lifecycle webhook ${record.hook.url} is enabled (the record has it ${
        record.hook.present ? "disabled" : "absent"})`);
    }
  }
  if (differences.length > 0) {
    throw new Error(`the handback read back unlike the record: ${differences.join("; ")}`);
  }
  say("the zone's routes, the egress domain and the webhook read back as the record has them");
});

/**
 * Fact 11's canary waits for the old stack: until it restarts, the routes the handback gave
 * back serve nothing, so the handback's own run would fail a correct handback.
 */
const canaryAfter = step(({ run }) => {
  run.io.log("The handles canary (Fact 11) waits for the old stack: restart it on its " +
             "untouched volumes, then dispatch the Handles canary workflow " +
             "(handles-canary.yml), the old stack's own check of its handle set.");
});

/** The versions a Worker serves, as a rollback's plan names them: each id, tag and share. */
function versionLine(serving: readonly (Serving & { readonly tag: string | null })[]): string {
  return serving.map((each) => `${each.version} (${each.tag ?? "untagged"}) at ${
    each.percentage}%`).join(" and ");
}

/**
 * Whether the Worker `name` served `before` just before a run of commit `ours` deployed it, by
 * Cloudflare's deployments listing (newest first): a deployment of `before` alone, at 100%,
 * followed by one serving a version tagged with that commit. A record naming an older real
 * version as the one before, with its real tag, names none such, unless a run of the same
 * commit followed it too; deployments made since (another run, and its rollback) leave it
 * found. `tagOf` reads a version's tag.
 */
async function servedBefore({ cloudflare, account }: Pick<Values, "cloudflare" | "account">,
                            name: string, ours: string, before: string,
                            tagOf: (version: string) => Promise<string | null>):
    Promise<boolean> {
  const history = await cloudflare.deployments(account, name);
  for (const [at, older] of history.entries()) {
    if (at === 0 || older.length !== 1 || older[0]!.version !== before
        || older[0]!.percentage !== 100) {
      continue;
    }
    for (const each of history[at - 1]!) {
      if (each.version !== before && taggedAs(await tagOf(each.version))?.commit === ours) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Mode `rollback`: the rollout record `--record` names (`RolloutRecord`), its run's Workers
 * taken back to the versions they served before it, in the reverse of its deploy order, each
 * a deployment of that version alone (`Cloudflare.deploy`), read back. So, as in the middle of
 * every deploy, the Workers a caller binds are never older than it. It stops at the first
 * Worker the run made forward-only (`forwardOnly`): that one and every Worker before it stay
 * on the run's versions, each named, and with none to take back the run stops before any
 * change. A Worker the run left at 0% beside its version before, which already serves all its
 * traffic, is taken back whatever its version carries. Each is held to the record first:
 * every version it serves is the one before or one
 * tagged with the run's commit (a split of the two, which a run stopped mid-rollout leaves,
 * included), and anything else (a deploy since) stops it, named, with nothing changed; so
 * does a record that is not Cloudflare's word: its Workers not this tree's, in its deploy
 * order; its tag before, counts or forward count not Cloudflare's tags; or, for a Worker it
 * takes back, its version before one Cloudflare's deployments listing never has it serving
 * just before a run of the commit (`servedBefore`). The record's namespace is bound,
 * writable, by the doors, owner and sandbox; tags and deployments only the deploy's token
 * writes.
 */
const rollbackAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const title = NAMESPACES["CONTROL"]!;
  const key = `${ROLLOUT_PREFIX}${run.record}`;
  const namespace = await controlNamespace(got.cloudflare, got.account);
  const value = namespace === undefined ? undefined
    : await got.cloudflare.value(got.account, namespace, key);
  if (value === undefined) {
    throw new Error(`there is no rollout record ${key} in ${title}: name the id a dark or ` +
                    "routes run's job summary gives; nothing is changed");
  }
  const record = rolloutOf(value, key);
  const ours = tagCommit(record.commit);
  const names = WORKERS.map((worker) => NAMES[worker]!);
  if (record.workers.map((each) => each.name).join(", ") !== names.join(", ")) {
    throw new Error(`the record ${key} names the Workers ${record.workers.map((each) =>
      each.name).join(", ")}, and this tree deploys ${names.join(", ")}, in that order: nothing ` +
      "is changed (a rollback by hand follows checklist.md)");
  }
  const back: Rolled[] = [];
  const lines: string[] = [];
  const drifted: string[] = [];
  let stop: { name: string; reason: string } | null = null;
  for (const rolled of [...record.workers].reverse()) {
    const tags = new Map<string, string | null>();
    const tagOf = async (version: string): Promise<string | null> => {
      if (!tags.has(version)) {
        tags.set(version, await got.cloudflare.tag(got.account, rolled.name, version));
      }
      return tags.get(version)!;
    };
    const serving = [];
    for (const each of await got.cloudflare.deployment(got.account, rolled.name)) {
      serving.push({ ...each, tag: await tagOf(each.version) });
    }
    const unknown = serving.filter((each) => each.version !== rolled.before?.version
      && taggedAs(each.tag)?.commit !== ours);
    if (serving.length === 0 || unknown.length > 0) {
      drifted.push(`${rolled.name} serves ${serving.length === 0 ? "no deployment"
        : versionLine(serving)}, neither its version before the run nor one of commit ${ours}`);
      continue;
    }
    // What `forwardOnly` reads, held to Cloudflare's tags.
    const pinned = pinnedCounts(rolled.counts).join(".");
    const tagBefore = rolled.before === null ? null : await tagOf(rolled.before.version);
    if (tagBefore !== (rolled.before?.tag ?? null) || serving.some((each) =>
      each.version !== rolled.before?.version
        && ((taggedAs(each.tag)?.counts ?? []).join(".") !== pinned
          || taggedAs(each.tag)?.forward !== rolled.forward))) {
      throw new Error(`the record ${key} holds ${rolled.name} as before ${
        rolled.before?.tag ?? "untagged"}, counts -m${pinned}, forward count ${rolled.forward
        }, and Cloudflare tags its version before ${tagBefore ?? "untagged"} and serves ${
        versionLine(serving)}: nothing is changed`);
    }
    if (serving.length === 1 && serving[0]!.version === rolled.before?.version) {
      lines.push(`${rolled.name} serves its version before the run already, ${
        versionLine(serving)}`);
      continue;
    }
    // A run stopped at a share of 0% leaves its version beside the one before, serving only a
    // request with an override for it, which no RPC call carries. Cloudflare assigns each
    // Durable Object a version by the shares, so none ran this one's migrations, and the
    // stages took it there only with no forward change (`atOnce`): the way back is open. In
    // whichever order Cloudflare lists the two: the shares, not the order, say who serves.
    const unserved = serving.length === 2 && serving.every((each) =>
      each.percentage === (each.version === rolled.before?.version ? 100 : 0));
    if (stop === null && !unserved) {
      const reason = forwardOnly(rolled);
      stop = reason === null ? null : { name: rolled.name, reason };
    }
    if (stop !== null && !unserved) {
      lines.push(`leave ${rolled.name} on ${versionLine(serving)}: ${rolled.name === stop.name
        ? stop.reason : `it comes before ${stop.name} in the deploy order, and ${stop.name} ` +
          "stays on the run's version"}`);
      continue;
    }
    if (!await servedBefore(got, rolled.name, ours, rolled.before!.version, tagOf)) {
      throw new Error(`the record ${key} holds ${rolled.name} as serving ${
        rolled.before!.version} before the run, and Cloudflare's deployments listing has no ` +
        `deployment of it alone followed by one of commit ${ours}: nothing is changed (a ` +
        "rollback by hand follows checklist.md)");
    }
    back.push(rolled);
    lines.push(`roll ${rolled.name} back to ${rolled.before!.version} (${
      rolled.before!.tag ?? "untagged"}) at 100%, from ${versionLine(serving)}${unserved
      ? ": its run's version at 0% served no request but an override's" : ""}`);
  }
  if (drifted.length > 0) {
    throw new Error(`deployed since run ${record.run}, so nothing is rolled back: ${
      drifted.join("; ")}`);
  }
  say(`the rollout record ${key}: the ${record.workers.length} Workers run ${record.run} ` +
      `deployed at ${record.at}, taken back in the reverse of its deploy order`, ...lines);
  if (back.length === 0 && stop !== null) {
    throw new Error(`nothing can be rolled back: ${stop.name} stays on run ${record.run}'s ` +
                    `version (${stop.reason}). Roll forward with a fix, or close the control ` +
                    "record's gates (--mode control); nothing is changed");
  }
  if (!run.apply) {
    return;
  }
  for (const rolled of back) {
    await got.cloudflare.deploy(got.account, rolled.name, rolled.before!.version);
    await readBack(run, got, rolled.name, [{ version: rolled.before!.version, percentage: 100 }],
                   ": the rollback stops here");
  }
  say(back.length === 0 ? "every Worker serves its version before the run already"
    : `read each Worker's deployment back: ${back.map((each) => each.name).join(", ")} ` +
      "serve their versions before the run");
});

// --- The modes --------------------------------------------------------------------------

/**
 * What a row reads from the environment: what the plan job holds too (`public`), what only the
 * apply job's production environment holds (`secret`), each refused before any step when
 * missing, and what that environment holds which the run goes on without (`optional`).
 */
export interface Inputs {
  readonly public: readonly string[];
  readonly secret: readonly string[];
  readonly optional: readonly string[];
}

/** One mode: what it reads, how far it reaches, and its steps. */
export interface Row {
  readonly inputs: Inputs;
  /** Whether it deploys Workers: it takes `--rotate` and `--migrate`, and needs the pair. */
  readonly deploys: boolean;
  /** Whether `--yes` may apply what it plans. */
  readonly applies: boolean;
  /** The options of its own it reads (`OWN`): it refuses another mode's. */
  readonly own: readonly Own[];
  /** Its steps, chosen once by `--public`: the plan job's (its token alone), or the rest. */
  readonly steps: Readonly<Record<Run["reach"], readonly Held[]>>;
  /**
   * Its plan, reported after its steps: what a run that changes nothing ends saying, and what
   * an applied run runs after it. Null for a row that plans nothing (`h2`, `pair`), which
   * holds its run to no option.
   */
  readonly report: { readonly unchanged: string; readonly after: readonly Held[] } | null;
}

/** The checks every deploy makes before any change: the plan job's, then the apply job's. */
const READS_LATER = Steps.none.then(locks).then(zone).then(upstream).then(templateLater)
  .then(pairLater);
const READS = Steps.none.then(locks).then(zone).then(upstream).then(e2b).then(templateReady)
  .then(pairReady).then(hookListed);

/**
 * A dark run: the checks and WorkOS's, then storage, secrets, the rollout record, Workers and
 * the control record.
 */
const DARK_LATER = READS_LATER.then(redirectLater).then(authorize).then(namespaces).then(buckets)
  .then(bucketLocks).then(queues).then(staged).then(secretsLater).then(rollout).then(routedNow)
  .then(workers).then(retired).then(control).then(hookLater(""));
const DARK = READS.then(redirect).then(authorize).then(namespaces).then(buckets)
  .then(bucketLocks).then(queues).then(staged).then(secrets).then(rollout).then(routedNow)
  .then(workers).then(retired).then(control).then(hook(false, ""));

/**
 * A routes run: the route plan read and its record kept before the first change, then the
 * host's DNS record and WorkOS's redirect, then a dark run's, then the handover, the custom
 * domain and the hook enabled.
 */
const ROUTED_HOOK = ", after the routes";
const ROUTES_LATER = READS_LATER.then(routePlan).then(recordedLater).then(hostRecord)
  .then(redirectLater).then(authorize).then(namespaces).then(buckets).then(bucketLocks)
  .then(queues).then(staged).then(secretsLater).then(rollout).then(workers).then(retired)
  .then(control).then(handOver).then(customDomain).then(hookLater(ROUTED_HOOK));
const ROUTED = READS.then(routePlan).then(recorded).then(hostRecord).then(redirect)
  .then(authorize).then(namespaces).then(buckets).then(bucketLocks).then(queues).then(staged)
  .then(secrets).then(rollout).then(workers).then(retired).then(control).then(handOver)
  .then(customDomain).then(hook(true, ROUTED_HOOK));

/**
 * A handback: a routes run's record read, held to the zone, handed back and read back, then
 * the host's records ended.
 */
const HANDBACK_LATER = Steps.none.then(zone).then(recordRead).then(backPlan).then(hookBackLater)
  .then(handBack);
const HANDBACK = Steps.none.then(zone).then(recordRead).then(e2b).then(hookListed).then(backPlan)
  .then(hookBack).then(handBack).then(handedBack).then(recordsEnded);

/** The operator path's modes: the same steps for the plan job, which applies nothing. */
const CONTROL = Steps.none.then(zone).then(controlEdit).list;
const OWNER = Steps.none.then(zone).then(ownerAsk).list;
const RESTORE = Steps.none.then(zone).then(restoreAsk).list;
const COPYBACK = Steps.none.then(zone).then(copyBackAsk).list;
const RECOVER = Steps.none.then(zone).then(recoverAsk).list;
const ERASE = Steps.none.then(zone).then(eraseAsk).list;
const ROTATION = Steps.none.then(zone).then(rotationSweep).list;
const RETIRE = Steps.none.then(zone).then(retireAsk).list;
const ROLLBACK = Steps.none.then(zone).then(rollbackAsk).list;

/** What the plan job holds: the Cloudflare token, its one `production` secret, and the vars. */
export const PUBLIC_INPUTS: readonly string[] = Object.freeze([
  "CLOUDFLARE_API_TOKEN", "WORKOS_CLIENT_ID", "OWNER_ISSUER"]);
/** What a deploy reads: every secret a config requires, and the optional ones when given. */
const DEPLOY_INPUTS: Inputs = Object.freeze({
  public: PUBLIC_INPUTS, secret: SECRET_INPUTS, optional: OPTIONAL });
const NOTHING = "\nNothing was changed.";

export const MODES: Readonly<Record<Mode, Row>> = Object.freeze({
  plan: {
    inputs: DEPLOY_INPUTS, deploys: true, applies: false, own: [],
    steps: { public: DARK_LATER.list, secrets: DARK.list },
    report: { unchanged: NOTHING, after: [] },
  },
  dark: {
    inputs: DEPLOY_INPUTS, deploys: true, applies: true, own: [],
    steps: { public: DARK_LATER.list, secrets: DARK.list },
    report: { unchanged: NOTHING, after: canary() },
  },
  routes: {
    inputs: DEPLOY_INPUTS, deploys: true, applies: true, own: [],
    steps: { public: ROUTES_LATER.list, secrets: ROUTED.list },
    report: { unchanged: NOTHING, after: canary("--side", "doors") },
  },
  // H2's probes read their own keys (`h2-probes.ts`), the revoke probe's two only if given.
  h2: {
    inputs: { public: [], secret: [H2_INPUTS.a, H2_INPUTS.b],
              optional: [H2_INPUTS.token, H2_INPUTS.keyId] },
    deploys: false, applies: true, own: [],
    steps: { public: [], secrets: [h2] }, report: null,
  },
  pair: {
    inputs: { public: [], secret: ["E2B_API_KEY"], optional: [] },
    deploys: false, applies: true, own: [],
    steps: { public: [], secrets: Steps.none.then(e2b).then(pairCheck).list }, report: null,
  },
  // The canary follows the old stack's restart.
  handback: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: ["E2B_API_KEY"], optional: [] },
    deploys: false, applies: true, own: ["--record"],
    steps: { public: HANDBACK_LATER.list, secrets: HANDBACK.list },
    report: { unchanged: NOTHING, after: [canaryAfter] },
  },
  // Read and deployed with the Cloudflare token alone; the canary follows, as after a deploy.
  rollback: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: [], optional: [] },
    deploys: false, applies: true, own: ["--record"],
    steps: { public: ROLLBACK, secrets: ROLLBACK },
    report: { unchanged: NOTHING, after: canary() },
  },
  // The control record, read and written with the Cloudflare token alone: the plan job reads
  // it too.
  control: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: [], optional: [] },
    deploys: false, applies: true, own: ["--set", "--replace"],
    steps: { public: CONTROL, secrets: CONTROL }, report: { unchanged: NOTHING, after: [] },
  },
  owner: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: [OPERATOR_KEYRING], optional: [] },
    deploys: false, applies: true, own: ["--owner", "--act"],
    steps: { public: OWNER, secrets: OWNER }, report: { unchanged: NOTHING, after: [] },
  },
  restore: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: [OPERATOR_KEYRING], optional: [] },
    deploys: false, applies: true, own: ["--owner", "--generation", "--confirm"],
    steps: { public: RESTORE, secrets: RESTORE }, report: { unchanged: NOTHING, after: [] },
  },
  // The copy's bucket and read key, and the R2 write pair minted for this restore alone.
  copyback: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN", ...BACK_INPUTS.public],
              secret: [OPERATOR_KEYRING, ...BACK_INPUTS.secret], optional: [] },
    deploys: false, applies: true, own: ["--owner", "--at"],
    steps: { public: COPYBACK, secrets: COPYBACK }, report: { unchanged: NOTHING, after: [] },
  },
  // As copyback's, in the new account after an account's loss: every owner, none named.
  recover: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN", ...BACK_INPUTS.public],
              secret: [OPERATOR_KEYRING, ...BACK_INPUTS.secret], optional: [] },
    deploys: false, applies: true, own: ["--at"],
    steps: { public: RECOVER, secrets: RECOVER }, report: { unchanged: NOTHING, after: [] },
  },
  erase: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: [OPERATOR_KEYRING], optional: [] },
    deploys: false, applies: true, own: ["--owner", "--confirm"],
    steps: { public: ERASE, secrets: ERASE }, report: { unchanged: NOTHING, after: [] },
  },
  rotation: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"],
              secret: [OPERATOR_KEYRING, ...Object.keys(ROTATED)], optional: [] },
    deploys: false, applies: true, own: ["--keyring"],
    steps: { public: ROTATION, secrets: ROTATION }, report: { unchanged: NOTHING, after: [] },
  },
  // The copy's nights are read with the job's own token, as the canary after it reads them.
  retire: {
    inputs: { public: ["CLOUDFLARE_API_TOKEN"], secret: [...Object.keys(ROTATED), TOKEN],
              optional: [] },
    deploys: false, applies: true, own: ["--keyring", "--confirm"],
    steps: { public: RETIRE, secrets: RETIRE }, report: { unchanged: NOTHING, after: canary() },
  },
});

// --- The plan ---------------------------------------------------------------------------

/** What a run asks, as `parse` reads it: its mode and its options. */
interface Asked {
  readonly mode: Mode;
  readonly rotate: readonly string[];
  readonly migrate: readonly string[];
  readonly record: string | null;
  readonly edits: readonly Edit[];
  readonly replace: unknown;
  readonly owner: string | null;
  readonly act: Act | null;
}

/**
 * A run's plan, the one its plan job shows and its apply job is held to: what the dispatch
 * asked (the founder's dispatch is the approval, `checklist.md`), and each line the row's
 * plan-job steps report. Each line is made from what its step read live and from this tree
 * alone, so the plan job and the apply job of one run plan the same over the same live state.
 * The run's id and the clock are in no line: the report's heading names the run, and the plan
 * file holds it beside the plan.
 */
interface Plan {
  readonly asked: Asked;
  readonly lines: readonly string[];
}

/** The plan file: the plan, and the run whose plan job wrote it. */
interface PlanFile {
  readonly kind: typeof PLAN_KIND;
  readonly run: string;
  readonly plan: Plan;
}

const PLAN_KIND = "tinyassets-plan";

function askedOf(run: Run): Asked {
  return { mode: run.mode, rotate: [...run.rotate].sort(), migrate: [...run.migrate].sort(),
           record: run.record, edits: run.edits, replace: run.replace, owner: run.owner,
           act: run.act };
}

/** Each difference of `live` from the `approved` plan, named: none when they are one plan. */
function planDifferences(approved: Plan, live: Plan): string[] {
  const asked = approved.asked as unknown as Readonly<Record<string, unknown>>;
  const now = live.asked as unknown as Readonly<Record<string, unknown>>;
  const differences = [...new Set([...Object.keys(asked), ...Object.keys(now)])]
    .filter((name) => JSON.stringify(asked[name]) !== JSON.stringify(now[name]))
    .map((name) => `the dispatch's ${name} was ${JSON.stringify(asked[name])}, and is ${
      JSON.stringify(now[name])}`);
  differences.push(
    ...approved.lines.filter((line) => !live.lines.includes(line))
      .map((line) => `approved, and not planned now: ${line}`),
    ...live.lines.filter((line) => !approved.lines.includes(line))
      .map((line) => `planned now, and never approved: ${line}`));
  if (differences.length === 0 && JSON.stringify(approved.lines) !== JSON.stringify(live.lines)) {
    differences.push("the approved lines, planned now in another order or number");
  }
  return differences;
}

/** The plan file `file` holds, in shape: missing, unreadable or out of shape, it throws. */
function planFile(file: string): PlanFile {
  let held: unknown;
  try {
    held = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    throw new Error(`there is no plan at ${file}: the apply job applies only the plan its run's ` +
                    "plan job wrote (--public --plan); nothing is changed");
  }
  const { kind, run, plan } = (held ?? {}) as Partial<Record<keyof PlanFile, unknown>>;
  const { asked, lines } = (plan ?? {}) as Partial<Record<keyof Plan, unknown>>;
  if (kind !== PLAN_KIND || typeof run !== "string" || typeof asked !== "object"
      || asked === null || !Array.isArray(lines)
      || !lines.every((line) => typeof line === "string")) {
    throw new Error(`${file} is not a plan the plan job wrote; nothing is changed`);
  }
  return held as PlanFile;
}

/**
 * The apply job's hold, before any change: the plan job's plan, made again from the live
 * state, is the one the plan job of this same run wrote to `run.plan`. Anything else stops it,
 * each difference named. Its lines, which this job's own steps are then held to as they say
 * each (`holding`): the plan job runs other steps, without the production secrets.
 */
async function approved(row: Row, run: Run, ctx: Context): Promise<readonly string[]> {
  const planJob: Run = Object.freeze({ ...run, reach: "public", apply: false });
  const live: Plan = { asked: askedOf(run),
                       lines: await stepsOf(row, planJob, ctx.scratch, null) };
  const file = planFile(run.plan!);
  if (file.run !== run.job.id) {
    throw new Error(`the plan at ${run.plan} is run ${file.run}'s, not this run's (${
      run.job.id}): the apply job applies only its own run's plan; nothing is changed`);
  }
  const differences = planDifferences(file.plan, live);
  if (differences.length > 0) {
    throw new Error(`the live state no longer plans what was approved, so nothing is changed: ${
      differences.join("; ")}. Dispatch the mode again for a new plan`);
  }
  ctx.say("the plan job's plan, made again from the live state, is the one approved, and each " +
          "line below is held to its line there as it is said");
  return file.plan.lines;
}

// --- The keyring tools ------------------------------------------------------------------

/** `keyring-new <file>`: a first keyring, written for the founder to store; never printed. */
export function keyringNew(file: string, log: Io["log"]): void {
  writeFileSync(file, ringText({ active: "k1", keys: new Map([["k1", randomBytes(KEY_BYTES)]]) }),
                { mode: 0o600, flag: "wx" });
  log(`wrote a new keyring to ${file}; store it as ${KEYRINGS.join(" or ")}, one ring each`);
}

/**
 * `keyring-add <file>`: the keyring in `file` with one more key under the kid after its
 * highest (never a retired kid's name), made active; the old kept.
 */
export function keyringAdd(file: string, log: Io["log"]): void {
  const ring = ringWith(keyring(readFileSync(file, "utf8"), "the keyring"),
                        randomBytes(KEY_BYTES));
  writeFileSync(file, ringText(ring), { mode: 0o600 });
  log(`added ${ring.active} to ${file}, active; store it as the keyring it was, then run with ` +
      "--rotate naming that keyring");
}

/**
 * `keyring-retire <file>`: the keyring in `file` with its active key alone, written beside it
 * (`<file>.retire`) for the founder to store before mode `retire`; the old file is left as it is,
 * the one way back for the kids retired (a kid's name is never used again, `ringWith`). A ring
 * of one key is refused: it has nothing to retire.
 */
export function keyringRetire(file: string, log: Io["log"]): void {
  const ring = keyring(readFileSync(file, "utf8"), "the keyring");
  if (ring.keys.size < 2) {
    throw new Error(`the keyring in ${file} holds its active key ${ring.active} alone: nothing ` +
                    "to retire");
  }
  const retired = [...ring.keys.keys()].filter((kid) => kid !== ring.active);
  writeFileSync(`${file}.retire`, ringText({ active: ring.active,
                                             keys: new Map([[ring.active, ring.keys.get(ring.active)!]]) }),
                { mode: 0o600, flag: "wx" });
  log(`wrote ${file}.retire, ${ring.active} alone (${retired.join(", ")} retired): keep ${file} ` +
      "offline, the one way back for them, store the new file as the keyring it was, then run " +
      "mode retire");
}

const TOOLS: Readonly<Record<string, (file: string, log: Io["log"]) => void>> = Object.freeze({
  "keyring-new": keyringNew, "keyring-add": keyringAdd, "keyring-retire": keyringRetire });

// --- The command line -------------------------------------------------------------------

/** The option `name`'s value: absent or empty, it names nothing. */
function option(argv: readonly string[], name: string): string {
  const at = argv.indexOf(name);
  return at < 0 ? "" : argv[at + 1] ?? "";
}

function names(argv: readonly string[], name: string): ReadonlySet<string> {
  return new Set(option(argv, name).split(",").filter(Boolean));
}

/**
 * The options a mode reads of its own (`Row.own`), each by what it names, and whether a mode
 * that reads it needs it.
 */
const OWN = Object.freeze({
  "--record": { names: "the record to hand back (a routes run's) or roll back (a dark or " +
                       "routes run's), by the run id its job summary names (letters, digits " +
                       "and -)", needed: true },
  "--set": { names: "the control record's edits", needed: false },
  "--replace": { names: "the whole control record, as JSON, that the edits start from in " +
                        "place of the live one", needed: false },
  "--owner": { names: "the owner principal the op is asked for", needed: true },
  "--act": { names: "the op asked of the doors", needed: true },
  "--generation": { names: "the generation to restore from, <namespace>/<gen> as " +
                           "restore.source answers them", needed: false },
  "--confirm": { names: "the words that confirm a restore's wipe, wipe-<owner>-<gen> as " +
                        "restore.pin answers them, or give its pin up, release-<owner>, " +
                        "an erasure, erase-<owner>, or a keyring's retirement, " +
                        "retire-<keyring>-<kids> as its apply job names them",
                 needed: false },
  "--keyring": { names: `the keyring swept or retired: ${Object.keys(ROTATED).join(" or ")}`,
                 needed: true },
  "--at": { names: "the time the off-account copy is read back as it stood at, " +
                   "<yyyy>-<mm>-<dd>T<hh>:<mm>:<ss>Z", needed: false },
});
export type Own = keyof typeof OWN;

/** The value `text` of the option `name` as its mode reads it, or a refusal naming the option. */
function ownValue(name: Own, text: string): unknown {
  const refused = () => new Error(`${name} is ${OWN[name].names}`);
  switch (name) {
    case "--record":
      if (!RECORD_ID.test(text)) {
        throw refused();
      }
      return text;
    case "--set":
      return controlEdits(text);
    case "--replace":
      try {
        return JSON.parse(text) as unknown;
      } catch {
        throw refused();
      }
    case "--owner":
      try {
        return principal(text);
      } catch {
        throw refused();
      }
    case "--act":
      return ownerAct(text);
    case "--generation":
      try {
        return restoreFrom(text);
      } catch {
        throw refused();
      }
    case "--confirm":
      if (!CONFIRM.test(text)) {
        throw refused();
      }
      return text;
    case "--keyring":
      if (!Object.hasOwn(ROTATED, text)) {
        throw refused();
      }
      return text;
    case "--at":
      if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(text) || Number.isNaN(Date.parse(text))) {
        throw refused();
      }
      return new Date(text);
  }
}

/**
 * The run `argv` asks for, over `env`: its mode and options, held to what the mode's row
 * takes. Every refusal is before any step. An option given empty is not given: the workflow
 * hands each dispatch input on as it is.
 */
export function parse(argv: readonly string[], env: Readonly<Record<string, string | undefined>>,
                      io: Io): Run {
  const at = argv.indexOf("--mode");
  const mode = at < 0 ? undefined : argv[at + 1];
  if (mode === undefined || !Object.hasOwn(MODES, mode)) {
    throw new Error(`--mode is one of ${Object.keys(MODES).join(", ")}`);
  }
  if (argv.indexOf("--mode", at + 1) >= 0) {
    throw new Error("--mode is given once: a run is one mode");
  }
  const row = MODES[mode as Mode];
  const own: Partial<Record<Own, unknown>> = {};
  for (const name of Object.keys(OWN) as Own[]) {
    const text = option(argv, name);
    if (row.own.includes(name)) {
      if (text === "" && OWN[name].needed) {
        throw new Error(`mode ${mode} needs ${name}: ${OWN[name].names}`);
      }
      own[name] = text === "" ? undefined : ownValue(name, text);
    } else if (text !== "" && row.report !== null) {
      const takers = (Object.keys(MODES) as Mode[]).filter((each) => MODES[each].own.includes(name));
      throw new Error(`mode ${mode} takes no ${name}: it is mode ${takers.length < 2 ? takers[0]
        : `${takers.slice(0, -1).join(", ")} and ${takers.at(-1)}`}'s`);
    }
  }
  const run: Run = Object.freeze({
    mode: mode as Mode, reach: argv.includes("--public") ? "public" : "secrets",
    apply: argv.includes("--yes"), plan: option(argv, "--plan") || null,
    job: runOf(env, io.now()), rotate: names(argv, "--rotate"),
    migrate: names(argv, "--migrate"), record: (own["--record"] as string | undefined) ?? null,
    edits: (own["--set"] as Edit[] | undefined) ?? [], replace: own["--replace"],
    owner: (own["--owner"] as string | undefined) ?? null,
    act: (own["--act"] as Act | undefined) ?? null,
    generation: (own["--generation"] as Generation | undefined) ?? null,
    confirm: (own["--confirm"] as string | undefined) ?? null,
    keyring: (own["--keyring"] as string | undefined) ?? null,
    at: (own["--at"] as Date | undefined) ?? null, env, io });
  if (row.report !== null) {
    if (run.apply && run.reach === "public") {
      throw new Error("--public is the plan job's: it never changes anything");
    }
    if (run.apply && !row.applies) {
      throw new Error(`mode ${mode} changes nothing: it takes no --yes`);
    }
    for (const worker of run.migrate) {
      if (!MIGRATES.includes(worker)) {
        throw new Error(`${worker} holds no Durable Object migration: --migrate names only ${
          MIGRATES.join(", ")}`);
      }
    }
    if (!row.deploys && (run.rotate.size > 0 || run.migrate.size > 0)) {
      throw new Error(`mode ${mode} deploys nothing: it takes no --rotate or --migrate`);
    }
  }
  if (run.apply && run.plan === null) {
    throw new Error("--yes applies the plan it was approved on: --plan names the file its run's " +
                    "plan job wrote");
  }
  if (!run.apply && run.reach === "secrets" && run.plan !== null) {
    throw new Error("--plan is the plan job's file (--public writes it) and the apply job's " +
                    "(--yes is held to it)");
  }
  return run;
}

/**
 * Each input the row reads on this run is set, before any step: the plan job's, and on a run
 * that reaches them the production secrets, each keyring among them one the gateway and the
 * files Worker would take.
 */
function given({ inputs }: Row, run: Run): void {
  const reached = run.reach === "secrets" ? inputs.secret : [];
  for (const name of [...inputs.public, ...reached]) {
    if (input(run, name) === undefined) {
      throw new Error(`${name} is not set (${reached.includes(name)
        ? "the production environment's secrets" : "the workflow's inputs"})`);
    }
  }
  for (const name of KEYRINGS.filter((each) => reached.includes(each))) {
    keyring(input(run, name), name);
  }
}

/**
 * The lines `row`'s steps for `run` report, run in order, each given what the ones before made;
 * on the apply job each held to the `approved` plan as it is said (`holding`).
 */
async function stepsOf(row: Row, run: Run, scratch: () => string,
                       approved: readonly string[] | null): Promise<string[]> {
  const lines: string[] = [];
  const hold = approved === null ? null : holding(approved);
  await chained(row.steps[run.reach], { ...reporterOf(run, lines, hold), run, scratch });
  hold?.done();
  return lines;
}

/** `steps` run in order, each given what the ones before made (`Steps`). */
async function chained(steps: readonly Held[], ctx: Context): Promise<void> {
  const values: Partial<Values> = {};
  for (const each of steps) {
    Object.assign(values, await each.run(ctx, values as Pick<Values, never>));
  }
}

/**
 * Runs the row's steps, then reports its plan: the plan job's written to `--plan`, the apply
 * job's held to that one first (`approved`). An applied run then runs what follows its report.
 * What the run wrote outside the checkout is removed at its end.
 */
export async function perform(run: Run): Promise<{ readonly plan: readonly string[] }> {
  const row = MODES[run.mode];
  const plan: string[] = [];
  let scratch: string | undefined;
  const ctx: Context = {
    ...reporterOf(run, plan), run,
    scratch: () => scratch ??= mkdtempSync(join(tmpdir(), "tinyassets-deploy-")),
  };
  try {
    given(row, run);
    const lines = run.apply ? await approved(row, run, ctx) : null;
    plan.push(...await stepsOf(row, run, ctx.scratch, lines));
    if (!run.apply && run.plan !== null) {
      const file: PlanFile = { kind: PLAN_KIND, run: run.job.id,
                               plan: { asked: askedOf(run), lines: plan } };
      writeFileSync(run.plan, `${JSON.stringify(file, null, 2)}\n`);
    }
    if (row.report !== null) {
      report(ctx, plan, run);
      if (!run.apply) {
        run.io.log(row.report.unchanged);
      }
      await chained(run.apply ? row.report.after : [], ctx);
    }
  } finally {
    if (scratch !== undefined) {
      rmSync(scratch, { recursive: true, force: true });
    }
  }
  return { plan };
}

async function main(argv: readonly string[]): Promise<void> {
  const tool = TOOLS[argv[0] ?? ""];
  if (tool !== undefined) {
    if (argv[1] === undefined) {
      throw new Error(`${argv[0]} <file>`);
    }
    tool(argv[1], IO.log);
    return;
  }
  await perform(parse(argv, { ...process.env }, IO));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error: Error) => {
    console.error(`deploy stopped: ${error.message}`);
    process.exit(1);
  });
}
