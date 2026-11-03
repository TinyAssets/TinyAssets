/**
 * A fake E2B, in tests only: the platform API (`api.e2b.app`) and envd (`sandbox.e2b.app`)
 * as `sandbox/src/e2b.ts` speaks them, in the field names of E2B's OpenAPI document, with
 * sandboxes held in memory. The tests script it and read back what it received at
 * `https://fake.test/*` (through the `E2B` service binding). It is also the ring queue's
 * consumer in tests, recording each ring.
 *
 * The runner's commands (`/usr/local/bin/ta-runner <command>`) answer as scripted: a
 * sign-in check by engine, the runner's state (for `state`, `quiet-wait` and `renew-wait`),
 * and every other command 0.
 */
interface FakeSandbox {
  id: string;
  token: string;
  state: "running" | "paused";
  startedAt: number;
  timeout: number;
  pauseMode: "full" | "filesystem" | null;
  network: unknown;
  metadata: Record<string, string>;
  template: string;
  secure: boolean;
}

interface Script {
  signin: Record<string, number>;
  state: { live: number; quiet: boolean };
  /** Other projects' running sandboxes E2B counts beside these. */
  others: number;
  /** How many pauses answer busy (503) before one goes. */
  busy: number;
  /** A create or resume answered 429 (E2B's concurrency limit). */
  full: boolean;
  /** What the next pause saves. */
  pauseMode: "full" | "filesystem";
  /** The list answers with no `X-Total-Running`. */
  noCount: boolean;
  /** How many info calls answer 500 (E2B unreachable) before they answer again. */
  down: number;
  /** A resume of a paused sandbox answered 429, creates still taken. */
  refuseResume: boolean;
  /** A runner command whose process start envd answers 502 (the sandbox went away). */
  broken: string | null;
}

const fake = {
  sandboxes: new Map<string, FakeSandbox>(),
  calls: [] as { method: string; path: string; body: unknown; query: string }[],
  files: new Map<string, string>(),
  commands: [] as { sandbox: string; argv: string[]; user: string }[],
  rings: [] as unknown[],
  script: null as unknown as Script,
  next: 0,
};

function reset(): void {
  fake.sandboxes.clear();
  fake.calls = [];
  fake.files.clear();
  fake.commands = [];
  fake.rings = [];
  fake.next = 0;
  fake.script = { signin: {}, state: { live: 1, quiet: false }, others: 0, busy: 0,
                  full: false, pauseMode: "full", noCount: false, down: 0,
                  refuseResume: false, broken: null };
}
reset();

function info(box: FakeSandbox): unknown {
  return {
    templateID: box.template, sandboxID: box.id, clientID: "x", envdVersion: "0.10.0",
    ...(box.secure ? { envdAccessToken: box.token } : {}),
    startedAt: new Date(box.startedAt).toISOString(),
    endAt: new Date(box.startedAt + box.timeout * 1000).toISOString(), cpuCount: 2,
    memoryMB: 1024, diskSizeMB: 20000, state: box.state, metadata: box.metadata,
    network: box.network, lifecycle: { autoResume: false, onTimeout: "pause" },
    ...(box.state === "paused" ? { pauseMode: box.pauseMode } : {}),
  };
}

function running(): number {
  return [...fake.sandboxes.values()].filter((box) => box.state === "running").length
    + fake.script.others;
}

function envelope(message: unknown, flags = 0): Uint8Array {
  const body = new TextEncoder().encode(JSON.stringify(message));
  const out = new Uint8Array(5 + body.length);
  out[0] = flags;
  new DataView(out.buffer).setUint32(1, body.length);
  out.set(body, 5);
  return out;
}

function b64(text: string): string {
  return btoa(String.fromCharCode(...new TextEncoder().encode(text)));
}

function runner(argv: string[]): { code: number; out: string } {
  const [, command, arg] = argv;
  if (command === "signin-check") {
    return { code: fake.script.signin[arg ?? ""] ?? 3, out: "" };
  }
  if (command === "state" || command === "quiet-wait") {
    return { code: 0, out: JSON.stringify(fake.script.state) };
  }
  if (command === "renew-wait") {
    return { code: 0, out: JSON.stringify({ live: fake.script.state.live }) };
  }
  return { code: 0, out: "" };
}

function connected(box: FakeSandbox, status = 200): Response {
  return Response.json({ sandboxID: box.id, envdAccessToken: box.token, templateID: "t",
                         clientID: "x", envdVersion: "0.10.0" }, { status });
}

async function api(request: Request, url: URL): Promise<Response> {
  if (request.headers.get("X-API-Key") !== "e2b_test_key_0000") {
    return Response.json({ code: 401, message: "bad key" }, { status: 401 });
  }
  const text = request.method === "GET" ? "" : await request.text();
  const body: unknown = text === "" ? null : JSON.parse(text);
  fake.calls.push({ method: request.method, path: url.pathname, body, query: url.search });
  const [, first, second, third, fourth] = url.pathname.split("/");
  if (request.method === "POST" && first === "v2" && second === "sandboxes" && !third) {
    if (fake.script.full) {
      return Response.json({ code: 429, message: "concurrency" }, { status: 429 });
    }
    const b = body as { templateID: string; timeout: number; network: unknown; secure: boolean;
                        metadata: Record<string, string> };
    const id = `sbx${String(fake.next += 1).padStart(5, "0")}`;
    const box: FakeSandbox = { id, token: `envd-${id}`, state: "running", startedAt: Date.now(),
                               timeout: b.timeout, pauseMode: null, network: b.network,
                               metadata: b.metadata, template: b.templateID,
                               secure: b.secure === true };
    fake.sandboxes.set(id, box);
    return Response.json({ templateID: b.templateID, sandboxID: id, clientID: "x",
                           envdVersion: "0.10.0",
                           ...(box.secure ? { envdAccessToken: box.token } : {}) },
                         { status: 201 });
  }
  if (request.method === "GET" && first === "v2" && second === "sandboxes" && !third) {
    const states = (url.searchParams.get("state") ?? "running,paused").split(",");
    const [key, value] = (url.searchParams.get("metadata") ?? "=").split("=")
      .map(decodeURIComponent);
    const listed = [...fake.sandboxes.values()].filter((box) => states.includes(box.state)
      && (key === "" || box.metadata[key!] === value)).map(info);
    return Response.json(listed, { headers: fake.script.noCount ? {}
      : { "X-Total-Running": String(running()) } });
  }
  const id = first === "v2" ? third! : second!;
  const action = first === "v2" ? fourth : third;
  const box = fake.sandboxes.get(id);
  if (box === undefined) {
    return Response.json({ code: 404, message: "no sandbox" }, { status: 404 });
  }
  if (request.method === "GET" && action === undefined) {
    if (fake.script.down > 0) {
      fake.script.down -= 1;
      return Response.json({ code: 500, message: "down" }, { status: 500 });
    }
    return Response.json(info(box));
  }
  if (request.method === "DELETE") {
    fake.sandboxes.delete(id);
    return new Response(null, { status: 204 });
  }
  if (action === "pause") {
    if (fake.script.busy > 0) {
      fake.script.busy -= 1;
      return Response.json({ code: 503, message: "busy" }, { status: 503 });
    }
    if (box.state === "paused") {
      return Response.json({ code: 409, message: "paused" }, { status: 409 });
    }
    box.state = "paused";
    box.pauseMode = fake.script.pauseMode;
    return new Response(null, { status: 204 });
  }
  if (action === "connect") {
    if (box.state === "running") {
      return connected(box);
    }
    if (fake.script.full || fake.script.refuseResume) {
      return Response.json({ code: 429, message: "concurrency" }, { status: 429 });
    }
    box.state = "running";
    box.startedAt = Date.now();
    box.timeout = (body as { timeout: number }).timeout;
    return connected(box, 201);
  }
  if (action === "network" && request.method === "PUT") {
    box.network = body;
    return new Response(null, { status: 204 });
  }
  return Response.json({ code: 404, message: "no route" }, { status: 404 });
}

async function envd(request: Request, url: URL): Promise<Response> {
  const id = request.headers.get("E2b-Sandbox-Id") ?? "";
  const box = fake.sandboxes.get(id);
  if (box === undefined || (box.secure && request.headers.get("X-Access-Token") !== box.token)
      || request.headers.get("E2b-Sandbox-Port") !== "49983") {
    return new Response("unauthorized", { status: 401 });
  }
  if (box.state !== "running") {
    return new Response("paused", { status: 502 });
  }
  const user = atob((request.headers.get("Authorization") ?? "Basic ").slice(6)).split(":")[0]!;
  if (url.pathname === "/files") {
    fake.files.set(`${id}:${url.searchParams.get("path")}`,
                   new TextDecoder().decode(await request.arrayBuffer()));
    return Response.json([{ name: "x", type: "file", path: url.searchParams.get("path") }]);
  }
  if (url.pathname === "/process.Process/Start") {
    const bytes = new Uint8Array(await request.arrayBuffer());
    const length = new DataView(bytes.buffer).getUint32(1);
    const start = JSON.parse(new TextDecoder().decode(bytes.subarray(5, 5 + length))) as {
      process: { cmd: string; args: string[] } };
    const argv = [start.process.cmd, ...start.process.args];
    fake.commands.push({ sandbox: id, argv, user });
    if (argv[1] !== undefined && argv[1] === fake.script.broken) {
      return new Response("envd went away", { status: 502 });
    }
    const done = runner(argv);
    const parts = [envelope({ event: { start: { pid: 42 } } }),
                   ...(done.out === "" ? [] : [envelope({ event: { data: { stdout: b64(done.out) } } })]),
                   envelope({ event: { end: { exitCode: done.code, exited: true,
                                               status: `exit status ${done.code}` } } }),
                   envelope({}, 2)];
    return new Response(new Blob(parts), { headers: { "content-type": "application/connect+json" } });
  }
  return new Response("no route", { status: 404 });
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.host === "api.e2b.app") {
      return api(request, url);
    }
    if (url.host === "sandbox.e2b.app") {
      return envd(request, url);
    }
    if (url.host === "fake.test") {
      if (url.pathname === "/reset") {
        reset();
        return Response.json({});
      }
      if (url.pathname === "/script") {
        Object.assign(fake.script, await request.json());
        return Response.json(fake.script);
      }
      if (url.pathname === "/age") {
        // A sandbox's running window moved back by `s` seconds: time passing in a test.
        const { id, s } = await request.json() as { id: string; s: number };
        fake.sandboxes.get(id)!.startedAt -= s * 1000;
        return Response.json({});
      }
      if (url.pathname === "/pause") {
        // E2B's own timeout pausing it, unseen by the Worker.
        const { id } = await request.json() as { id: string };
        fake.sandboxes.get(id)!.state = "paused";
        fake.sandboxes.get(id)!.pauseMode = "full";
        return Response.json({});
      }
      if (url.pathname === "/stray") {
        // A sandbox E2B holds under a tag whose row the Worker lost.
        const { metadata } = await request.json() as { metadata: Record<string, string> };
        const id = `sbx${String(fake.next += 1).padStart(5, "0")}`;
        fake.sandboxes.set(id, { id, token: `envd-${id}`, state: "paused", startedAt: Date.now(),
                                 timeout: 60, pauseMode: "full", network: null, metadata,
                                 template: "t", secure: true });
        return Response.json({ id });
      }
      return Response.json({ sandboxes: [...fake.sandboxes.values()], calls: fake.calls,
                             files: Object.fromEntries(fake.files), commands: fake.commands,
                             rings: fake.rings });
    }
    return new Response(`the sandbox Worker reached ${url.host}, which is not E2B`,
                        { status: 599 });
  },
  /** The ring queue's consumer, in tests: each ring recorded. */
  async queue(batch: MessageBatch<unknown>): Promise<void> {
    for (const message of batch.messages) {
      fake.rings.push(message.body);
      message.ack();
    }
  },
};
