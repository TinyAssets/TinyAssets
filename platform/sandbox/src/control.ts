/**
 * One owner's sandboxes (`SandboxControl`, a Durable Object of the sandbox Worker's own, one
 * per owner by name): what serializes the E2B calls on them, so a start, a cancel, a
 * release and a renewal of one sandbox never interleave (a release's pause can never
 * freeze a run a start just launched), and the clock that renews each before its runtime
 * ceiling. It keeps no credential and no owner content: each authority's E2B sandbox id,
 * the lease that last configured it, and which runs it took. The envd token is read from
 * E2B at each use (`info`), never stored.
 *
 * **A start** (`start`, `SandboxService.start` through the entrypoint):
 * 1. the control record's sandbox numbers are read; none, and nothing starts
 *    (`sandbox_unconfigured`);
 * 2. the authority's sandbox is found running, or resumed (`connect`, memory restored), or
 *    created from the versioned template (`E2B_TEMPLATE`) with the lease's network, its
 *    timeout the runtime ceiling and paused on timeout, memory kept. A resume or a create
 *    is one more running sandbox: past `sandbox_concurrency_ceiling` (E2B's own running
 *    count, `X-Total-Running`) or refused by E2B for room, the run waits
 *    (`sandbox_waiting`), never refused. A start that opened the lease (`tokens`) applies
 *    its network whole and hands the runner its secrets (`/var/lib/ta/lease.json`, in the
 *    root-only state directory); a start that joins finds its sandbox configured;
 * 3. a run on the vendor's own sign-in widens the network to its record's login and
 *    provider hosts (that traffic goes to the vendor directly), and runs the record's
 *    `status_argv` there first (`ta-runner signin-check`): no login, and the answer is
 *    `signin_required` (ADR-031 decision 2: a login is never copied in);
 * 4. the run's start document goes into the state directory and `ta-runner launch` starts
 *    the run's runner, detached; the answer comes at once.
 *
 * **Renewal** (the founder's rule, 2026-11-03): a sandbox may run `runtime_ceiling_s` at a
 * stretch (E2B's plan: Hobby 1 hour, Pro 24), and a pause resets that window (probed:
 * E2B's `startedAt` moves to the resume, and a process lives through it). The window is
 * E2B's own `startedAt`, so nothing here assumes its length. From `lead` before the ceiling
 * the alarm asks the runner whether its CLIs are quiet (between turns, as their events
 * show); at a quiet moment, or forced at `edge` before the ceiling, it renews: the runner
 * posts each live run's `renewing` frame and holds its posts, the sandbox is paused with its
 * memory and resumed at once, and the runner reconnects (`renewed` frames; a CLI whose model
 * call failed in the pause is resumed by the runner from its session). A sandbox E2B paused
 * filesystem-only is rebooted by its resume: `ta-runner recover` restarts its runs from their
 * sessions. A sandbox with no live run is paused instead.
 */
import { DurableObject } from "cloudflare:workers";

import { type SandboxCeilings, parseControl } from "../../doors/src/directory";
import { bundledSources } from "../../doors/src/sources";
import { DNS_SERVERS, EGRESS_HOST } from "../../owner/src/network";
import type { SandboxStart, Started } from "../../owner/src/sandbox";

import { E2b, type E2bNetwork, type Handle, noRoom } from "./e2b";

/** The runner's command in the template, and its root-only state directory. */
export const RUNNER = "/usr/local/bin/ta-runner";
export const STATE_DIR = "/var/lib/ta";

/** The start's fixed answers, beside `started: true`. */
export const CODES = Object.freeze({
  waiting: "sandbox_waiting",
  signin: "signin_required",
  unconfigured: "sandbox_unconfigured",
  lost: "sandbox_lost",
} as const);

/** The runner's exit codes: done, "no" (not signed in, not live), and anything else a fault. */
const OK = 0;
const NO = 3;

/** How long one runner command may take before it is a fault (envd's own deadline). */
const COMMAND_MS = 60_000;
/** How many times a pause E2B answered busy is asked again, and how long apart. */
const PAUSE_TRIES = 10;
const PAUSE_BACKOFF_MS = 2_000;

export interface SandboxEnv {
  readonly E2B_API_KEY: string;
  /** The versioned template, `name:tag` (`template/build.py` prints it). */
  readonly E2B_TEMPLATE: string;
  /** The platform's directory, for its control record alone (`c:control`). */
  readonly DIRECTORY: KVNamespace;
}

/**
 * When to look for a quiet moment, and when to renew regardless, before the ceiling: a
 * sixth of it, and a thirtieth (at Hobby's hour, 10 minutes and 2), so the rule holds at
 * any ceiling the record names.
 */
export function renewal(ceilingS: number): { readonly leadS: number; readonly edgeS: number;
                                            readonly stepS: number } {
  const leadS = Math.max(10, Math.floor(ceilingS / 6));
  const edgeS = Math.max(5, Math.floor(ceilingS / 30));
  return { leadS, edgeS, stepS: Math.max(2, Math.floor(leadS / 20)) };
}

/** The control record's sandbox numbers, or null when it holds none or cannot be read. */
export async function ceilings(env: SandboxEnv): Promise<SandboxCeilings | null> {
  let value: unknown;
  try {
    value = await env.DIRECTORY.get("c:control", { type: "json" });
  } catch {
    console.error("the directory's control record is unreadable");
    return null;
  }
  return value === null ? null : parseControl(value)?.sandbox ?? null;
}

interface Row {
  authority: string;
  sandbox_id: string;
  lease_id: string | null;
}

const RUN = /^run_[0-9a-f]{32}$/;
const LEASE = /^[0-9a-f]{32}$/;
const AUTHORITY = /^(?:owner|[0-9a-f]{64})$/;

function json(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

/** E2B's network document from the lease's plan, as the owner's object made it. */
function e2bNetwork(network: unknown): E2bNetwork {
  const n = network as Record<string, unknown> | null;
  if (n === null || typeof n !== "object" || !Array.isArray(n["allowOut"])
      || !Array.isArray(n["denyOut"]) || !Array.isArray(n["dnsServers"])
      || typeof n["rules"] !== "object" || n["rules"] === null) {
    throw new Error("a sandbox network is E2B's whole document");
  }
  return { allowOut: n["allowOut"] as string[], denyOut: n["denyOut"] as string[],
           dnsServers: n["dnsServers"] as string[],
           rules: n["rules"] as Record<string, unknown[]> };
}

/**
 * The network widened to `hosts` (a signed-in engine's vendor hosts): unchanged when it
 * already lets every name out (`*`) or lists them all; the egress door stays listed last.
 */
export function widened(network: E2bNetwork, hosts: readonly string[]): E2bNetwork | null {
  if (network.allowOut.includes("*") || hosts.every((host) => network.allowOut.includes(host))) {
    return null;
  }
  const names = new Set([...network.allowOut, ...hosts]);
  names.delete(EGRESS_HOST);
  return { ...network, allowOut: [...[...names].sort(), EGRESS_HOST] };
}

/** The vendor's own hosts of an engine run on its sign-in; none on a key. */
function vendorHosts(start: SandboxStart): string[] {
  if (start.credential !== "vendor_login_in_sandbox") {
    return [];
  }
  const cli = bundledSources().get(start.engine)?.cli;
  if (cli === undefined) {
    throw new Error(`engine ${start.engine} has no CLI record`);
  }
  return [...new Set([...cli.hosts.login, ...cli.hosts.provider])];
}

/** The start's document as the runner reads it: everything but the lease's own secrets. */
function startDocument(start: SandboxStart): unknown {
  const { tokens: _tokens, network: _network, ...rest } = start;
  return rest;
}

/** A hex SHA-256 of `text`. */
async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export class SandboxControl extends DurableObject<SandboxEnv> {
  readonly #sql: SqlStorage;
  /** The one chain every E2B change of this owner's sandboxes runs on, in order. */
  #chain: Promise<unknown> = Promise.resolve();
  readonly #e2b: () => E2b;

  constructor(ctx: DurableObjectState, env: SandboxEnv) {
    super(ctx, env);
    this.#sql = ctx.storage.sql;
    this.#sql.exec("CREATE TABLE IF NOT EXISTS sandboxes (authority TEXT PRIMARY KEY, " +
                     "sandbox_id TEXT NOT NULL, lease_id TEXT)");
    this.#sql.exec("CREATE TABLE IF NOT EXISTS launched (run TEXT PRIMARY KEY, " +
                     "authority TEXT NOT NULL, lease_id TEXT NOT NULL)");
    this.#e2b = () => new E2b(env.E2B_API_KEY);
  }

  /** `act`, after every change before it: one at a time, whatever awaits inside. */
  #serial<T>(act: () => Promise<T>): Promise<T> {
    const next = this.#chain.then(act, act);
    this.#chain = next.catch(() => undefined);
    return next;
  }

  #row(authority: string): Row | null {
    return this.#sql.exec<Row & Record<string, SqlStorageValue>>(
      "SELECT authority, sandbox_id, lease_id FROM sandboxes WHERE authority = ?", authority)
      .toArray()[0] ?? null;
  }

  async #run(handle: Handle, argv: readonly string[]): Promise<{ code: number; out: string }> {
    const done = await this.#e2b().exec(handle, [RUNNER, ...argv], { timeoutMs: COMMAND_MS });
    if (done.exitCode !== OK && done.exitCode !== NO) {
      throw new Error(`ta-runner ${argv[0]} failed (${done.exitCode}): ${
        done.stderr.slice(-300)}`);
    }
    return { code: done.exitCode, out: done.stdout };
  }

  /**
   * The authority's sandbox, running: as it is, or resumed, or created with `network`
   * (only a start that opened the lease has one); or `waiting` when one more running
   * sandbox has no room; or `lost` when a joining run finds none.
   */
  async #ensure(owner: string, authority: string, network: E2bNetwork | null,
                limits: SandboxCeilings): Promise<{ handle: Handle } | "waiting" | "lost"> {
    const e2b = this.#e2b();
    const row = this.#row(authority);
    const found = row === null ? null : await e2b.info(row.sandbox_id);
    if (found !== null && found.state === "running" && found.envdAccessToken !== null) {
      const handle = { sandboxID: found.sandboxID, envdAccessToken: found.envdAccessToken };
      if (network !== null) {
        await e2b.network(found.sandboxID, network);
      }
      return { handle };
    }
    if (found === null && network === null) {
      return "lost";  // a joining run's sandbox is gone: nothing configures a new one
    }
    try {
      if (await e2b.running() >= limits.concurrency_ceiling) {
        return "waiting";
      }
      if (found !== null) {
        const resumed = await e2b.connect(found.sandboxID, limits.runtime_ceiling_s);
        if (resumed !== null) {
          const handle = { sandboxID: resumed.sandboxID,
                           envdAccessToken: resumed.envdAccessToken };
          if (network !== null) {
            await e2b.network(resumed.sandboxID, network);
          }
          await this.#prepare(handle, network ?? e2bNetwork(found.network));
          if (found.pauseMode === "filesystem") {
            await this.#run(handle, ["recover"]);  // rebooted: its runs restart from disk
          }
          return { handle };
        }
        if (network === null) {
          return "lost";
        }
      }
      const created = await e2b.create({
        template: this.env.E2B_TEMPLATE, timeoutS: limits.runtime_ceiling_s,
        network: network!,
        // An opaque name, so E2B's own list shows whose it is to us and to no one else.
        metadata: { ta_sandbox: (await sha256(`${owner}\n${authority}`)).slice(0, 32) },
      });
      this.#sql.exec("INSERT INTO sandboxes (authority, sandbox_id, lease_id) VALUES (?, ?, " +
                       "NULL) ON CONFLICT (authority) DO UPDATE SET sandbox_id = " +
                       "excluded.sandbox_id, lease_id = NULL", authority, created.sandboxID);
      await this.#prepare(created, network!);
      return { handle: created };
    } catch (error) {
      if (noRoom(error)) {
        return "waiting";
      }
      throw error;
    }
  }

  /** A sandbox just running: its resolver pinned to the network's, `/proc` hidden. */
  async #prepare(handle: Handle, network: E2bNetwork): Promise<void> {
    const servers = network.dnsServers.length > 0 ? network.dnsServers : DNS_SERVERS;
    await this.#run(handle, ["prepare", ...servers]);
  }

  async start(owner: string, start: SandboxStart): Promise<Started> {
    if (!RUN.test(start.run) || !AUTHORITY.test(start.authority)
        || !LEASE.test(start.lease.lease_id) || (start.tokens === null) !== (start.network === null)) {
      throw new Error("a sandbox start names its run, its authority and its lease");
    }
    const limits = await ceilings(this.env);
    if (limits === null) {
      return { started: false, code: CODES.unconfigured };
    }
    return this.#serial(async () => {
      const e2b = this.#e2b();
      const network = start.network === null ? null : e2bNetwork(start.network.network);
      const hosts = vendorHosts(start);
      const first = network === null ? null : widened(network, hosts) ?? network;
      const got = await this.#ensure(owner, start.authority, first, limits);
      if (got === "waiting") {
        return { started: false, code: CODES.waiting };
      }
      if (got === "lost") {
        return { started: false, code: CODES.lost };
      }
      const { handle } = got;
      if (start.tokens !== null) {
        await e2b.upload(handle, `${STATE_DIR}/lease.json`, json({
          lease: start.lease, authority: start.authority, tokens: start.tokens }));
        this.#sql.exec("UPDATE sandboxes SET lease_id = ? WHERE authority = ?",
                       start.lease.lease_id, start.authority);
      } else if (hosts.length > 0) {
        // A joining run on another engine's sign-in: its vendor's hosts added to the live
        // network (read back whole, its transform with it, and replaced whole).
        const live = await e2b.info(handle.sandboxID);
        const more = live === null ? null : widened(e2bNetwork(live.network), hosts);
        if (more !== null) {
          await e2b.network(handle.sandboxID, more);
        }
      }
      if (start.credential === "vendor_login_in_sandbox") {
        const signedIn = await this.#run(handle, ["signin-check", start.engine]);
        if (signedIn.code === NO) {
          return { started: false, code: CODES.signin };
        }
      }
      await e2b.upload(handle, `${STATE_DIR}/inbox/${start.run}.json`, json(startDocument(start)));
      const launched = await this.#run(handle, ["launch", start.run]);
      if (launched.code !== OK) {
        throw new Error(`the runner did not launch run ${start.run}`);
      }
      this.#sql.exec("INSERT OR REPLACE INTO launched (run, authority, lease_id) VALUES (?, ?, ?)",
                     start.run, start.authority, start.lease.lease_id);
      await this.#arm();
      return { started: true };
    });
  }

  /** The running sandbox a run is in, or null (gone, or paused: its runner is not running). */
  async #live(run: string): Promise<Handle | null> {
    const at = this.#sql.exec<{ authority: string }>(
      "SELECT authority FROM launched WHERE run = ?", run).toArray()[0];
    const row = at === undefined ? null : this.#row(at.authority);
    if (row === null) {
      return null;
    }
    const found = await this.#e2b().info(row.sandbox_id);
    return found === null || found.state !== "running" || found.envdAccessToken === null ? null
      : { sandboxID: found.sandboxID, envdAccessToken: found.envdAccessToken };
  }

  async cancel(_owner: string, request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    if (!RUN.test(request.run)) {
      throw new Error("a cancel names its run");
    }
    return this.#serial(async () => {
      const handle = await this.#live(request.run);
      if (handle === null) {
        return { cancelled: false };
      }
      // SIGTERM to the CLI, then a kill after its grace; the runner then syncs and ends.
      return { cancelled: (await this.#run(handle, ["cancel", request.run])).code === OK };
    });
  }

  async status(_owner: string, request: { run: string; lease_id: string }):
      Promise<{ state: "running" | "exited" | "gone" }> {
    if (!RUN.test(request.run)) {
      throw new Error("a status names its run");
    }
    return this.#serial(async () => {
      const at = this.#sql.exec<{ authority: string }>(
        "SELECT authority FROM launched WHERE run = ?", request.run).toArray()[0];
      const row = at === undefined ? null : this.#row(at.authority);
      const found = row === null ? null : await this.#e2b().info(row.sandbox_id);
      if (found === null) {
        return { state: "gone" };
      }
      const handle = await this.#live(request.run);
      if (handle === null) {
        return { state: "exited" };
      }
      return { state: (await this.#run(handle, ["status", request.run])).code === OK
        ? "running" : "exited" };
    });
  }

  async release(_owner: string, request: { lease_id: string }): Promise<{ paused: boolean }> {
    if (!LEASE.test(request.lease_id)) {
      throw new Error("a release names its lease");
    }
    return this.#serial(async () => {
      const row = this.#sql.exec<Row & Record<string, SqlStorageValue>>(
        "SELECT authority, sandbox_id, lease_id FROM sandboxes WHERE lease_id = ?",
        request.lease_id).toArray()[0];
      if (row === undefined) {
        return { paused: false };  // another lease configured it since: not this one's
      }
      const paused = await this.#pauseIdle(row);
      this.#sql.exec("DELETE FROM launched WHERE lease_id = ?", request.lease_id);
      return { paused };
    });
  }

  /** Pause the sandbox when its runner has no live run; whether it is paused now. */
  async #pauseIdle(row: Row): Promise<boolean> {
    const e2b = this.#e2b();
    const found = await e2b.info(row.sandbox_id);
    if (found === null || found.state !== "running" || found.envdAccessToken === null) {
      return found !== null && found.state === "paused";
    }
    const handle = { sandboxID: found.sandboxID, envdAccessToken: found.envdAccessToken };
    const state = await this.#runnerState(handle);
    if (state.live > 0) {
      return false;
    }
    return (await this.#pause(found.sandboxID, Date.now() + PAUSE_TRIES * PAUSE_BACKOFF_MS))
      === "paused";
  }

  async #runnerState(handle: Handle): Promise<{ live: number; quiet: boolean }> {
    const { out } = await this.#run(handle, ["state"]);
    const parsed = JSON.parse(out) as { live?: unknown; quiet?: unknown };
    if (typeof parsed.live !== "number" || typeof parsed.quiet !== "boolean") {
      throw new Error("the runner's state is out of shape");
    }
    return { live: parsed.live, quiet: parsed.quiet };
  }

  /** Pause it, asking again while E2B is busy, until `until`. */
  async #pause(id: string, until: number): Promise<"paused" | "busy" | "gone"> {
    for (;;) {
      const paused = await this.#e2b().pause(id);
      if (paused !== "busy" || Date.now() + PAUSE_BACKOFF_MS > until) {
        return paused;
      }
      await new Promise((resolve) => setTimeout(resolve, PAUSE_BACKOFF_MS));
    }
  }

  /** The alarm at the soonest renewal look of any running sandbox; none, no alarm. */
  async #arm(at?: number): Promise<void> {
    const limits = await ceilings(this.env);
    if (limits === null) {
      return;
    }
    const e2b = this.#e2b();
    let soonest = at ?? Number.POSITIVE_INFINITY;
    const { leadS } = renewal(limits.runtime_ceiling_s);
    for (const row of this.#sql.exec<Row & Record<string, SqlStorageValue>>(
      "SELECT authority, sandbox_id, lease_id FROM sandboxes").toArray()) {
      const found = await e2b.info(row.sandbox_id);
      if (found?.state === "running") {
        soonest = Math.min(soonest, Date.parse(found.startedAt)
                           + (limits.runtime_ceiling_s - leadS) * 1000);
      }
    }
    if (Number.isFinite(soonest)) {
      await this.ctx.storage.setAlarm(Math.max(Date.now(), soonest));
    } else {
      await this.ctx.storage.deleteAlarm();
    }
  }

  /** Each running sandbox looked at: renewed at a quiet moment or at the edge, or paused. */
  override async alarm(): Promise<void> {
    const limits = await ceilings(this.env);
    if (limits === null) {
      return;  // E2B's own timeout pauses each at its plan's ceiling
    }
    const next = await this.#serial(async () => {
      const e2b = this.#e2b();
      const { leadS, edgeS, stepS } = renewal(limits.runtime_ceiling_s);
      let soonest = Number.POSITIVE_INFINITY;
      for (const row of this.#sql.exec<Row & Record<string, SqlStorageValue>>(
        "SELECT authority, sandbox_id, lease_id FROM sandboxes").toArray()) {
        const found = await e2b.info(row.sandbox_id);
        if (found === null || found.state !== "running" || found.envdAccessToken === null) {
          continue;
        }
        const handle = { sandboxID: found.sandboxID, envdAccessToken: found.envdAccessToken };
        const startedAt = Date.parse(found.startedAt);
        const elapsedS = (Date.now() - startedAt) / 1000;
        if (elapsedS < limits.runtime_ceiling_s - leadS) {
          soonest = Math.min(soonest, startedAt + (limits.runtime_ceiling_s - leadS) * 1000);
          continue;
        }
        const state = await this.#runnerState(handle);
        if (state.live === 0) {
          await this.#pause(found.sandboxID, startedAt + limits.runtime_ceiling_s * 1000);
          continue;
        }
        if (state.quiet || elapsedS >= limits.runtime_ceiling_s - edgeS) {
          await this.#renew(handle, startedAt + limits.runtime_ceiling_s * 1000, limits);
          soonest = Math.min(soonest, Date.now() + (limits.runtime_ceiling_s - leadS) * 1000);
          continue;
        }
        soonest = Math.min(soonest, Date.now() + stepS * 1000);
      }
      return soonest;
    });
    if (Number.isFinite(next)) {
      await this.ctx.storage.setAlarm(Math.max(Date.now(), next));
    }
  }

  /**
   * One renewal: the runner posts `renewing` and holds its posts; the sandbox is paused with
   * its memory (asked again while E2B is busy, until its ceiling, where E2B's own timeout
   * pauses it) and resumed; the runner reconnects (`renewed`), or, when E2B kept its files
   * only, restarts its runs from their sessions (`recover`).
   */
  async #renew(handle: Handle, ceilingAt: number, limits: SandboxCeilings): Promise<void> {
    const e2b = this.#e2b();
    await this.#run(handle, ["renewing"]);
    const paused = await this.#pause(handle.sandboxID, ceilingAt);
    if (paused === "gone") {
      return;
    }
    if (paused === "busy") {
      // Still busy at the ceiling: E2B's timeout pauses it (memory kept, retried for about
      // two minutes, else filesystem-only); wait for that, then resume as below.
      for (let i = 0; i < 90; i += 1) {
        const found = await e2b.info(handle.sandboxID);
        if (found === null) {
          return;
        }
        if (found.state === "paused") {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, PAUSE_BACKOFF_MS));
      }
    }
    const before = await e2b.info(handle.sandboxID);
    const resumed = await e2b.connect(handle.sandboxID, limits.runtime_ceiling_s);
    if (resumed === null) {
      return;
    }
    const after = { sandboxID: resumed.sandboxID, envdAccessToken: resumed.envdAccessToken };
    if (before?.pauseMode === "filesystem") {
      await this.#prepare(after, e2bNetwork(before.network));
      await this.#run(after, ["recover"]);
    } else {
      await this.#run(after, ["renewed"]);
    }
  }
}
