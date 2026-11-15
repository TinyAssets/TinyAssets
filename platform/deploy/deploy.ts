/**
 * The platform's deploy kit (tasks 5, "0c (live, slice L)"; tasks 2, "DNS, routes and
 * deploy"): the six platform Workers deployed to the production Cloudflare account, the
 * doors' routes handed over at the cutover, the handles canary after each change (AGENTS.md
 * Fact 11), and H2's probes (`h2-probes.ts`).
 *
 * It runs from the manual workflow `.github/workflows/platform-deploy.yml`, from `main`, one
 * mode a run (`MODES`, each a row of steps):
 *
 *   node --experimental-strip-types platform/deploy/deploy.ts --mode <mode> [--public | --yes]
 *        [--rotate <secrets>] [--migrate <Workers>] [--record <id>] [--set <edits>]
 *        [--replace <record>] [--owner <owner> --act <act>] [--keyring <keyring>]
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-new <file>
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-add <file>
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-retire <file>
 *
 * - `plan`: `dark`'s checks and plan, never a change.
 * - `dark`: storage, secrets and every Worker with no public route, the control record when
 *   absent, the WorkOS redirect URI and E2B's lifecycle webhook (disabled), then the handles
 *   canary.
 * - `probe`: after `dark`, the sign-in's two paths alone handed to the doors (`PROBE_ROUTES`),
 *   nothing deployed and no gate changed, then the canary and the one sign-in link to open,
 *   with its pass and fail signs (`PROBE_SIGNS`).
 * - `routes`: `dark`, then the doors' routes handed over one at a time, the egress custom
 *   domain, and the webhook enabled. Before any change it keeps a record of each of those
 *   three as they stood (`RouteRecord`), and the job summary names its id.
 * - `handback`: the record `--record` names, handed back (the cutover's rollback): the webhook
 *   disabled and the egress custom domain detached unless the record has them so before, then
 *   the zone's routes put back as the record has them, only what the handover changed, one at
 *   a time; each read back and held to the record. Each is held to the record before any
 *   change too: anything changed since `routes` stops it, named. The handles canary follows
 *   the old stack's restart, so the run ends saying where it runs.
 * - `h2`: H2's probes alone, once a probe owner exists.
 * - `pair`: the pinned template with the runner this checkout ships and the probes beside
 *   it, checked together on throwaway sandboxes unless E2B already holds their pair's tag
 *   (`sandbox/template/checks.ts pair`).
 * - `control` (the operator path, `operator.ts`): the control record edited in KV by
 *   `--set`'s edits, from `--replace`'s whole record in place of the live one, never through
 *   the doors; written only when `parseControl` takes it, the record it replaces kept first
 *   and the write read back.
 * - `owner` (the operator path): `--act`'s op asked of the doors for `--owner` through the
 *   operator channel, and their answer said, with the owner's state before and after.
 * - `restore` (the operator path): `--owner`'s restore from `--generation` through the
 *   operator channel: a live owner pinned, held and its present state written, its object
 *   wiped only with `--confirm` the words that names; then begun, stepped until done and the
 *   owner admitted. With no `--generation`, the owner's namespace and finished generations,
 *   as its object holds them, or with `--confirm release-<owner>` its pin given up.
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
 * naming each difference, unless it is that file's plan, of this same run.
 *
 * Every lookup is checked: a failed listing or read stops the kit loudly, never reads as
 * "absent". So:
 * - a namespace, bucket or queue is created only when its listing answered and lacks it, and
 *   a locked bucket's one lock rule (`platform.json` `bucket_locks`) set only when its rules
 *   answered and are not that rule alone;
 * - a secret is set only on a Worker whose secret listing answered and lacks it, or that the
 *   account's Workers listing lacks (whether a Worker exists is that listing's word alone);
 *   the kit never makes a secret's value (`keyring-new` makes a first keyring,
 *   `scripts/platform_setup.py` stores one). A held one is never replaced
 *   unless `--rotate` names it, a keyring only by adding a key (mode `retire` alone takes its
 *   old kids out); Cloudflare never shows a held
 *   secret, so the kit cannot check that a rotated keyring keeps the deployed key;
 * - the control record is written only when the API answered that it is absent;
 * - the WorkOS redirect URI is registered only when WorkOS's listing lacks it (GET, then
 *   POST; never a delete), after the other live checks and before any Cloudflare change, and
 *   only then is the early sign-in check run, which needs it listed;
 * - E2B's lifecycle webhook is listed and checked before any change, registered disabled only
 *   when E2B's listing lacks it, its secret changed only with the doors' copy, and enabled
 *   only by `routes` after the handover; the run that registers it sets the doors' copy too;
 * - nothing is deployed unless every migration lock (`owner/migrations.json`, and the sandbox
 *   Worker's `sandbox/migrations/<class>.json`) marks the first deploy and pins every
 *   migration the code ships, in order (`lockRefusal`, by `ci/kit/locks.ts`);
 * - a Worker's first deploy is `wrangler deploy`; later ones upload a version, then deploy it,
 *   which never touches a route; `--migrate` names a Worker with a pending Durable Object
 *   migration to deploy whole;
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
 * The access token's exact `iss` and `auth_time` only a real sign-in shows: `probe` settles
 * them.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { PLATFORM, REPO } from "../ci/paths.ts";
import { migrated } from "../ci/kit/locks.ts";
import { WRANGLER } from "../ci/wrangler.ts";
// The envelope and the E2B client import only by their own names, so Node's type stripping
// runs them here.
import { closedToAll, CONTROL_KEY, parseControl } from "../contract/src/control.ts";
import { ZONE } from "../contract/src/egress.ts";
import { KEY_BYTES, parseRing, type Ring, ringText, ringWith } from "../contract/src/envelope.ts";
import {
  APP_RESOURCE, UPSTREAM_CALLBACK, WEB_CLIENT, WEB_REDIRECT,
} from "../contract/src/identity.ts";
import { IMPORT_KEY } from "../contract/src/import-message.ts";
import {
  argsText, OPERATOR_KEYRING, type OperatorArgs,
} from "../contract/src/operator-message.ts";
import {
  eraseWords, type RestoreAnswer, type RestoreHold, type RestoreSource,
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
  type LockRule, type Moved, narrower, type Route, type RouteChange, unlike,
} from "./cloudflare.ts";
import {
  AUTHORIZE, BUCKET_LOCKS, BUCKETS, CONFIGS, DOORS, E2B_HOOK_URL, EGRESS_DOMAIN, FILES_BUCKET,
  IDENTITY, KEYRINGS, MIGRATES, NAMES, NAMESPACES, OPTIONAL, PROBE_ROUTES, QUEUES,
  RETIRED, ROTATED, ROUTES, SECRET_INPUTS, SECRETS, SHARED, WORKER_FACTS, WORKERS,
} from "./facts.ts";
import { INPUTS as H2_INPUTS } from "./h2-probes.ts";
import {
  BACK_INPUTS, backFrom, copyBack, firstNightAfter, KEEP_DAYS, readableAt, RESTORE_KEY,
  restoreBegan, type Watch, watch,
} from "./offsite.ts";
import {
  type Act, askOperator, controlEdits, differences, type Edit, edited, type Generation,
  ERASE_LOSES, heldRotation, keyHashes, ownerAct, RESTORE_LOSES, restoreFrom, rotate,
  type RotationRecord, rotationKey, rotationRecord, RUN_MS,
} from "./operator.ts";
/**
 * The control record a first deploy writes when there is none: every outside door closed, and
 * the founder's tier numbers. The founder's data, so a file of its own, which the tests read too.
 */
import DARK_CONTROL from "./control.json" with { type: "json" };

type Json = Record<string, unknown>;
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/**
 * E2B's sandbox lifecycle webhook, the doors' one E2B hook (`doors/src/e2b-hooks.ts`), its
 * API read in e2b-dev/infra's `spec/openapi.yml` (2026-11-04).
 */
export const E2B_HOOK = Object.freeze({
  name: "tinyassets-lifecycle", url: E2B_HOOK_URL,
  events: Object.freeze(["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"]),
  /** Its signature secret, which the Worker whose config requires it holds a copy of. */
  secret: "E2B_WEBHOOK_SECRET",
});
/** What the plan job says of a check that needs a production secret. */
const LATER = "checked by the apply job before any change";

// --- The run ----------------------------------------------------------------------------

/** The outside world the kit acts on: replaced whole by the tests. */
export interface Io {
  readonly fetch: Fetch;
  readonly spawn: typeof spawnSync;
  readonly exec: typeof execFileSync;
  /** The runner and the probes this tree builds, and its image (`sandbox/template/pair.ts`). */
  readonly bundles: () => Bundles;
  readonly treeImage: () => string;
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
  log: (line: string) => console.log(line),
  now: () => new Date(),
  sleep: (ms: number) => new Promise<void>((done) => { setTimeout(done, ms); }),
});

export type Mode = "plan" | "dark" | "probe" | "routes" | "h2" | "pair" | "handback" | "control"
  | "owner" | "restore" | "copyback" | "erase" | "rotation" | "retire";

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
  /** `--record`: the id of the route record `handback` hands back. */
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
  /** `--at`: the time `copyback` reads the off-account copy as it stood at; none, as it stands. */
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
  /** The route record `routes` keeps, and its key: null on a run that changes nothing. */
  readonly kept: KeptRecord | null;
  /** The KV namespaces' ids, by binding: the account's, and those this run made. */
  readonly ids: Readonly<Record<string, string>>;
  /** Each Worker's config as it deploys. */
  readonly stage: Stage;
  /** The account's Workers, by name, as its listing answered before any deploy. */
  readonly deployed: readonly string[];
  /** The secrets the run sets, each on its Worker. */
  readonly setting: readonly { worker: string; name: string }[];
  /** The route record `handback` reads, and its handback on the zone as it stands. */
  readonly record: RouteRecord;
  readonly back: readonly RouteChange[];
  /** The doors' custom domains on the egress host the handback detaches. */
  readonly detach: readonly { id: string; service: string }[];
}

/**
 * The run's one reporter: its plan, the job's summary and the masks in its log, each through
 * `actions.ts`, which holds what is GitHub's.
 */
export interface Reporter {
  /** Adds lines to the run's plan. */
  readonly say: (...lines: string[]) => void;
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

/** The reporter of `run`, its plan's lines gathered in `plan`. */
function reporterOf(run: Run, plan: string[]): Reporter {
  return Object.freeze({
    say: (...lines: string[]) => { plan.push(...lines); },
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
 * Why `worker` may not deploy under its migration locks, or null: the locks' command line
 * (`ci/kit/locks.ts digests`) refuses unless each lock marks the first deploy and pins
 * exactly the migrations the code ships, in order, and unless the Worker states a lock.
 */
export function lockRefusal(worker: string, run: Run): string | null {
  const result = run.io.spawn(process.execPath,
                              [join(PLATFORM, "ci", "kit", "locks.ts"), "digests", worker],
                              { cwd: PLATFORM, encoding: "utf8", env: {} });
  return result.status === 0 ? null : `${worker}'s migration locks refuse the deploy ` +
    `(exit ${result.status}): ${String(result.stderr).trim()}`;
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
 * alone (`token_issuer`); a wrong one is the sign-in probe's 502, which names the issuer the
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
    // Any other spelling refuses every sign-in (tasks 2): stop before deploying it.
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
                   // WorkOS's docs and SDK fixtures (research, 2026-11-06); the probe proves it.
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
 * is reported as `dark`'s to register, not a stop; every other refusal still stops.
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
  if (unlisted && `${to.origin}${to.pathname}` === REDIRECT_UNLISTED) {
    return `WorkOS's authorize does not take ${UPSTREAM_CALLBACK} yet: dark registers it, ` +
           "then checks the client and it again";
  }
  if (to.protocol !== "https:" || to.host !== host || to.searchParams.has("error")
      || /error/i.test(to.pathname)) {
    throw new Error(`WorkOS's authorize refused the doors' client or ${UPSTREAM_CALLBACK}: it ` +
                    `sent the browser to ${to.origin}${to.pathname}, not ${host}'s sign-in`);
  }
  return `WorkOS's authorize takes the doors' client and ${UPSTREAM_CALLBACK}: it sent the ` +
         `browser to ${host}'s sign-in`;
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
 * (missing on the doors, named in `--rotate`, or the hook missing at E2B), so the two never
 * drift apart.
 */
export function hookSetsSecret(setting: readonly { worker: string; name: string }[]): boolean {
  return setting.some((each) => each.name === E2B_HOOK.secret);
}

/**
 * E2B's lifecycle webhook as its listing holds it, matched by its URL, or null when it holds
 * none. A failed listing, or two hooks for the URL, throws.
 */
export async function heldHook(e2b: E2bAdmin): Promise<Json | null> {
  const hooks = await e2b.webhooks();
  const ours = hooks.filter((hook) => (hook as Json | null)?.["url"] === E2B_HOOK.url) as Json[];
  if (ours.length > 1) {
    throw new Error(`E2B holds ${ours.length} webhooks for ${E2B_HOOK.url}: keep one`);
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
 * hook's secret, so the two change together or not at all); its events when they differ;
 * and it is enabled with `enable`. It is never deleted, and never disabled here: only a
 * handback disables it, back to the state its route record kept. Without `apply` it only
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
    ...(JSON.stringify(events) !== JSON.stringify([...E2B_HOOK.events].sort())
      ? { events: [...E2B_HOOK.events] } : {}),
    ...(enable && hook["enabled"] !== true ? { enabled: true } : {}),
  };
  const state = hook["enabled"] === true || enable ? "enabled" : "disabled";
  const what = Object.keys(change).length === 0 ? "unchanged"
    : `${Object.keys(change).map((field) => field === "signatureSecret" ? "its secret"
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

/** The version id `wrangler versions upload` prints, or a throw. */
export function versionOf(output: string): string {
  const id = /Worker Version ID:\s*([0-9a-f-]{36})/.exec(output)?.[1];
  if (id === undefined) {
    throw new Error("wrangler versions upload printed no Worker Version ID");
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

// --- The probe --------------------------------------------------------------------------

/**
 * The probe's handover (mode `probe`): `PROBE_ROUTES` alone, each added for the doors, or
 * left when the doors hold it already. A path another Worker (or no Worker) serves, by the
 * same, a narrower or a wider pattern, stops it: the probe takes only paths nothing serves.
 */
export function probeSteps(routes: readonly Route[]): RouteChange[] {
  const served = routes.filter((route) => route.script !== DOORS && PROBE_ROUTES.some(
    (pattern) => route.pattern === pattern || narrower(route.pattern, pattern)
      || narrower(pattern, route.pattern)));
  if (served.length > 0) {
    throw new Error(`the probe takes only paths nothing serves, and ${served.map((route) =>
      `${route.pattern} is ${route.script ?? "no Worker"}'s`).join(", ")}: nothing is changed`);
  }
  return handover(routes, DOORS, PROBE_ROUTES);
}

/**
 * The one first-party sign-in link the probe prints: the doors' own app's authorization
 * (`doors/app/api.js`), with a fresh S256 challenge whose verifier is dropped, so no code it
 * could end in is ever redeemable.
 */
export function probeLink(): string {
  const { state, challenge } = throwaway();
  const query = new URLSearchParams({
    response_type: "code", client_id: WEB_CLIENT, redirect_uri: WEB_REDIRECT,
    state, code_challenge: challenge, code_challenge_method: "S256",
    resource: APP_RESOURCE });
  return `${AUTHORIZE}?${query}`;
}

/**
 * A fresh state and S256 challenge, the challenge's verifier dropped here, so no code a flow
 * started with them could end in is ever redeemable (the probe's link, `authorizeCheck`).
 */
export function throwaway(): { state: string; challenge: string } {
  return { state: randomBytes(16).toString("base64url"),
           challenge: createHash("sha256").update(randomBytes(32).toString("base64url"))
             .digest("base64url") };
}

/** What the probe's sign-in shows, read from where the browser ends. */
export const PROBE_SIGNS = [
  `Pass: the browser's address begins ${WEB_REDIRECT}?error=temporarily_unavailable ` +
  "(more follows it; the page itself is the old app's and reads as a sign-in error, " +
  "which is expected): the closed gate refused the sign-in after every " +
  "check passed, and no owner was made. The doors' client and API key were taken at User " +
  "Management's authenticate, the callback was its registered redirect, and the access " +
  "token's signature, iss, client_id, sub (the answer's user.id) and auth_time held.",
  `Fail, at ${AUTHORIZE}: a 400 page, so the doors could not read their own ` +
  "app's client document.",
  "Fail, at error.workos.com or another error page before or after the sign-in: WorkOS " +
  "refused the doors' client or the callback.",
  `Fail, at ${UPSTREAM_CALLBACK}: a 502 page naming the check that failed (the token endpoint's ` +
  "status and error code, auth_time, client_id...), the same line in the doors' log. On " +
  "issuer, it names the iss the token carries: that value becomes upstreamFrom's " +
  "token_issuer.",
  `Fail, at ${WEB_REDIRECT}?error=access_denied ("sign in again"): auth_time is more than ` +
  `${SIGN_IN_MAX_AGE_S} s old or ahead of the clock, so max_age=0 did not force a fresh ` +
  "sign-in.",
].join("\n");

// --- The route record -------------------------------------------------------------------

/**
 * What `routes` keeps before it changes anything, so `handback` can undo it (the import
 * research's rollback, Fact 13): the before-state of every outward change `routes` makes that
 * `dark` does not, and the zone, the run and its time. Those changes, from its row:
 * - the zone's routes (`handOver`, and `customDomain`'s `wrangler triggers deploy`, which sets
 *   the doors' routes to the config's, the same patterns): every route as the route plan read
 *   it, each pattern and its Worker, and the changes the handover makes;
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
 * kit's own record store (the control record is written there, and read back by a later
 * `probe`): the kit writes it itself, before its first change, over the client and token it
 * already holds, so a refused write stops the run before anything is changed, and `handback`
 * reads it with that same token, with no host and no GitHub API in between. Its key is the
 * run's id (`actions.ts` `runOf`), which a re-run attempt keeps, and the job summary names it.
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
  /** E2B's lifecycle webhook: whether it was held, and enabled. */
  readonly hook: { readonly url: string; readonly present: boolean; readonly enabled: boolean };
}

const RECORD_KIND = "tinyassets-routes";
/** A route record's key in the control namespace, after its run's id. */
const RECORD_PREFIX = "deploy:routes:";
const RECORD_ID = /^[A-Za-z0-9-]{1,64}$/;

/**
 * The record the run `job` keeps at `at` of `table` and its handover `steps` on the zone
 * `zoneId`, the egress domain's Worker `domain` and E2B's `hook` as listed.
 */
function routeRecord(job: JobRun, at: Date, zoneId: string, table: readonly Route[],
                     steps: readonly RouteChange[], domain: string | null,
                     hook: Json | null): RouteRecord {
  return {
    kind: RECORD_KIND, zone: ZONE, zone_id: zoneId, run: job.id, attempt: job.attempt,
    at: at.toISOString(),
    routes: table.map((route) => ({ pattern: route.pattern, script: route.script ?? null })),
    moved: steps.map((each) => ({ method: each.method, pattern: each.pattern })),
    domain: { hostname: EGRESS_DOMAIN, service: domain, created: null },
    hook: { url: E2B_HOOK.url, present: hook !== null, enabled: hook?.["enabled"] === true },
  };
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
  if (typeof domain !== "object" || domain === null || domain.hostname !== EGRESS_DOMAIN
      || !(domain.service === null || typeof domain.service === "string")
      || !(domain.created === null || (Array.isArray(domain.created)
        && (domain.created as Partial<DnsRecord>[]).every((each) => typeof each?.id === "string"
          && typeof each.type === "string" && typeof each.content === "string")))
      || (domain.created !== null && domain.service !== null)) {
    throw bad("holds no egress domain in shape");
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
  const held = await cloudflare.domains(account, EGRESS_DOMAIN);
  const others = held.filter((domain) => domain.service !== DOORS);
  if (others.length > 0) {
    throw new Error(`${EGRESS_DOMAIN} is ${others.map((domain) => domain.service).join(", ")}'s ` +
                    "custom domain: routes would take it, and a handback could not give it " +
                    "back; nothing is changed");
  }
  if (held.length > 0) {
    return DOORS;
  }
  const records = await cloudflare.dnsRecords(id, EGRESS_DOMAIN);
  if (records.length > 0) {
    throw new Error(`${EGRESS_DOMAIN} holds ${records.length} DNS record(s) (${records.map(
      (record) => record.type).join(", ")}): the doors' custom domain would replace them, and ` +
      "a handback could not give them back; nothing is changed");
  }
  return null;
}

// --- The steps --------------------------------------------------------------------------

/** No Worker deploys a migration its lock does not pin, checked before anything is changed. */
const locks = step(({ run, say }) => {
  const workers = migrated();
  for (const worker of workers) {
    const refused = lockRefusal(worker, run);
    if (refused !== null) {
      throw new Error(refused);
    }
  }
  say(`the migration locks pin every migration the ${workers.join(" and ")} Workers ship`);
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

/** The run's one E2B client, from its key. */
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
});

/** The plan job's: the pinned template is this tree's image; its build is the apply job's. */
const templateLater = step((ctx) => {
  ctx.say(`E2B's ready build of ${pinnedTree(ctx)}: ${LATER}`);
});

/** The pair this deploy makes (`shippedPair`), passed. */
const pairReady = step<"e2b">(async ({ run, say }, { e2b: admin }) => {
  const pair = shippedPair(run.io);
  if (await admin.ready(pair)) {
    say(`the pair ${pair} passed its standing checks in this E2B team`);
  } else if (!run.apply) {
    say(`the pair ${pair} has not passed in this E2B team: the pair job runs its check ` +
        "(--mode pair) before the apply job");
  } else {
    throw new Error(`the pair ${pair} has not passed its standing checks in this E2B team: ` +
                    "run the pair job (--mode pair) first; nothing is deployed");
  }
});

const pairLater = step(({ run, say }) => {
  say(`the pair ${shippedPair(run.io)} passed its standing checks: ${LATER}`);
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
  say(`WorkOS redirect URI ${UPSTREAM_CALLBACK}: ${state}`);
  return { listed: state !== "missing" };
});

const redirectLater = step<never, "listed">(({ say }) => {
  say(`WorkOS redirect URI ${UPSTREAM_CALLBACK}: ${LATER}`);
  return { listed: false };
});

/**
 * The early sign-in check: a callback the run could not list, or found missing, is one a
 * redirect-uri-invalid answer leaves to `dark` to register.
 */
const authorize = step<"upstream" | "listed">(async ({ run, say }, got) => {
  say(await authorizeCheck(got.upstream, !got.listed, run.io.fetch));
});

/** The doors' routes' handover, from the zone's routes as they stand. */
const routePlan = step<"cloudflare" | "zone", "table" | "changes">(async (_ctx, got) => {
  const table = await got.cloudflare.routes(got.zone);
  return { table, changes: handover(table, DOORS, ROUTES) };
});

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

type Recording = "cloudflare" | "zone" | "account" | "table" | "changes";

/**
 * The route record (`RouteRecord`), kept before any change: written when the handover moves
 * a route and the run's record is not there yet, and kept as it is when it is (a re-run
 * attempt keeps the table its first attempt read). A record that cannot be written stops the
 * run, nothing changed. `hook` is E2B's listing of the lifecycle hook, or undefined in the plan
 * job, which cannot list it and never writes.
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
  const held = await got.cloudflare.value(got.account, namespace, key);
  if (held !== undefined) {
    const record = recordOf(held, key, got.zone);
    say(`keep this run's route record as attempt ${record.attempt ?? "-"} wrote it at ` +
        `${record.at}, before its first change: hand back with --mode handback --record ` +
        "<this run's id>");
    return { kept: { record, namespace, key } };
  }
  // Doors on more of their routes than the probe's: an earlier routes run moved them.
  const earlier = got.table.filter((route) => route.script === DOORS
    && ROUTES.includes(route.pattern) && !PROBE_ROUTES.includes(route.pattern))
    .map((route) => route.pattern);
  const word = hook === undefined ? LATER : hook === null ? "absent"
    : hook["enabled"] === true ? "enabled" : "disabled";
  say(`record the zone's ${got.table.length} routes and the handover's ${got.changes.length} ` +
      `changes, the egress domain (${domain ?? "none"}) and E2B's lifecycle webhook (${word}) ` +
      `as this run's route record in ${title}, before any change: hand back with --mode ` +
      `handback --record <this run's id>${
        earlier.length === 0 ? "" : `; ${DOORS} holds ${earlier.join(", ")} already: the ` +
        "record of the routes run that moved them is the one to hand back, and it covers this " +
        "run's changes too"}`);
  // The plan job (no listing of the hook) never applies.
  if (!run.apply || hook === undefined) {
    return { kept: null };
  }
  const record = routeRecord(run.job, run.io.now(), got.zone, got.table, got.changes, domain,
                             hook);
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

/** The R2 buckets the configs bind. */
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

/** The queues the configs send to. */
const queues = storage("queue", QUEUES,
                       async (cloudflare, account) => new Set((await cloudflare.queues(account))
                         .keys()),
                       (cloudflare, account, queue) => cloudflare.makeQueue(account, queue));

/** Each Worker's config as it deploys (`Stage`). */
const staged = step<"ids" | "upstream", "stage">((ctx, got) => ({
  stage: stageOf(ctx.scratch, got.ids, got.upstream) }));

type Secrets = "cloudflare" | "account" | "wrangler" | "stage";

/**
 * Each Worker's missing secrets (and the rotated), set first: `secrets.required` refuses a
 * deploy. Whether a Worker exists is the account's Workers listing's word: a missing one holds
 * none. `values` is the run's secrets, or null in the plan job, which holds none: an optional
 * secret (a shared app's, the canary clock's) the run is not given is left unset.
 */
async function setSecrets({ run, say }: Context, got: Pick<Values, Secrets>,
                          values: ((name: string) => string | undefined) | null,
                          rotate: ReadonlySet<string>):
    Promise<Pick<Values, "deployed" | "setting">> {
  const deployed = await got.cloudflare.workers(got.account);
  const held: Record<string, string[]> = {};
  for (const worker of Object.keys(SECRETS)) {
    held[worker] = deployed.includes(NAMES[worker]!)
      ? await got.cloudflare.secrets(got.account, NAMES[worker]!) : [];
  }
  const setting = secretPlan(held, rotate, values);
  for (const { worker, name } of setting) {
    const value = values?.(name);
    if (values !== null && value === undefined) {
      continue;
    }
    say(`set the secret ${name} on ${NAMES[worker]}${values !== null ? ""
      : ` (its value, or its absence if optional: ${LATER})`}`);
    if (run.apply) {
      got.wrangler(["secret", "put", name, "--config", got.stage.config(worker)], value);
    }
  }
  return { deployed, setting };
}

/** The secrets, the hook's with them when E2B holds no hook: its register takes production's. */
const secrets = step<Secrets | "hook", "deployed" | "setting">((ctx, got) =>
  setSecrets(ctx, got, (name) => input(ctx.run, name), got.hook === null
    ? new Set([...ctx.run.rotate, E2B_HOOK.secret]) : ctx.run.rotate));
const secretsLater = step<Secrets, "deployed" | "setting">((ctx, got) =>
  setSecrets(ctx, got, null, ctx.run.rotate));

/**
 * Each Worker deployed, bound first, touching no route. A first deploy (the account's Workers
 * listing lacks it) is `wrangler deploy`; later ones are Cloudflare's staged path,
 * `versions upload` then `versions deploy` at 100%, which leaves the Worker's routes as they
 * are. Wrangler 4.154 refuses a versions upload with a pending Durable Object migration:
 * `--migrate <worker>` deploys it whole.
 */
const workers = step<"wrangler" | "deployed" | "stage">(({ run, say }, got) => {
  for (const worker of WORKERS) {
    const whole = !got.deployed.includes(NAMES[worker]!) || run.migrate.has(worker);
    say(`${whole ? "deploy" : "upload and deploy a version of"} ${NAMES[worker]}, touching no ` +
        "route");
    if (run.apply) {
      const config = got.stage.config(worker);
      if (whole) {
        got.wrangler(["deploy", "--config", config]);
      } else {
        const id = versionOf(got.wrangler(["versions", "upload", "--config", config]));
        got.wrangler(["versions", "deploy", `${id}@100`, "--yes", "--config", config]);
      }
    }
  }
});

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

/** The control record, written closed only when the API answered there is none. */
const control = step<"cloudflare" | "account" | "ids">(async ({ run, say }, got) => {
  const id = got.ids["CONTROL"];
  if (id === undefined || await got.cloudflare.value(got.account, id, CONTROL_KEY) === undefined) {
    say("write the control record closed (outside off, canary none, inbound off, the " +
        "founder's tier numbers): the API answered there is none");
    if (run.apply) {
      await putOrStop(got, id!, CONTROL_KEY, JSON.stringify(DARK_CONTROL));
    }
  }
});

// --- The operator path (`operator.ts`) -----------------------------------------------------

/** Where a `control` run keeps the record it replaced, in the control namespace, by run id. */
const KEPT_PREFIX = "deploy:control:";

/**
 * Mode `control`: the control record edited in KV, never through the doors, so the kill
 * switch works when they don't. The edits apply to the live record, or to `--replace`'s whole
 * record in its place, which a missing or malformed live record needs; the result is written
 * only when `parseControl`, every reader's parser, takes it and it differs. Before the write
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
  if (run.replace === undefined && parseControl(live) === null) {
    throw new Error(`the control record is ${held === undefined ? "missing"
      : `one no reader takes: ${held}`}; give the whole record with --replace; nothing is ` +
                    "changed");
  }
  const record = edited(run.replace ?? live, run.edits);
  const control = parseControl(record);
  if (control === null) {
    throw new Error("the control record as edited is one no reader takes (parseControl): " +
                    `${JSON.stringify(record)}; nothing is changed`);
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
  const text = JSON.stringify(record);
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
  say(`ask the doors ${op} ${argsText(args)} for ${owner}, through the operator channel, ` +
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
    say(`ask the doors ${op} for ${owner}, through the operator channel`);
    if (run.apply) {
      say(`the doors answered ${op} for ${owner}: ${JSON.stringify(await ask(op))}`);
    }
    return;
  }
  say(`restore ${owner} from generation ${from.gen} of namespace ${from.namespace}, into its ` +
      "own object only while it is pending and empty (a live owner held first, its present " +
      "state written as a fresh generation, and its object wiped once --confirm names that " +
      "generation), then admit it, through the operator channel: " + RESTORE_LOSES);
  if (!run.apply) {
    return;
  }
  const source = await ask("restore.source") as RestoreSource;
  const held = source.restore;
  if (held?.step === "done" && held.gen === from.gen) {
    say(`${owner} was restored from ${from.gen} already: the doors answered owner.admit: ${
      JSON.stringify(await ask("owner.admit"))}`);
    return;
  }
  if (!source.begins) {
    const until = run.io.now().getTime() + RUN_MS;
    let hold = await ask("restore.pin", { gen: from.gen }) as RestoreHold;
    while (!hold.written) {
      if (run.io.now().getTime() >= until) {
        throw new Error(`${owner}'s present state was not written within ${RUN_MS / 60_000} ` +
                        "minutes, and the owner stays held: run again to carry it on, or give " +
                        `the pin up with --confirm release-${owner} and no --generation`);
      }
      say(`${owner} is held; its present state is being written as a fresh generation`);
      hold = await ask("restore.pin", { gen: from.gen }) as RestoreHold;
    }
    if (run.confirm === null) {
      say(`${owner} is held, its present state written as generation ${hold.fresh} of ` +
          `${hold.namespace}: to wipe its object and restore ${from.gen}, run again with ` +
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
 * Then mode `restore` restores a generation from it, should the owner's database go back too.
 * The plan job only plans it.
 */
const copyBackAsk = step<"cloudflare" | "account">(async ({ run, say }, got) => {
  const owner = run.owner!;
  readableAt(run.at, run.io.now());
  say(`copy back into ${FILES_BUCKET} the objects of ${owner}'s namespace that it lacks, as the ` +
      `off-account copy ${run.at === null ? "stands" : `stood at ${run.at.toISOString()}`}, ` +
      `never one it holds, after writing the restore record ${RESTORE_KEY}: the namespace ` +
      "the owner's own object names (restore.source through the operator channel, which the " +
      "doors refuse for an erased or erasing owner), each object written read back byte for " +
      "byte");
  if (run.reach === "public") {
    return;
  }
  const { namespace } = await asked(run, got, "restore.source", owner, {}) as RestoreSource;
  if (namespace === null) {
    throw new Error(`${owner}'s object names no namespace, so nothing says which of the copy's ` +
                    "is its own (a lost object, or any after an account loss: checklist.md, " +
                    "\"A restore from the copy\"); nothing is read or written");
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
                        { cloudflare: got.cloudflare, account: got.account, namespace: control },
                        run.io));
});

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
  say(`erase ${owner} through the operator channel, as its own door erases it: ${ERASE_LOSES}`);
  if (!run.apply) {
    return;
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
    say(`E2B lifecycle webhook ${E2B_HOOK.url}${where}: ${result}`);
  });
}

/** The plan job's word on E2B's lifecycle webhook, which it cannot reach. */
const hookLater = step(({ say }) => {
  say(`E2B lifecycle webhook ${E2B_HOOK.url}: ${LATER}`);
});

/** The doors' routes handed over one at a time, no path ever served by nothing. */
const handOver = step<"cloudflare" | "zone" | "changes">(async ({ run, say }, got) => {
  say(...got.changes.map((each) => describeStep(each, DOORS)));
  if (run.apply) {
    await got.cloudflare.reroute(got.zone, got.changes, (each) => describeStep(each, DOORS));
  }
});

/** The sandbox's egress host, the doors' custom domain (`wrangler triggers deploy`). */
const customDomain = step<"cloudflare" | "zone" | "account" | "wrangler" | "stage" | "kept">(
  async ({ run, say }, got) => {
    say(`add the custom domain ${EGRESS_DOMAIN} for ${DOORS} (wrangler triggers deploy)`);
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
      const created = await got.cloudflare.dnsRecords(got.zone, EGRESS_DOMAIN);
      const status = await got.cloudflare.put(got.account, namespace, key,
                                              { ...record, domain: { ...record.domain, created } });
      if (status !== 200) {
        throw new Error(`writing the custom domain's DNS records into ${key} failed (${
          status}): run routes again, which reads and writes them once more`);
      }
      say(`record in ${key} the DNS records the custom domain made: ${
        created.length === 0 ? "none" : created.map((each) => each.type).join(", ")}`);
    }
  });

/**
 * Fact 11: after a deploy or a route change, the platform's canary (`canary.ts`), reading
 * its own keys. With no side it holds whichever serves `/mcp`: before `routes` the old stack,
 * where it checks that the old stack still answers and the doors' checks wait. `routes` asks
 * for `--side doors --unkeyed`: the doors must answer `/mcp` and its resource document, and
 * the old stack must not, while the keyed checks wait, since no canary owner's key can be
 * minted before the doors serve `/app/api`, and the dark control record admits no one.
 */
function canary(...args: [] | ["--side", "doors", "--unkeyed"]): Step<never, never> {
  return step(({ run }) => {
    const ran = run.io.spawn(process.execPath, ["--experimental-strip-types",
                                                join(PLATFORM, "deploy", "canary.ts"), ...args],
                             { stdio: "inherit", cwd: REPO });
    if (ran.status !== 0) {
      throw new Error(`the platform canary${args.length === 0 ? "" : ` (${args.join(" ")})`} ` +
                      `failed (exit ${ran.status})`);
    }
  });
}

/** The doors are deployed: a dark run came first. */
const doorsDeployed = step<"cloudflare" | "account">(async ({ say }, got) => {
  if (!(await got.cloudflare.workers(got.account)).includes(DOORS)) {
    throw new Error(`${DOORS} is not deployed: the probe runs after a dark run`);
  }
  say(`${DOORS} is deployed`);
});

/** The control record admits nobody, as a dark run writes it. */
const gatesClosed = step<"cloudflare" | "account">(async ({ say }, got) => {
  const namespace = await controlNamespace(got.cloudflare, got.account);
  const control = namespace === undefined ? null
    : parseControl(await got.cloudflare.value(got.account, namespace, CONTROL_KEY));
  if (control === null || !closedToAll(control)) {
    throw new Error("the control record is missing or admits someone: the probe runs only " +
                    "with every gate closed, as a dark run writes it");
  }
  say("the control record admits no owner and no outside client; no gate is changed");
});

/** WorkOS lists the callback, as a dark run registers it. */
const callbackListed = step(async ({ run, say }) => {
  if (await registerRedirect(input(run, "WORKOS_API_KEY")!, false, run.io.fetch) !== "present") {
    throw new Error(`WorkOS lists no ${UPSTREAM_CALLBACK}: run dark first, which registers it`);
  }
  say(`WorkOS lists ${UPSTREAM_CALLBACK}, as a dark run registers it`);
});

const callbackLater = step(({ say }) => {
  say(`WorkOS lists ${UPSTREAM_CALLBACK}, as a dark run registers it: ${LATER}`);
});

/** The probe's handover, from the zone's routes as they stand. */
const probePlan = step<"cloudflare" | "zone", "changes">(async (_ctx, got) => ({
  changes: probeSteps(await got.cloudflare.routes(got.zone)) }));

/**
 * The early sign-in check, before a route moves, on the run that moves them: WorkOS takes the
 * client and UPSTREAM_CALLBACK, which it lists, so a redirect-uri-invalid answer stops the probe.
 */
const probeAuthorize = step(async ({ run, say }) => {
  if (run.apply) {
    say(await authorizeCheck(await upstreamFrom(input(run, "OWNER_ISSUER"),
                                                input(run, "WORKOS_CLIENT_ID"), run.io.fetch),
                             false, run.io.fetch));
  }
});

/** The sign-in's two paths handed to the doors, or found theirs already. */
const probeHandOver = step<"cloudflare" | "zone" | "changes">(async ({ run, say }, got) => {
  say(...(got.changes.length === 0 ? [`${DOORS} holds ${PROBE_ROUTES.join(" and ")} already`]
    : got.changes.map((each) => describeStep(each, DOORS))));
  if (run.apply) {
    await got.cloudflare.reroute(got.zone, got.changes, (each) => describeStep(each, DOORS));
  }
});

const signInLink = step(({ run }) => {
  run.io.log(`\nSign in once, in a browser, at:\n${probeLink()}\n\n${PROBE_SIGNS}`);
});

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

/** The route record `--record` names, read and held to its shape; missing or bad stops it. */
const recordRead = step<"cloudflare" | "zone" | "account", "record">(async ({ run, say }, got) => {
  const key = `${RECORD_PREFIX}${run.record}`;
  const namespace = await controlNamespace(got.cloudflare, got.account);
  const value = namespace === undefined ? undefined
    : await got.cloudflare.value(got.account, namespace, key);
  if (value === undefined) {
    throw new Error(`there is no route record ${key} in ${NAMESPACES["CONTROL"]}: name the ` +
                    "id a routes run's job summary gives; nothing is changed");
  }
  const record = recordOf(value, key, got.zone);
  say(`the route record ${key}: the zone's ${record.routes.length} routes as run ${record.run} ` +
      `read them at ${record.at}, and the handover's ${record.moved.length} changes`);
  return { record };
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
  say(before.enabled ? `E2B lifecycle webhook ${url}: enabled before routes, left`
    : disable === null ? `E2B lifecycle webhook ${url}: not enabled already`
    : `disable E2B lifecycle webhook ${url}, as it was ${before.present ? "before routes"
      : "absent before routes (a disabled hook sends nothing)"}`);
  if (run.apply && disable !== null) {
    await got.e2b.changeWebhook(disable, { enabled: false });
  }
});

const hookBackLater = step<"record">(({ say }, got) => {
  say(`E2B lifecycle webhook ${got.record.hook.url}: ${LATER}`);
});

/**
 * The handback's other changes: the doors' custom domain detached, then the routes one at a
 * time, no path the record served ever served by nothing. Last, the DNS record the attach
 * made, if the detach left it: it serves no one, so a refused delete (the token's DNS Edit)
 * never holds the routes back.
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
  if (run.apply) {
    for (const domain of got.detach) {
      await got.cloudflare.detach(got.account, domain.id, record.domain.hostname);
    }
    await got.cloudflare.reroute(got.zone, got.back, describeBack);
    // The address records the attach made, only those and only as recorded, if the detach
    // left them.
    for (const left of await createdLeft(got)) {
      await got.cloudflare.deleteDns(got.zone, left, record.domain.hostname);
    }
  }
});

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

/** A dark run: the checks and WorkOS's, then storage, secrets, Workers and the control record. */
const DARK_LATER = READS_LATER.then(redirectLater).then(authorize).then(namespaces).then(buckets)
  .then(bucketLocks).then(queues).then(staged).then(secretsLater).then(workers).then(retired)
  .then(control).then(hookLater);
const DARK = READS.then(redirect).then(authorize).then(namespaces).then(buckets)
  .then(bucketLocks).then(queues).then(staged).then(secrets).then(workers).then(retired)
  .then(control).then(hook(false, ""));

/**
 * A routes run: the route plan read and its record kept before the first change, WorkOS's
 * redirect too, then a dark run's, then the handover, the custom domain and the hook enabled.
 */
const ROUTES_LATER = READS_LATER.then(routePlan).then(recordedLater).then(redirectLater)
  .then(authorize).then(namespaces).then(buckets).then(bucketLocks).then(queues).then(staged)
  .then(secretsLater).then(workers).then(retired).then(control).then(hookLater).then(handOver)
  .then(customDomain);
const ROUTED = READS.then(routePlan).then(recorded).then(redirect).then(authorize)
  .then(namespaces).then(buckets).then(bucketLocks).then(queues).then(staged).then(secrets)
  .then(workers).then(retired).then(control).then(handOver).then(customDomain)
  .then(hook(true, ", after the routes"));

/** A probe run: a dark run came first, every gate is closed, then the sign-in's two paths. */
const PROBE_LATER = Steps.none.then(zone).then(doorsDeployed).then(gatesClosed)
  .then(callbackLater).then(probePlan).then(probeHandOver);
const PROBE = Steps.none.then(zone).then(doorsDeployed).then(gatesClosed).then(callbackListed)
  .then(probePlan).then(probeAuthorize).then(probeHandOver);

/** A handback: a routes run's record read, held to the zone, handed back and read back. */
const HANDBACK_LATER = Steps.none.then(zone).then(recordRead).then(backPlan).then(hookBackLater)
  .then(handBack);
const HANDBACK = Steps.none.then(zone).then(recordRead).then(e2b).then(hookListed).then(backPlan)
  .then(hookBack).then(handBack).then(handedBack);

/** The operator path's modes: the same steps for the plan job, which applies nothing. */
const CONTROL = Steps.none.then(zone).then(controlEdit).list;
const OWNER = Steps.none.then(zone).then(ownerAsk).list;
const RESTORE = Steps.none.then(zone).then(restoreAsk).list;
const COPYBACK = Steps.none.then(zone).then(copyBackAsk).list;
const ERASE = Steps.none.then(zone).then(eraseAsk).list;
const ROTATION = Steps.none.then(zone).then(rotationSweep).list;
const RETIRE = Steps.none.then(zone).then(retireAsk).list;

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
    report: { unchanged: NOTHING, after: [canary()] },
  },
  probe: {
    inputs: { public: PUBLIC_INPUTS, secret: ["WORKOS_API_KEY"], optional: [] },
    deploys: false, applies: true, own: [],
    steps: { public: PROBE_LATER.list, secrets: PROBE.list },
    report: { unchanged: "\nNothing was changed. The apply job prints the sign-in link.",
              after: [canary(), signInLink] },
  },
  routes: {
    inputs: DEPLOY_INPUTS, deploys: true, applies: true, own: [],
    steps: { public: ROUTES_LATER.list, secrets: ROUTED.list },
    report: { unchanged: NOTHING, after: [canary("--side", "doors", "--unkeyed")] },
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
    steps: { public: RETIRE, secrets: RETIRE }, report: { unchanged: NOTHING, after: [canary()] },
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
 * each difference named.
 */
async function approved(row: Row, run: Run, ctx: Context): Promise<void> {
  const planJob: Run = Object.freeze({ ...run, reach: "public", apply: false });
  const live: Plan = { asked: askedOf(run), lines: await stepsOf(row, planJob, ctx.scratch) };
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
  ctx.say("the plan job's plan, made again from the live state, is the one approved");
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
  "--record": { names: "the routes run's record to hand back, by the run id its job summary " +
                       "names (letters, digits and -)", needed: true },
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

/** The lines `row`'s steps for `run` report, run in order, each given what the ones before made. */
async function stepsOf(row: Row, run: Run, scratch: () => string): Promise<string[]> {
  const lines: string[] = [];
  const ctx: Context = { ...reporterOf(run, lines), run, scratch };
  const values: Partial<Values> = {};
  for (const each of row.steps[run.reach]) {
    Object.assign(values, await each.run(ctx, values as Pick<Values, never>));
  }
  return lines;
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
    if (run.apply) {
      await approved(row, run, ctx);
    }
    plan.push(...await stepsOf(row, run, ctx.scratch));
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
      for (const each of run.apply ? row.report.after : []) {
        await each.run(ctx, {});
      }
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
