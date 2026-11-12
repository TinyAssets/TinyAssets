/**
 * The template's standing checks, a bundle of their own beside the runner (`probes.mjs`,
 * `template/bundle.mjs`): nothing in the runner imports them, so the runner every sandbox
 * runs carries none of them, and this bundle carries what they judge by (the owner's run
 * environment, `runEnv` and `sandboxEnv`, the egress door's paths, `STEP_NS`). The pair the
 * deploy looks for names both bundles' digests (`template/pair.ts`).
 *
 * The bundle is built with the digest of the runner built beside it (`TA_RUNNER_SHA`), and
 * runs through the template's loader as a runner does (`ta-runner install`, then
 * `ta-runner <sha> <command>`), only once that runner is installed and has prepared the
 * sandbox (`template/checks.ts`): every command but `plan` refuses unless that runner is in
 * place. It never runs `prepare` or `adopt`, which would drop the runner's bytes.
 *
 * Its commands are its own, not the Worker's protocol with the runner (`protocol.ts`):
 * - `plan`: what to check, as JSON (`Plan`), run on the host that runs the checks: the
 *   sandboxes and their lifetimes and networks, and every step's command and bound;
 * - `login-probe <engine> [region]`: a record's sign-in up to its prompt, its shape judged;
 * - `route-probe <engine> <provider> <resolver>...`: a keyed engine's two turns on the door;
 * - `matcher-probe`: the sync-ignore automaton's step, judged against `STEP_NS`.
 * Each prints its result as one JSON line and exits 0 when it passed, 3 when it did not.
 */
import { sha256Hex } from "../../contract/src/digest";
import { randomBytes } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { chownSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer as createHttpServer, type IncomingMessage } from "node:http";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";

import type { EgressScope } from "../../contract/src/egress-scope";
import { ALL_TRAFFIC, closedNetwork, DNS_SERVERS, EGRESS_ORIGIN, egressBaseUrl,
         sandboxEnv } from "../../contract/src/egress";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { AGENTS_DIR } from "../../contract/src/paths";
import { FRAME_UNITS_MAX, readEvent } from "../../contract/src/run-events";
import { STEP_NS, stepNanos } from "../../contract/src/sync-ignore";
import { bundledRecords, engineHosts } from "../../sources/src/catalog";
import { runEnv } from "../../sources/src/run-env";
import type { SourceRecord } from "../../sources/src/schema";
import { SIGNIN_REFUSALS, signinShape } from "../../sources/src/signin";

import { binPath, invocation, isKept, keptPaths, SYSTEM } from "./engine";
import { lineEvent } from "./frames";
import { remove, safeDirs, safeWrite } from "./fsafe";
import { loginArgv, onTerminal, plain, type Prompt, prompted } from "./login";
import { COMMAND_MS, EXIT, RUNNERS, type StartDoc, STATE_DIR } from "./protocol";
import { type Captured, outsideConnects, reply, routeVerdict } from "./route";
import { CONVERSATIONS, configOwned, dropHome, engineEnv, giveTree, keepHome,
         makeHome, record, RECORDS, shared, sortedWalk, sources, STORE } from "./session";
import { asUser, give, killAll, take } from "./users";

/** The digest of the runner built beside this bundle (`bundle.mjs` defines it). */
declare const TA_RUNNER_SHA: string;
const RUNNER: string | null = typeof TA_RUNNER_SHA === "string" ? TA_RUNNER_SHA : null;

const STATE = STATE_DIR;
const OK = EXIT.ok;
const NO = EXIT.no;

/** The probes' own commands. */
const PROBE = Object.freeze({
  plan: "plan",
  login: "login-probe",
  route: "route-probe",
  matcher: "matcher-probe",
} as const);

/** E2B's network document, as a check sandbox is given it (`closedNetwork`'s shape). */
interface PlanNetwork {
  readonly allowOut: readonly string[];
  readonly denyOut: readonly string[];
  readonly dnsServers: readonly string[];
  readonly rules: Readonly<Record<string, readonly unknown[]>>;
}

/** One command in a check sandbox: its argv, its bound, and the network it runs under. */
export interface PlanStep {
  /** Which check it is: its result goes to the record's `logins`, `routes` or `matcher`. */
  readonly check: "login" | "route" | "matcher";
  /** This bundle's command and its arguments (`ta-runner <probes> ...argv`). */
  readonly argv: readonly string[];
  readonly timeout_s: number;
  /** The sandbox's network replaced with this before the step, or kept (null). */
  readonly network: PlanNetwork | null;
}

/** One throwaway sandbox of the template, and what runs in it, in order. */
export interface PlanSandbox {
  readonly name: string;
  /** Its lifetime: E2B kills it then, whatever the checks are doing. */
  readonly timeout_s: number;
  /** Its network at creation, or E2B's default, open (null). */
  readonly network: PlanNetwork | null;
  /** Commands as root after the runner's install and before its `prepare`, each to exit 0. */
  readonly setup: readonly { readonly argv: readonly string[]; readonly timeout_s: number }[];
  /** Its network replaced with this once the runner prepared it, or kept (null). */
  readonly prepared: PlanNetwork | null;
  readonly steps: readonly PlanStep[];
}

/** What the template's checks run (`plan`): every sandbox, and the runner each prepares. */
export interface Plan {
  /** The runner built beside this bundle, which each sandbox installs and prepares first. */
  readonly runner: string;
  /** `prepare`'s resolvers. */
  readonly resolvers: readonly string[];
  /** The runner's install and `prepare`, each's bound. */
  readonly install_s: number;
  readonly prepare_s: number;
  readonly sandboxes: readonly PlanSandbox[];
}

/** `strace` installed on the route probe's sandbox while its network is open, or nothing. */
const STRACE = "(command -v strace || (apt-get -qq update && apt-get -qq install -y strace)) " +
  ">/dev/null 2>&1; command -v strace >/dev/null";

/**
 * The checks: each record's sign-in (every region of a record with regions) under the
 * production egress allowlist widened by that engine's own hosts, as the sandbox Worker
 * widens a sign-in's network (review L3); every keyed engine on every provider it keys, on a
 * sandbox whose network denies everything once prepared; and the matcher's step.
 */
export function plan(runner: string): Plan {
  const logins: PlanStep[] = [];
  const routes: PlanStep[] = [];
  for (const rec of [...RECORDS.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const cli = rec.cli!;
    if (rec.unwired !== undefined) {
      continue;
    }
    if (cli.login !== undefined) {
      const regions = Object.keys(cli.regions ?? {}).sort();
      for (const region of regions.length > 0 ? regions : [null]) {
        logins.push({ check: "login", timeout_s: 180,
                      argv: [PROBE.login, rec.id, ...(region === null ? [] : [region])],
                      network: closedNetwork(engineHosts(rec, region)) });
      }
    }
    if (cli.credential.includes("api_key")) {
      for (const provider of Object.keys(cli.api_key_env ?? {}).sort()) {
        routes.push({ check: "route", timeout_s: 300, network: null,
                      argv: [PROBE.route, rec.id, provider, ...DNS_SERVERS] });
      }
    }
  }
  return {
    runner, resolvers: [...DNS_SERVERS], install_s: 120, prepare_s: 300,
    sandboxes: [
      { name: "logins", timeout_s: 900, network: closedNetwork([]), setup: [], prepared: null,
        steps: logins },
      { name: "routes", timeout_s: 1800, network: null,
        setup: [{ argv: ["/bin/sh", "-c", STRACE], timeout_s: 600 }],
        prepared: { allowOut: [], denyOut: [ALL_TRAFFIC], dnsServers: [...DNS_SERVERS],
                    rules: {} },
        steps: routes },
      { name: "matcher", timeout_s: 300, network: null, setup: [], prepared: null,
        steps: [{ check: "matcher", argv: [PROBE.matcher], timeout_s: 120, network: null }] },
    ],
  };
}

/**
 * The standing check of a record's sign-in (at every pair check, so at every pinned CLI's
 * version bump): the record's login run, as a sign-in runs it, up to
 * the prompt its patterns find, then stopped. Its answer: whether the prompt is the record's
 * own shape (`signinShape`), the URL's host, whether a code was shown, and whether the URL
 * would send the owner's browser back to `localhost` (which strands it). No account needed.
 */
async function loginProbe(engine: string, region: string | null): Promise<number> {
  const rec = record(engine);
  const login = rec.cli!.login;
  if (login === undefined) {
    throw new Error(`engine ${engine} has no vendor sign-in`);
  }
  const name = `probe-${rec.id}-${process.pid}`;
  const as = take(STATE);
  const home = await makeHome(name, rec);
  let shown: Prompt = { url: null, code: null };
  try {
    const files = rec.cli!.per_credential?.vendor_login_in_sandbox?.files ?? {};
    for (const [path, text] of Object.entries(files)) {
      safeWrite(home, path.replaceAll("{home}", home), new TextEncoder().encode(text), 0o600);
    }
    giveTree(home, as);
    const argv = asUser(as, [shared()], onTerminal(loginArgv(login, binPath(rec), region)));
    await new Promise<void>((resolve) => {
      const child = spawn(argv[0]!, argv.slice(1), { env: engineEnv(rec, home), cwd: home,
                                                     detached: true,
                                                     stdio: ["pipe", "pipe", "pipe"] });
      let text = "";
      // Its group killed; the answer waits for its close, so no exited child is left unreaped
      // for the user's processes' kill to wait on.
      const done = () => {
        try {
          process.kill(-child.pid!, "SIGKILL");
        } catch {
          // gone already
        }
      };
      // As long as a command may take: a CLI that shows no prompt by then shows none.
      const timer = setTimeout(done, COMMAND_MS);
      const read = (data: Buffer) => {
        text = (text + plain(data.toString("utf8"))).slice(-FRAME_UNITS_MAX);
        shown = prompted(login, text, shown);
        const wantsCode = login.code_pattern !== undefined;
        if (shown.url !== null && (!wantsCode || shown.code !== null)) {
          clearTimeout(timer);
          done();
        }
      };
      child.stdout!.on("data", read);
      child.stderr!.on("data", read);
      child.on("close", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  } finally {
    killAll(as);
    dropHome(home);
    give(STATE, as);
  }
  const redirect = shown.url === null ? null : new URL(shown.url).searchParams.get("redirect_uri");
  const local = redirect !== null && /^https?:\/\/(?:localhost|127\.|\[::1\])/.test(redirect);
  const refused = shown.url === null ? null : signinShape(rec, region, shown.url, shown.code);
  const why = shown.url === null ? "no sign-in URL shown"
    : local ? "the URL sends the browser back to localhost"
      : refused === null ? null : SIGNIN_REFUSALS[refused];
  process.stdout.write(JSON.stringify({
    engine, region, ok: why === null, reason: why,
    host: shown.url === null ? null : new URL(shown.url).hostname, code_shown: shown.code !== null }));
  return why === null ? OK : NO;
}

// -- the route probe (codexurl_research.md Q4) ------------------------------------------

/** How long one probe turn may take: a command's bound. */
const ROUTE_PROBE_MS = COMMAND_MS;

const hex = (bytes: number) => randomBytes(bytes).toString("hex");

/** The sealed API a provider's key goes to, as the gateway's record for it says. */
function sealedScope(provider: string, engine: string): EgressScope {
  const found = (bundledRecords() as Record<string, unknown>[]).filter((raw) =>
    raw["provider"] === provider && raw["unwired"] === undefined
    && typeof raw["base_url"] === "string" && Array.isArray(raw["connect"])
    && (raw["connect"] as string[]).includes("api_key")
    && Array.isArray(raw["engines"]) && (raw["engines"] as string[]).includes(engine))
    .sort((a, b) => String(a["id"]).localeCompare(String(b["id"])))[0];
  if (found === undefined) {
    throw new Error(`no gateway record seals ${provider}'s key for ${engine}`);
  }
  return { api: String(found["api"]), base_url: String(found["base_url"]), endpoints: [],
           meter: null };
}

/** One probe turn's outcome: its exit, the session it named, whether it gave its final text. */
interface ProbeTurn {
  /** Its stderr's tail, named when it failed. */
  readonly stderr: string;
  readonly exit: number;
  readonly session_id: string | null;
  readonly answered: boolean;
  readonly resumed: boolean;
  readonly connects: string[];
}

/**
 * One keyed engine on one provider, run as a run runs it (the real `invocation`, `runEnv`
 * and `sandboxEnv`, `makeHome` for an API-key run, its own pool user), its egress door a
 * loopback capture server at the door's real path answering each model request with a
 * minimal valid reply in its sealed API's wire format, under `strace` for its connects, with
 * a fake sign-in seeded in every one of its store's `secret_paths`. Two turns: a new
 * conversation, then its resume (its home made again from the store, as the next run's is),
 * that one compacting first where the record says how (`cli.compaction`). Each turn must exit
 * 0 with its final text, the first name its session where the record resumes by one, the
 * second resume it, a compaction be seen; and every request of both is judged
 * (`routeVerdict`).
 */
async function probeRoute(rec: SourceRecord, provider: string,
                          resolvers: readonly string[]): Promise<Record<string, unknown>> {
  const cli = rec.cli!;
  const egress = sealedScope(provider, rec.id);
  const requests: Captured[] = [];
  const server = createHttpServer((request: IncomingMessage, response) => {
    const chunks: Buffer[] = [];
    let size = 0;
    request.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size <= EGRESS_BODY_MAX) {
        chunks.push(chunk);
      }
    });
    request.on("end", () => {
      const captured: Captured = {
        method: request.method ?? "", url: request.url ?? "",
        headers: Object.fromEntries(Object.entries(request.headers).map(([name, value]) =>
          [name, Array.isArray(value) ? value.join(", ") : value ?? ""])),
        body: Buffer.concat(chunks).toString("utf8") };
      requests.push(captured);
      // The MCP door answers none (404: no server, so no client starts an OAuth discovery
      // of it); a model request gets its API's minimal valid reply.
      const answer = captured.url.split("?")[0]!.endsWith("/mcp")
        ? { status: 404, type: "application/json", body: "{}" } : reply(egress.api, captured);
      response.writeHead(answer.status, { "content-type": answer.type });
      response.end(answer.body);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  const origin = `http://127.0.0.1:${port}`;
  const local = (value: string) => value.replaceAll(EGRESS_ORIGIN, origin);
  const run = `run_${hex(16)}`;
  const secret = hex(32);
  const connection = `c${hex(12)}`;
  const marker = `ta_fake_signin_${hex(16)}`;
  // The first turn's own word, which a resumed turn's requests carry only if it loaded the
  // first turn's history (a CLI that silently starts afresh fails).
  const nonce = `probe${hex(8)}`;
  const engine = runEnv(sources, rec.id, "api_key");
  const keyed = sandboxEnv([{ connection, provider, egress }], () => hex(16),
                           { ...engine, run, secret, connection });
  const placeholder = Object.values(keyed).find((value) => value.startsWith("ta_ph_"))!;
  const names = cli.api_key_env![provider]!;
  const inPath = engine.secret_header === undefined ? secret : undefined;
  const prefix = new URL(egressBaseUrl(connection, names.base_url !== null
    && names.base === "connection" ? null : egress, run, inPath)).pathname;
  const env = Object.fromEntries(Object.entries({ ...engine.env, ...keyed }).map(
    ([name, value]) => [name, local(value)]));
  const first: StartDoc = {
    run, agent: "route-probe", conversation: `cv_${hex(16)}`, engine: rec.id,
    credential: "api_key", model: null, session_id: null,
    prompt: `Remember the word ${nonce}. Reply with the word OK.`, unattended: false,
    authority: "owner",
    lease: { lease_id: hex(16), epoch: 1 },
    config: { generation: 1, egress: { mode: "open", destinations: [] }, env,
              inherit: engine.inherit },
    urls: { mcp: `${origin}/r/${run}/${secret}/mcp`, mcp_bare: `${origin}/r/${run}/mcp`,
            events: `${origin}/r/${run}/events`, signin: `${origin}/signin` } };
  // A fake sign-in in each of the store's secret paths: if any of it reaches a request, a
  // key run's home carried the sign-in.
  const store = `${STORE}/${rec.id}`;
  // What the store held before the probe: only what the probe adds (its fake sign-in, its
  // turns' sessions) goes at its end.
  const had = new Set(existsSync(store) ? sortedWalk(store) : []);
  const seeded: string[] = [];
  for (const rel of keptPaths(rec).secrets) {
    const concrete = rel.split("/").map((part) => part === "*" ? "probe" : part).join("/");
    const path = join(store, concrete.endsWith("/") ? `${concrete}probe.json` : concrete);
    if (!existsSync(path)) {
      safeDirs("/", dirname(path), 0o700);
      safeWrite(store, path, new TextEncoder().encode(JSON.stringify({
        access_token: marker, refresh_token: marker, id_token: marker,
        tokens: { access_token: marker, refresh_token: marker },
        claudeAiOauth: { accessToken: marker, refreshToken: marker } })), 0o600);
      seeded.push(path);
    }
  }
  const name = `route-${rec.id}-${process.pid}`;
  const system = `${SYSTEM}/${name}`;
  const agentDir = `${AGENTS_DIR}/${first.agent}`;
  const conversationDir = cli.engine.resume === "continue_in_cwd"
    ? `${CONVERSATIONS}/${rec.id}/${first.conversation}` : undefined;
  const turns: ProbeTurn[] = [];

  /** One turn, as one run runs: its own user and home, then its home kept and dropped. */
  const turn = async (start: StartDoc, resumed: boolean,
                      extra: readonly string[]): Promise<ProbeTurn> => {
    const as = take(STATE);
    const trace = `${STATE}/route-${name}.trace`;
    let home: string | null = null;
    let exit = 1;
    let session: string | null = null;
    let answered = false;
    let text = "";
    let stderr = "";
    try {
      home = await makeHome(name, rec, false);
      const carried = [...sortedWalk(home)].filter((rel) => isKept(keptPaths(rec).secrets, rel));
      if (carried.length > 0) {
        throw new Error(`an API-key run's home carries the sign-in's ${carried.join(", ")}`);
      }
      remove(system);
      mkdirSync(system, { mode: 0o750 });
      chownSync(system, 0, as.gid);
      giveTree(agentDir, as);
      if (conversationDir !== undefined) {
        safeDirs("/", conversationDir, 0o700);
        giveTree(conversationDir, as);
      }
      const call = invocation(rec, start, process.env, {
        continued: resumed, home, system,
        ...(conversationDir === undefined ? {} : { conversationDir }) });
      if (call.resumed !== resumed) {
        throw new Error(resumed ? "the second turn does not resume the first"
          : "the first turn resumes nothing");
      }
      for (const [path, text] of call.files) {
        const inSystem = path.startsWith(`${system}/`);
        safeWrite(inSystem ? system : home, path, new TextEncoder().encode(text),
                  inSystem ? 0o640 : 0o644, undefined, inSystem ? 0o750 : 0o755);
      }
      giveTree(home, as);
      configOwned(rec, home, system, call.files.keys(), as);
      const argv = [call.argv[0]!, ...extra, ...call.argv.slice(1)];
      const runHome = home;
      exit = await new Promise<number>((resolve) => {
        const child = spawn("strace", ["-f", "-qq", "-e", "trace=connect", "-o", trace, "--",
                                       ...asUser(as, [shared()], argv)], {
          cwd: call.cwd, env: { ...call.env, TMPDIR: join(runHome, "tmp") }, detached: true,
          stdio: [call.stdin === null ? "ignore" : "pipe", "pipe", "pipe"] });
        child.stderr!.on("data", (data: Buffer) => {
          stderr = (stderr + data.toString("utf8")).slice(-600);
        });
        if (call.stdin !== null) {
          child.stdin!.on("error", () => undefined);
          child.stdin!.end(call.stdin);
        }
        createInterface({ input: child.stdout! }).on("line", (line) => {
          if (line.trim() === "") {
            return;
          }
          const read = readEvent(cli.engine.events, lineEvent(line));
          session = read.session_id ?? session;
          if (read.text !== null) {
            // Its final text, as the run reads it: deltas accumulated where the record says.
            text = read.text.accumulate ? text + read.text.value : read.text.value;
            answered ||= text.includes("OK");
          }
        });
        const timer = setTimeout(() => {
          try {
            process.kill(-child.pid!, "SIGKILL");
          } catch {
            // gone already
          }
        }, ROUTE_PROBE_MS);
        child.on("close", (code) => {
          clearTimeout(timer);
          resolve(code ?? 1);
        });
        child.on("error", () => {
          clearTimeout(timer);
          resolve(127);
        });
      });
      killAll(as);
      await keepHome(home, rec);
      return { stderr, exit, session_id: session, answered, resumed: call.resumed,
               connects: outsideConnects(existsSync(trace) ? readFileSync(trace, "utf8") : "",
                                         resolvers) };
    } finally {
      killAll(as);
      if (home !== null) {
        dropHome(home);
      }
      remove(trace);
      give(STATE, as);
    }
  };

  let failed: string | null = null;
  let compacted: boolean | null = null;
  let history: boolean | null = null;
  try {
    if (spawnSync("strace", ["-V"], { encoding: "utf8" }).status !== 0) {
      throw new Error("strace is not installed in the probe's sandbox");
    }
    safeDirs("/", SYSTEM, 0o755);
    remove(agentDir);
    safeDirs("/", agentDir, 0o755);
    writeFileSync(`${agentDir}/agent.json`, JSON.stringify({ engine: rec.id, scopes: [] }));
    writeFileSync(`${agentDir}/AGENTS.md`, "# Route probe\n\nReply with the word OK.\n");
    const one = await turn(first, false, []);
    turns.push(one);
    if (one.exit !== 0 || !one.answered) {
      throw new Error(`the first turn did not complete (exit ${one.exit}${one.answered ? ""
        : ", no final text"}): ${one.stderr.trim().slice(-300)}`);
    }
    if (cli.engine.resume === "session_id" && one.session_id === null) {
      throw new Error("the first turn named no session to resume");
    }
    const before = requests.length;
    const two = await turn({ ...first, session_id: one.session_id,
                             prompt: "Reply with the word OK once more." }, true,
                           cli.compaction?.argv ?? []);
    turns.push(two);
    if (two.exit !== 0 || !two.answered) {
      throw new Error(`the resumed turn did not complete (exit ${two.exit}${two.answered ? ""
        : ", no final text"}): ${two.stderr.trim().slice(-300)}`);
    }
    history = requests.slice(before).some((request) => request.body.includes(nonce));
    if (!history) {
      throw new Error("the resumed turn's requests carry none of the first turn's history");
    }
    if (cli.compaction !== undefined) {
      compacted = requests.slice(before).some((request) =>
        request.body.includes(cli.compaction!.marker));
      if (!compacted) {
        throw new Error("the resumed turn made no compaction request");
      }
    }
  } catch (error) {
    failed = String(error instanceof Error ? error.message : error);
  } finally {
    remove(system);
    remove(agentDir);
    if (conversationDir !== undefined) {
      remove(conversationDir);
    }
    for (const rel of existsSync(store) ? sortedWalk(store) : []) {
      if (!had.has(rel)) {
        remove(join(store, rel));  // the probe's own sessions and its fake sign-in
      }
    }
    for (const path of seeded) {
      remove(path);
    }
    server.close();
  }
  // What the CLI did is judged whatever happened: a leak or an outside connect is named
  // before the turn's own failure.
  const reason = routeVerdict(requests, turns.flatMap((each) => each.connects), {
    prefix, placeholder, secret, marker, resolvers,
    mcp: [`/r/${run}/${secret}/mcp`, `/r/${run}/mcp`],
    secretInHeader: engine.secret_header !== undefined }) ?? failed;
  return { engine: rec.id, provider, ok: reason === null, reason,
           turns: turns.map((each) => ({ exit: each.exit, answered: each.answered,
                                         resumed: each.resumed })),
           compacted, history,
           requests: requests.map((request) => `${request.method} ${request.url.split("?")[0]}`),
           connects: [...new Set(turns.flatMap((each) => each.connects))] };
}


/**
 * The route probe of one keyed engine on one provider (`route-probe <engine> <provider>
 * <resolver>...`, on a sandbox whose network denies all), run as a run runs it
 * (`probeRoute`): its one JSON line.
 */
async function routeProbe(engine: string, provider: string,
                          resolvers: readonly string[]): Promise<number> {
  const rec = record(engine);
  if (rec.unwired !== undefined || !rec.cli!.credential.includes("api_key")
      || rec.cli!.api_key_env?.[provider] === undefined) {
    throw new Error(`engine ${engine} runs on no API key of provider ${provider}`);
  }
  const result = await probeRoute(rec, provider, resolvers);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  return result["ok"] === true ? OK : NO;
}

/** The sync-ignore automaton's step, timed here, within `STEP_NS` or failed. */
function matcherProbe(): number {
  const step = stepNanos();
  const ok = step <= STEP_NS;
  process.stdout.write(`${JSON.stringify({ step_ns: step, ceiling_ns: STEP_NS, ok })}\n`);
  return ok ? OK : NO;
}

/**
 * Why this sandbox cannot be checked by this bundle, or null: the runner it was built beside
 * must be installed here, byte for byte, as the loader installed it.
 */
function unpaired(): string | null {
  if (RUNNER === null) {
    return "the probes were built without their runner's digest (template/bundle.mjs)";
  }
  const file = `${RUNNERS}/${RUNNER}/runner.mjs`;
  if (!existsSync(file)
      || sha256Hex(readFileSync(file)) !== RUNNER) {
    return `the runner ${RUNNER} the probes were built beside is not installed here`;
  }
  return null;
}

export async function main(argv: readonly string[]): Promise<number> {
  const [command, ...args] = argv;
  if (command === PROBE.plan) {
    if (RUNNER === null) {
      throw new Error("the probes were built without their runner's digest");
    }
    process.stdout.write(`${JSON.stringify(plan(RUNNER))}\n`);
    return OK;
  }
  if (command !== PROBE.login && command !== PROBE.route && command !== PROBE.matcher) {
    process.stderr.write(`probes: no command ${String(command)}\n`);
    return EXIT.usage;
  }
  const refused = unpaired();
  if (refused !== null) {
    throw new Error(refused);
  }
  if (command === PROBE.login) {
    return loginProbe(args[0] ?? "", args[1] ?? null);
  }
  if (command === PROBE.route) {
    return routeProbe(args[0] ?? "", args[1] ?? "", args.slice(2));
  }
  return matcherProbe();
}

main(process.argv.slice(2)).then((code) => process.exit(code), (error: unknown) => {
  process.stderr.write(`probes: ${String(error)}\n`);
  process.exit(1);
});
