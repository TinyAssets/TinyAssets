/**
 * E2B, as the sandbox Worker speaks to it: the one module that fetches, and only E2B's two
 * hosts (`guards/config.test.ts`). Raw HTTP, no SDK, in the field names of E2B's OpenAPI
 * document (`docs.e2b.dev/openapi-public.yaml`, read 2026-11-03), checked live by this
 * slice's probes (`openspec/changes/clean-sheet-platform/sandbox-probe.md`):
 *
 * - the platform API, `api.e2b.app`, by the project's API key (`X-API-Key`): create
 *   (`POST /v2/sandboxes`, `secure: true`, so envd takes only the sandbox's own envd token,
 *   probed live), info (`GET /sandboxes/{id}`), pause
 *   (`POST /sandboxes/{id}/pause`, `503` while a previous snapshot finishes: the sandbox
 *   keeps running), connect, which resumes a paused sandbox and only extends its timeout
 *   (`POST /v2/sandboxes/{id}/connect`), the network's replacement
 *   (`PUT /sandboxes/{id}/network`), the list (`GET /v2/sandboxes`) and kill;
 * - a sandbox's own daemon, envd, on the shared sandbox host `sandbox.e2b.app`, routed by
 *   `E2b-Sandbox-Id` and `E2b-Sandbox-Port`, by the sandbox's envd token
 *   (`X-Access-Token`), its user as HTTP Basic: a file upload (`POST /files`, the bytes
 *   as `application/octet-stream`) and a process (`/process.Process/Start`, a Connect
 *   server stream of `application/connect+json` envelopes: the start event with the pid,
 *   stdout and stderr in base64, then the end event with the exit code). A process the
 *   stream is dropped from keeps running (probed); the runner's own launch daemonizes
 *   anyway, so nothing relies on that.
 *
 * The deploy kit (`platform/deploy/deploy.ts`) speaks to E2B through this same client: a
 * template's ready build (`ready`: the alias, then the tag, as `sandbox-template.yml` asks
 * too) and the team's event webhooks (`webhooks`, `addWebhook`, `changeWebhook`, e2b-dev/
 * infra's `spec/openapi.yml`, read 2026-11-04). It imports nothing, so Node's type
 * stripping runs it there by its own name.
 *
 * Every answer outside the documented shape fails loudly (`E2bError`, its status and
 * E2B's own `error_code`), never a guess.
 */

export const E2B_API = "https://api.e2b.app";
export const E2B_ENVD = "https://sandbox.e2b.app";
export const ENVD_PORT = 49983;

/** E2B answered other than the call expects. */
export class E2bError extends Error {
  override readonly name = "E2bError";
  readonly status: number;
  readonly code: string | null;

  // Fields, not parameter properties: Node's type stripping, which runs this file in the
  // deploy kit, takes no parameter property.
  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/**
 * Whether an answer means E2B has no room for one more running sandbox now: its
 * concurrency limit (`429`) or no capacity to place one (`503`, or the
 * `sandbox_capacity_unavailable` code). A run then waits, never refused.
 */
export function noRoom(error: unknown): boolean {
  return error instanceof E2bError && (error.status === 429 || error.status === 503
    || error.code === "sandbox_capacity_unavailable");
}

export type SandboxState = "running" | "paused";

/** What the platform API tells of one sandbox. */
export interface SandboxInfo {
  readonly sandboxID: string;
  readonly state: SandboxState;
  readonly startedAt: string;
  readonly endAt: string;
  /** What its last pause saved: `filesystem` reboots it on resume, memory lost. */
  readonly pauseMode: "full" | "filesystem" | null;
  readonly envdAccessToken: string | null;
  readonly metadata: Readonly<Record<string, string>>;
  readonly network: unknown;
}

/** A running sandbox, as envd is reached. */
export interface Handle {
  readonly sandboxID: string;
  readonly envdAccessToken: string;
}

/** E2B's network document (`SandboxNetworkConfig`), as the owner's network plan makes it. */
export interface E2bNetwork {
  readonly allowOut: readonly string[];
  readonly denyOut: readonly string[];
  readonly dnsServers: readonly string[];
  readonly rules: Readonly<Record<string, readonly unknown[]>>;
}

export interface Created {
  readonly sandboxID: string;
  readonly envdAccessToken: string;
}

export interface Exec {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}

const SANDBOX_ID = /^[a-z0-9]{1,64}$/;

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function base64(text: string): string {
  let binary = "";
  for (const byte of new TextEncoder().encode(text)) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function unbase64(text: string): Uint8Array {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** One Connect envelope: a flags byte, a big-endian length, the JSON message. */
export function envelope(message: unknown, flags = 0): Uint8Array {
  const body = new TextEncoder().encode(JSON.stringify(message));
  const out = new Uint8Array(5 + body.length);
  out[0] = flags;
  new DataView(out.buffer).setUint32(1, body.length);
  out.set(body, 5);
  return out;
}

/** The envelopes of a whole Connect stream, each its flags and its JSON message. */
export function envelopes(bytes: Uint8Array): { flags: number; message: unknown }[] {
  const out: { flags: number; message: unknown }[] = [];
  let at = 0;
  while (at + 5 <= bytes.length) {
    const length = new DataView(bytes.buffer, bytes.byteOffset + at + 1, 4).getUint32(0);
    if (at + 5 + length > bytes.length) {
      throw new E2bError(502, null, "envd's stream ended inside an envelope");
    }
    const text = new TextDecoder("utf-8", { fatal: false, ignoreBOM: false }).decode(bytes.subarray(at + 5, at + 5 + length));
    out.push({ flags: bytes[at]!, message: text === "" ? {} : JSON.parse(text) });
    at += 5 + length;
  }
  if (at !== bytes.length) {
    throw new E2bError(502, null, "envd's stream ended inside an envelope header");
  }
  return out;
}

function info(value: unknown): SandboxInfo {
  if (!object(value) || typeof value["sandboxID"] !== "string"
      || (value["state"] !== "running" && value["state"] !== "paused")
      || typeof value["startedAt"] !== "string" || typeof value["endAt"] !== "string") {
    throw new E2bError(502, null, "E2B's sandbox info is out of shape");
  }
  const mode = value["pauseMode"];
  return {
    sandboxID: value["sandboxID"], state: value["state"], startedAt: value["startedAt"],
    endAt: value["endAt"],
    pauseMode: mode === "full" || mode === "filesystem" ? mode : null,
    envdAccessToken: typeof value["envdAccessToken"] === "string"
      ? value["envdAccessToken"] : null,
    metadata: object(value["metadata"]) ? value["metadata"] as Record<string, string> : {},
    network: value["network"] ?? null,
  };
}

/**
 * The Worker's and the template checks' `E2b`, on the runtime's own fetch: called on its own,
 * since workerd refuses a fetch called as another object's method. The fetch never leaves
 * this module, so every call it makes is one of `E2b`'s, on E2B's origins.
 */
export function runtimeE2b(key: string): E2b {
  return new E2b(key, (url, init) => fetch(url, init));
}

/**
 * The platform API and envd, by the project's key and its caller's `fetcher`: the runtime's
 * (`runtimeE2b`), or the deploy kit's recorded IO.
 */
export class E2b {
  readonly #key: string;
  readonly #fetch: (url: string, init?: RequestInit) => Promise<Response>;

  constructor(key: string, fetcher: (url: string, init?: RequestInit) => Promise<Response>) {
    if (typeof key !== "string" || key.length < 8) {
      throw new Error("E2B_API_KEY is not set: the sandbox Worker reaches E2B by it");
    }
    this.#key = key;
    this.#fetch = fetcher;
  }

  async #api(method: string, path: string, body?: unknown, ok: readonly number[] = [200]):
      Promise<{ status: number; json: unknown }> {
    const response = await this.#fetch(`${E2B_API}${path}`, {
      method,
      headers: { "X-API-Key": this.#key,
                 ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    if (!ok.includes(response.status)) {
      let code: string | null = null;
      try {
        const parsed: unknown = JSON.parse(text);
        code = object(parsed) && typeof parsed["error_code"] === "string"
          ? parsed["error_code"] : null;
      } catch {
        code = null;
      }
      throw new E2bError(response.status, code, `E2B ${method} ${path.split("?")[0]} answered ${
        response.status}`);
    }
    return { status: response.status, json: text === "" ? null : JSON.parse(text) };
  }

  /**
   * A new sandbox from `template`, its network and its timeout, paused on timeout with its
   * memory. `secure`: envd answers only a caller with the sandbox's envd token (without it,
   * anything that reaches envd could run commands as root, undoing the per-run users).
   */
  async create(request: { template: string; timeoutS: number; network: E2bNetwork;
                          metadata: Readonly<Record<string, string>> }): Promise<Created> {
    const { json } = await this.#api("POST", "/v2/sandboxes", {
      templateID: request.template, timeout: request.timeoutS, secure: true, autoPause: true,
      autoPauseMemory: true, autoResume: { enabled: false }, network: request.network,
      metadata: request.metadata,
    }, [201, 200]);
    return created(json);
  }

  /**
   * A throwaway sandbox (the template's checks), killed, never paused, at its timeout, so
   * one a caller lost is gone at its timeout. Its network is E2B's default, open, unless
   * one is given.
   */
  async createThrowaway(request: { template: string; timeoutS: number;
                                   network: E2bNetwork | null;
                                   metadata: Readonly<Record<string, string>> }):
      Promise<Created> {
    const { json } = await this.#api("POST", "/v2/sandboxes", {
      templateID: request.template, timeout: request.timeoutS, secure: true, autoPause: false,
      ...(request.network === null ? {} : { network: request.network }),
      metadata: request.metadata,
    }, [201, 200]);
    return created(json);
  }

  /** The sandbox's info, or null when E2B has no such sandbox (killed). */
  async info(id: string): Promise<SandboxInfo | null> {
    try {
      return info((await this.#api("GET", `/sandboxes/${sandboxId(id)}`)).json);
    } catch (error) {
      if (error instanceof E2bError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  /**
   * Pause it with its memory: `paused`, or `busy` when E2B refused because a previous
   * snapshot is still being written (the sandbox runs on, unharmed), or `gone`.
   */
  async pause(id: string): Promise<"paused" | "busy" | "gone"> {
    try {
      await this.#api("POST", `/sandboxes/${sandboxId(id)}/pause`, { memory: true },
                      [204, 409]);
      return "paused";
    } catch (error) {
      if (error instanceof E2bError && error.status === 503) {
        return "busy";
      }
      if (error instanceof E2bError && error.status === 404) {
        return "gone";
      }
      throw error;
    }
  }

  /** Resume it if paused (memory restored), its timeout at least `timeoutS` from now. */
  async connect(id: string, timeoutS: number): Promise<Created | null> {
    try {
      return created((await this.#api("POST", `/v2/sandboxes/${sandboxId(id)}/connect`,
                                       { timeout: timeoutS }, [200, 201])).json);
    } catch (error) {
      if (error instanceof E2bError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  /** Replace its network whole (every omitted field is cleared, so none is omitted). */
  async network(id: string, network: E2bNetwork): Promise<void> {
    await this.#api("PUT", `/sandboxes/${sandboxId(id)}/network`, network, [204, 200]);
  }

  async kill(id: string): Promise<void> {
    await this.#api("DELETE", `/sandboxes/${sandboxId(id)}`, undefined, [204, 404]);
  }

  /**
   * How many sandboxes E2B runs now for the whole team the key is of (`X-Total-Running`,
   * the running sandboxes before pagination: probes and other templates count too), or
   * null when E2B gave no count. A count is advisory: two owners may read it at once, so
   * E2B's own refusal (`noRoom`) is what holds the plan, and an unknown count means "try".
   */
  async running(): Promise<number | null> {
    const response = await this.#fetch(`${E2B_API}/v2/sandboxes?state=running&limit=1`, {
      headers: { "X-API-Key": this.#key } });
    await response.arrayBuffer();
    if (response.status !== 200) {
      throw new E2bError(response.status, null, `E2B's list answered ${response.status}`);
    }
    const total = response.headers.get("x-total-running");
    return total !== null && /^[0-9]{1,9}$/.test(total) ? Number(total) : null;
  }

  /**
   * Every sandbox of the team, running or paused, whose metadata holds `key`=`value`: the
   * ids, page by page (`x-next-token`) to the end.
   */
  async list(key: string, value: string): Promise<string[]> {
    const ids: string[] = [];
    let next: string | null = null;
    do {
      const query = new URLSearchParams({ metadata: `${encodeURIComponent(key)}=${
        encodeURIComponent(value)}`, state: "running,paused" });
      if (next !== null) {
        query.set("nextToken", next);
      }
      const response = await this.#fetch(`${E2B_API}/v2/sandboxes?${query}`, {
        headers: { "X-API-Key": this.#key } });
      const text = await response.text();
      if (response.status !== 200) {
        throw new E2bError(response.status, null, `E2B's list answered ${response.status}`);
      }
      const page: unknown = JSON.parse(text);
      if (!Array.isArray(page) || !page.every((each) => object(each)
                                              && typeof each["sandboxID"] === "string")) {
        throw new E2bError(502, null, "E2B's list is out of shape");
      }
      for (const each of page as { sandboxID: string; metadata?: unknown }[]) {
        const metadata = object(each.metadata) ? each.metadata : {};
        if (metadata[key] === value) {  // E2B's own filter, checked
          ids.push(sandboxId(each.sandboxID));
        }
      }
      next = response.headers.get("x-next-token");
    } while (next !== null && next !== "");
    return ids;
  }

  /**
   * Whether `template` (`name:tag`) has a ready build in this team (E2B's OpenAPI alias and
   * tag checks): the alias's template, then whether it holds the tag. No such alias is
   * false; any other failed lookup throws.
   */
  async ready(template: string): Promise<boolean> {
    const at = template.lastIndexOf(":");
    if (at < 1) {
      throw new Error("E2B_TEMPLATE is name:tag");
    }
    const headers = { "X-API-Key": this.#key, Accept: "application/json" };
    const alias = await this.#fetch(`${E2B_API}/templates/aliases/${
      encodeURIComponent(template.slice(0, at))}`, { headers });
    if (alias.status === 404) {
      return false;
    }
    if (alias.status !== 200) {
      throw new Error(`E2B's alias lookup answered ${alias.status}`);
    }
    const { templateID } = await alias.json() as { templateID?: string };
    const tag = await this.#fetch(`${E2B_API}/templates/${
      encodeURIComponent(String(templateID))}/tags/exists?tag=${
      encodeURIComponent(template.slice(at + 1))}`, { headers });
    if (tag.status !== 200) {
      throw new Error(`E2B's tag lookup answered ${tag.status}`);
    }
    return (await tag.json() as { exists?: unknown }).exists === true;
  }

  /**
   * `tags` assigned to the build `target` (`name:tag`) names (E2B's OpenAPI
   * `AssignTemplateTagsRequest`): how the template's checks mark a pair that passed.
   */
  async tag(target: string, tags: readonly string[]): Promise<void> {
    await this.#api("POST", "/templates/tags", { target, tags: [...tags] }, [200, 201, 204]);
  }

  #hooks(): Record<string, string> {
    return { "X-API-Key": this.#key, "Content-Type": "application/json" };
  }

  /** The team's event webhooks, as E2B lists them; a failed listing throws. */
  async webhooks(): Promise<unknown[]> {
    const listed = await this.#fetch(`${E2B_API}/events/webhooks`, { headers: this.#hooks() });
    if (listed.status !== 200) {
      throw new Error(`E2B listed its webhooks with ${listed.status}`);
    }
    const hooks = await listed.json() as unknown;
    if (!Array.isArray(hooks)) {
      throw new Error("E2B's webhook listing holds no list");
    }
    return hooks;
  }

  /** One more event webhook, as `hook` describes it; a refusal throws. */
  async addWebhook(hook: Readonly<Record<string, unknown>>): Promise<void> {
    const made = await this.#fetch(`${E2B_API}/events/webhooks`, {
      method: "POST", headers: this.#hooks(), body: JSON.stringify(hook) });
    if (made.status !== 200 && made.status !== 201) {
      throw new Error(`E2B refused the webhook with ${made.status}`);
    }
  }

  /** The webhook `id`'s fields `change` names set; a refusal throws. */
  async changeWebhook(id: string, change: Readonly<Record<string, unknown>>): Promise<void> {
    const changed = await this.#fetch(`${E2B_API}/events/webhooks/${encodeURIComponent(id)}`, {
      method: "PATCH", headers: this.#hooks(), body: JSON.stringify(change) });
    if (changed.status !== 200) {
      throw new Error(`E2B refused the webhook's change with ${changed.status}`);
    }
  }

  #envd(handle: Handle, user: string): Record<string, string> {
    return {
      "E2b-Sandbox-Id": sandboxId(handle.sandboxID),
      "E2b-Sandbox-Port": String(ENVD_PORT),
      "X-Access-Token": handle.envdAccessToken,
      Authorization: `Basic ${base64(`${user}:`)}`,
    };
  }

  /** Write `bytes` at `path` as `user` (parents made); the file is replaced whole. */
  async upload(handle: Handle, path: string, bytes: Uint8Array, user = "root"): Promise<void> {
    if (!path.startsWith("/") || path.includes("\0")) {
      throw new Error("an upload's path is absolute");
    }
    const query = `path=${encodeURIComponent(path)}&username=${encodeURIComponent(user)}`;
    const response = await this.#fetch(`${E2B_ENVD}/files?${query}`, {
      method: "POST",
      headers: { ...this.#envd(handle, user), "Content-Type": "application/octet-stream" },
      body: bytes,
    });
    await response.arrayBuffer();
    if (response.status !== 200) {
      throw new E2bError(response.status, null, `envd's upload answered ${response.status}`);
    }
  }

  /**
   * Run `argv` in the sandbox as `user`, no shell, with an empty environment beside what
   * envd sets, and wait for it to end (at most `timeoutMs`): its output and exit code.
   * `keepalive`: envd pings the stream every 50 s, as E2B's SDK asks it to, so a command
   * quiet for minutes keeps its stream (a fetch outside workerd gives up on a body silent
   * for 300 s); the template's checks ask it.
   */
  async exec(handle: Handle, argv: readonly string[], options: { user?: string;
             timeoutMs: number; keepalive?: boolean }): Promise<Exec> {
    if (argv.length === 0 || !argv[0]!.startsWith("/")) {
      throw new Error("a command is an absolute path and its arguments");
    }
    const response = await this.#fetch(`${E2B_ENVD}/process.Process/Start`, {
      method: "POST",
      headers: { ...this.#envd(handle, options.user ?? "root"),
                 "Content-Type": "application/connect+json", "Connect-Protocol-Version": "1",
                 "Connect-Timeout-Ms": String(options.timeoutMs),
                 ...(options.keepalive === true ? { "Keepalive-Ping-Interval": "50" } : {}) },
      body: envelope({ process: { cmd: argv[0], args: argv.slice(1), envs: {} },
                       stdin: false }),
    });
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (response.status !== 200) {
      throw new E2bError(response.status, null, `envd's process start answered ${
        response.status}`);
    }
    const out: Uint8Array[] = [];
    const err: Uint8Array[] = [];
    let exitCode: number | null = null;
    for (const { flags, message } of envelopes(bytes)) {
      if ((flags & 2) !== 0) {
        if (object(message) && object(message["error"])) {
          throw new E2bError(502, String(message["error"]["code"] ?? ""),
                             "envd's process stream ended in an error");
        }
        continue;
      }
      const event = object(message) && object(message["event"]) ? message["event"] : {};
      const data = object(event["data"]) ? event["data"] : null;
      if (data !== null && typeof data["stdout"] === "string") {
        out.push(unbase64(data["stdout"]));
      }
      if (data !== null && typeof data["stderr"] === "string") {
        err.push(unbase64(data["stderr"]));
      }
      if (object(event["end"])) {
        const code = event["end"]["exitCode"];
        exitCode = typeof code === "number" ? code : 0;
      }
    }
    if (exitCode === null) {
      throw new E2bError(502, null, "envd's process stream ended with no end event");
    }
    const join = (parts: Uint8Array[]) => new TextDecoder("utf-8", { fatal: false, ignoreBOM: false }).decode(new Uint8Array(
      parts.flatMap((part) => [...part])));
    return { stdout: join(out), stderr: join(err), exitCode };
  }
}

function sandboxId(id: string): string {
  if (!SANDBOX_ID.test(id)) {
    throw new Error("an E2B sandbox id is lowercase letters and digits");
  }
  return id;
}

function created(value: unknown): Created {
  if (!object(value) || typeof value["sandboxID"] !== "string"
      || typeof value["envdAccessToken"] !== "string") {
    throw new E2bError(502, null, "E2B's sandbox is out of shape");
  }
  return { sandboxID: sandboxId(value["sandboxID"]), envdAccessToken: value["envdAccessToken"] };
}
