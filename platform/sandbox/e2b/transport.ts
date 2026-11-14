/**
 * E2B's wire, for every caller of it (untangle B11): the sandbox Worker (`src/e2b.ts`), the
 * template's checks and the deploy kit (`admin.ts`), and the sandbox's tools. Raw HTTP, no
 * SDK, in the field names of E2B's OpenAPI document (`docs.e2b.dev/openapi-public.yaml`, read
 * 2026-11-03), checked live by the sandbox's probes
 * (`openspec/changes/clean-sheet-platform/sandbox-probe.md`):
 *
 * - the platform API, `api.e2b.app`, by the project's API key (`X-API-Key`), through `api`
 *   alone, so every refusal is one `E2bError` with E2B's own `error_code`; here, the calls
 *   every caller makes of a sandbox it holds: its network's replacement
 *   (`PUT /sandboxes/{id}/network`) and its kill;
 * - a sandbox's own daemon, envd, on the shared sandbox host `sandbox.e2b.app`, routed by
 *   `E2b-Sandbox-Id` and `E2b-Sandbox-Port`, by the sandbox's envd token
 *   (`X-Access-Token`), as root (HTTP Basic): a file upload (`POST /files`, the bytes as
 *   `application/octet-stream`) and a process (`/process.Process/Start`, a Connect server
 *   stream of `application/connect+json` envelopes: the start event with the pid, stdout and
 *   stderr in base64, then the end event with the exit code). A process the stream is dropped
 *   from keeps running (probed); the runner's own launch daemonizes anyway, so nothing relies
 *   on that.
 *
 * It imports nothing at run time, so Node's type stripping runs it in the deploy kit by its
 * own name. Every answer outside the documented shape fails loudly, never a guess.
 */
import type { NetworkDocument } from "../../contract/src/sandbox.ts";

export const E2B_API = "https://api.e2b.app";
export const E2B_ENVD = "https://sandbox.e2b.app";
const ENVD_PORT = 49983;

/** E2B answered other than the call expects: its status, and E2B's `error_code` if it gave one. */
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

/** How a client reaches E2B: the runtime's own fetch, or a caller's recorded IO. */
export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

/**
 * The runtime's own fetch, called on its own, since workerd refuses a fetch called as another
 * object's method. The fetch is named here alone (`guards/config.test.ts`).
 */
export const runtimeFetch: Fetcher = (url, init) => fetch(url, init);

/** A running sandbox, as envd is reached. */
export interface Handle {
  readonly sandboxID: string;
  readonly envdAccessToken: string;
}

export interface Exec {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}

/** One answer of the platform API: its status, its JSON (null when empty) and its headers. */
export interface Answer {
  readonly status: number;
  readonly json: unknown;
  readonly headers: Headers;
}

const SANDBOX_ID = /^[a-z0-9]{1,64}$/;

export function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function sandboxId(id: string): string {
  if (!SANDBOX_ID.test(id)) {
    throw new Error("an E2B sandbox id is lowercase letters and digits");
  }
  return id;
}

/** A sandbox E2B made or resumed, as envd is reached. */
export function created(value: unknown): Handle {
  if (!object(value) || typeof value["sandboxID"] !== "string"
      || typeof value["envdAccessToken"] !== "string") {
    throw new E2bError(502, null, "E2B's sandbox is out of shape");
  }
  return { sandboxID: sandboxId(value["sandboxID"]), envdAccessToken: value["envdAccessToken"] };
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

/** One Connect envelope, as a request sends it: no flags, a big-endian length, the JSON. */
function envelope(message: unknown): Uint8Array {
  const body = new TextEncoder().encode(JSON.stringify(message));
  const out = new Uint8Array(5 + body.length);
  new DataView(out.buffer).setUint32(1, body.length);
  out.set(body, 5);
  return out;
}

/** The envelopes of a whole Connect stream, each its flags and its JSON message. */
function envelopes(bytes: Uint8Array): { flags: number; message: unknown }[] {
  const out: { flags: number; message: unknown }[] = [];
  let at = 0;
  while (at + 5 <= bytes.length) {
    const length = new DataView(bytes.buffer, bytes.byteOffset + at + 1, 4).getUint32(0);
    if (at + 5 + length > bytes.length) {
      throw new E2bError(502, null, "envd's stream ended inside an envelope");
    }
    const text = new TextDecoder().decode(bytes.subarray(at + 5, at + 5 + length));
    out.push({ flags: bytes[at]!, message: text === "" ? {} : JSON.parse(text) });
    at += 5 + length;
  }
  if (at !== bytes.length) {
    throw new E2bError(502, null, "envd's stream ended inside an envelope header");
  }
  return out;
}

/**
 * The platform API and envd, by the project's key and its caller's `fetcher`: what every
 * client of E2B shares. The sandbox Worker's (`src/e2b.ts`) and the tools' (`admin.ts`) each
 * add their own calls, through `api`.
 */
export class E2bTransport {
  readonly #key: string;
  readonly #fetch: Fetcher;

  constructor(key: string, fetcher: Fetcher) {
    if (typeof key !== "string" || key.length < 8) {
      throw new Error("E2B_API_KEY is not set: E2B is reached by it");
    }
    this.#key = key;
    this.#fetch = fetcher;
  }

  /** One platform API call: any status outside `ok` throws, with E2B's own `error_code`. */
  protected async api(method: string, path: string, body?: unknown,
                      ok: readonly number[] = [200]): Promise<Answer> {
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
    return { status: response.status, json: text === "" ? null : JSON.parse(text),
             headers: response.headers };
  }

  /** Replace its network whole (every omitted field is cleared, so none is omitted). */
  async network(id: string, network: NetworkDocument): Promise<void> {
    await this.api("PUT", `/sandboxes/${sandboxId(id)}/network`, network, [204, 200]);
  }

  async kill(id: string): Promise<void> {
    await this.api("DELETE", `/sandboxes/${sandboxId(id)}`, undefined, [204, 404]);
  }

  #envd(handle: Handle): Record<string, string> {
    return {
      "E2b-Sandbox-Id": sandboxId(handle.sandboxID),
      "E2b-Sandbox-Port": String(ENVD_PORT),
      "X-Access-Token": handle.envdAccessToken,
      Authorization: `Basic ${base64("root:")}`,
    };
  }

  /** Write `bytes` at `path` as root (parents made); the file is replaced whole. */
  async upload(handle: Handle, path: string, bytes: Uint8Array): Promise<void> {
    if (!path.startsWith("/") || path.includes("\0")) {
      throw new Error("an upload's path is absolute");
    }
    const response = await this.#fetch(`${E2B_ENVD}/files?path=${encodeURIComponent(path)
      }&username=root`, {
      method: "POST",
      headers: { ...this.#envd(handle), "Content-Type": "application/octet-stream" },
      body: bytes,
    });
    await response.arrayBuffer();
    if (response.status !== 200) {
      throw new E2bError(response.status, null, `envd's upload answered ${response.status}`);
    }
  }

  /**
   * Run `argv` in the sandbox as root, no shell, with an empty environment beside what envd
   * sets, and wait for it to end (at most `timeoutMs`): its output and exit code.
   * `keepalive`: envd pings the stream every 50 s, as E2B's SDK asks it to, so a command
   * quiet for minutes keeps its stream. Node's fetch gives up on a body silent for 300 s and
   * workerd's does not, so the callers that run in Node ask it (the template's checks, the
   * pause probe) and the Worker never does.
   */
  async exec(handle: Handle, argv: readonly string[],
             options: { readonly timeoutMs: number; readonly keepalive?: true }): Promise<Exec> {
    if (argv.length === 0 || !argv[0]!.startsWith("/")) {
      throw new Error("a command is an absolute path and its arguments");
    }
    const response = await this.#fetch(`${E2B_ENVD}/process.Process/Start`, {
      method: "POST",
      headers: { ...this.#envd(handle),
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
        // envd speaks proto3 JSON, which omits a field at its zero value: an end event with
        // no `exitCode` is exit 0.
        const code = event["end"]["exitCode"];
        exitCode = typeof code === "number" ? code : 0;
      }
    }
    if (exitCode === null) {
      throw new E2bError(502, null, "envd's process stream ended with no end event");
    }
    const join = (parts: Uint8Array[]) => new TextDecoder().decode(new Uint8Array(
      parts.flatMap((part) => [...part])));
    return { stdout: join(out), stderr: join(err), exitCode };
  }
}
