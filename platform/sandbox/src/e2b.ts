/**
 * E2B, as the sandbox Worker speaks to it: the one module that fetches, and only E2B's two
 * hosts (`guards/config.test.ts`). Raw HTTP, no SDK, in the field names of E2B's OpenAPI
 * document (`docs.e2b.dev/openapi-public.yaml`, read 2026-11-03), checked live by this
 * slice's probes (`openspec/changes/clean-sheet-platform/sandbox-probe.md`):
 *
 * - the platform API, `api.e2b.app`, by the project's API key (`X-API-Key`): create
 *   (`POST /v2/sandboxes`), info (`GET /sandboxes/{id}`), pause
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
 * Every answer outside the documented shape fails loudly (`E2bError`, its status and
 * E2B's own `error_code`), never a guess.
 */

export const E2B_API = "https://api.e2b.app";
export const E2B_ENVD = "https://sandbox.e2b.app";
export const ENVD_PORT = 49983;

/** E2B answered other than the call expects. */
export class E2bError extends Error {
  override readonly name = "E2bError";
  constructor(readonly status: number, readonly code: string | null, message: string,
              readonly retryAfter: number | null = null) {
    super(message);
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

/** E2B's network document (`SandboxNetworkConfig`), as `owner/src/network.ts` makes it. */
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

/** The platform API and envd, by the project's key; `fetcher` is the Worker's own fetch. */
export class E2b {
  readonly #key: string;
  readonly #fetch: typeof fetch;

  constructor(key: string, fetcher: typeof fetch = (input, init) => fetch(input, init)) {
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
      const after = Number(response.headers.get("Retry-After"));
      throw new E2bError(response.status, code, `E2B ${method} ${path.split("?")[0]} answered ${
        response.status}`, Number.isFinite(after) && after > 0 ? after : null);
    }
    return { status: response.status, json: text === "" ? null : JSON.parse(text) };
  }

  /** A new sandbox from `template`, its network and its timeout, paused on timeout. */
  async create(request: { template: string; timeoutS: number; network: E2bNetwork;
                          metadata: Readonly<Record<string, string>> }): Promise<Created> {
    const { json } = await this.#api("POST", "/v2/sandboxes", {
      templateID: request.template, timeout: request.timeoutS, autoPause: true,
      autoPauseMemory: true, autoResume: { enabled: false }, network: request.network,
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
   * How many of the project's sandboxes run now: E2B's own count (`X-Total-Running`, the
   * running sandboxes matching the list's filters before pagination), so one call reads it
   * whatever the plan's size.
   */
  async running(): Promise<number> {
    const response = await this.#fetch(`${E2B_API}/v2/sandboxes?state=running&limit=1`, {
      headers: { "X-API-Key": this.#key } });
    await response.arrayBuffer();
    const total = response.headers.get("x-total-running");
    if (response.status !== 200 || total === null || !/^[0-9]{1,9}$/.test(total)) {
      throw new E2bError(response.status, null, "E2B's list gave no running count");
    }
    return Number(total);
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
   */
  async exec(handle: Handle, argv: readonly string[], options: { user?: string;
             timeoutMs: number }): Promise<Exec> {
    if (argv.length === 0 || !argv[0]!.startsWith("/")) {
      throw new Error("a command is an absolute path and its arguments");
    }
    const response = await this.#fetch(`${E2B_ENVD}/process.Process/Start`, {
      method: "POST",
      headers: { ...this.#envd(handle, options.user ?? "root"),
                 "Content-Type": "application/connect+json", "Connect-Protocol-Version": "1",
                 "Connect-Timeout-Ms": String(options.timeoutMs) },
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
