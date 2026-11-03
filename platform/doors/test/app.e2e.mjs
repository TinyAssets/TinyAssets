// The owner app in a real browser (Chromium, through Playwright), against the doors in
// workerd: the doors' test Worker (`e2e-entry.ts`: the fake upstream sign-in, the real
// owner object with a recording sandbox) with the real gateway and hooks Workers and the
// fake providers, each bundled by wrangler from its own config as `vitest.config.ts` does.
//
// The browser believes it talks to https://tinyassets.io: every request it makes is caught
// (`context.route`) and handed to the doors with `dispatchFetch`, the app's live socket
// likewise (`routeWebSocket`). Two other hosts answer, each a top-level navigation by
// design: the identity provider (`upstream.test`, which signs the owner in at once) and the
// test OAuth source's authorization server (`auth.test`, which approves). Any other request
// is refused and fails the test: the app makes no third-party request.
//
// Run: `npm run test:app` in platform/doors. Playwright and Chromium are the host's own
// (PLAYWRIGHT_BROWSERS_PATH); nothing is installed.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import ts from "typescript";
import { unstable_getMiniflareWorkerOptions } from "wrangler";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
const PLATFORM = join(DOORS, "..");
const WRANGLER = join(PLATFORM, "node_modules", "wrangler", "bin", "wrangler.js");
const ORIGIN = "https://tinyassets.io";
const EGRESS = "https://egress.tinyassets.io";
const UPSTREAM = "https://upstream.test";
const SOURCE_AUTH = "https://auth.test";
const WHO = `user_${randomBytes(16).toString("hex")}`;
const TIERS = { free: { runs: 5, storage_bytes: 25_000_000_000 },
                paid: { runs: 10, storage_bytes: 250_000_000_000 } };
const OPEN = { outside: "on", ordinary: "all", canary: [], inbound: false, tiers: TIERS };

/** Playwright: this package's, or the host's global one (never installed here). */
function playwright() {
  try {
    return createRequire(import.meta.url)("playwright");
  } catch {
    const root = execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
    return createRequire(join(root, "noop.js"))("playwright");
  }
}

function config(dir) {
  return ts.parseConfigFileTextToJson("wrangler.jsonc",
                                      readFileSync(join(dir, "wrangler.jsonc"), "utf8")).config;
}

/** A Worker bundled by wrangler from its own config, as a deploy bundles it. */
function bundled(dir, entry) {
  const out = mkdtempSync(join(tmpdir(), "app-e2e-"));
  try {
    execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir", out,
                                    "--config", join(dir, "wrangler.jsonc"),
                                    ...(entry === undefined ? [] : [entry])],
                 { cwd: dir, stdio: "pipe", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    const main = entry === undefined ? "index.js" : `${entry.split("/").at(-1).replace(/\.ts$/, "")}.js`;
    return readdirSync(out).filter((file) => /\.(?:js|wasm)$/.test(file))
      .sort((a, b) => Number(b === main) - Number(a === main) || a.localeCompare(b))
      .map((file) => ({
        type: file.endsWith(".wasm") ? "CompiledWasm" : "ESModule",
        path: join(dir, file), contents: readFileSync(join(out, file)),
      }));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/**
 * The hand-off hosts' Worker: the identity provider signs `WHO` in at once, its code the
 * fake upstream's (`fake-upstream.ts`'s `fakeCode`), and the test source's authorization
 * server approves (`fake-providers.ts` takes `good-code`); each sends the browser back.
 */
const HAND_OFF = `
export default {
  fetch(request, env) {
    const url = new URL(request.url);
    const back = new URL(url.searchParams.get("redirect_uri") ?? "https://tinyassets.io/");
    const state = url.searchParams.get("state") ?? "";
    if (url.hostname === "upstream.test" && url.pathname === "/authorize") {
      const signIn = JSON.stringify({ subject: env.WHO,
        authenticated_at: Math.floor(Date.now() / 1000) - 5,
        challenge: url.searchParams.get("code_challenge") });
      const code = btoa(signIn).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
      back.search = new URLSearchParams({ code, state }).toString();
    } else if (url.hostname === "auth.test" && url.pathname === "/authorize") {
      back.search = new URLSearchParams({ code: "good-code", state,
                                          iss: "https://auth.test" }).toString();
    } else {
      return new Response("not found", { status: 404 });
    }
    return Response.redirect(back.href, 302);
  },
};
`;

let mf;
let browser;
let context;
let page;
const outside = [];
const seen = [];
const errors = [];

async function doors() {
  const gateway = config(join(PLATFORM, "gateway"));
  const hooks = config(join(PLATFORM, "hooks"));
  const { workerOptions } = unstable_getMiniflareWorkerOptions(join(DOORS, "wrangler.jsonc"));
  const providers = readFileSync(join(DOORS, "test", "fake-providers.ts"), "utf8");
  const keyring = JSON.stringify({ active: "k1", keys: { k1: randomBytes(32).toString("base64") } });
  const webClient = `${ORIGIN}/oauth/clients/web.json`;
  mf = new Miniflare(convertV4MiniflareOptions({
    host: "127.0.0.1", port: 0, https: true,
    workers: [
      {
        ...workerOptions,
        name: "tinyassets-doors",
        modulesRoot: DOORS,
        modules: bundled(DOORS, "test/e2e-entry.ts"),
        bindings: { ...workerOptions.bindings, OWNER_ISSUER: UPSTREAM,
                    ROUTING_MAC_KEY: randomBytes(32).toString("base64url") },
        durableObjects: { OWNER: { className: "OwnerDO", useSQLite: true } },
        serviceBindings: {
          GATEWAY: { name: gateway.name, entrypoint: "ConnectGateway" },
          EGRESS: { name: gateway.name, entrypoint: "EgressGateway" },
          HOOKS: hooks.name, PROVIDERS: "fake-providers",
        },
        // The provider fetches the first-party app's metadata document, as production does
        // over the public internet; nothing else leaves the doors.
        outboundService: (request) => request.url === webClient ? mf.dispatchFetch(webClient)
          : new Response(`the doors reach no ${request.url} here`, { status: 502 }),
      },
      {
        name: gateway.name, modulesRoot: join(PLATFORM, "gateway"),
        modules: bundled(join(PLATFORM, "gateway")),
        compatibilityDate: gateway.compatibility_date,
        compatibilityFlags: gateway.compatibility_flags,
        bindings: { GATEWAY_KEYRING: keyring }, outboundService: "fake-providers",
      },
      {
        name: hooks.name, modulesRoot: join(PLATFORM, "hooks"),
        modules: bundled(join(PLATFORM, "hooks")),
        compatibilityDate: hooks.compatibility_date,
        compatibilityFlags: hooks.compatibility_flags,
        durableObjects: Object.fromEntries((hooks.durable_objects?.bindings ?? [])
          .map((b) => [b.name, { className: b.class_name, useSQLite: true }])),
      },
      {
        name: "fake-providers", modulesRoot: DOORS,
        modules: [{ type: "ESModule", path: join(DOORS, "test", "fake-providers.js"),
                    contents: ts.transpileModule(providers, { compilerOptions: {
                      module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText }],
        compatibilityDate: gateway.compatibility_date,
      },
      {
        // The two hand-off hosts, each answering the browser's top-level navigation at once.
        name: "hand-off", routes: ["upstream.test/*", "auth.test/*"],
        bindings: { WHO }, compatibilityDate: gateway.compatibility_date,
        modules: true, script: HAND_OFF,
      },
    ],
  }));
  await mf.ready;
  const control = await mf.getKVNamespace("CONTROL", "tinyassets-doors");
  await control.put("c:control", JSON.stringify(OPEN));
}

before(async () => {
  await doors();
  const port = new URL(await mf.ready).port;
  // No proxy: the browser reaches only the local workerd, by the rules below.
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([name]) => !/proxy/i.test(name)));
  const local = ["tinyassets.io", "egress.tinyassets.io", "upstream.test", "auth.test"]
    .map((host) => `MAP ${host}:443 127.0.0.1:${port}`).join(", ");
  browser = await playwright().chromium.launch({ env, args: [
    "--no-proxy-server",
    // Our hosts and the two hand-off hosts to the local workerd; nothing else resolves.
    `--host-resolver-rules=${local}, MAP * ~NOTFOUND`,
  ] });
  // workerd's own certificate stands in for the public one.
  context = await browser.newContext({ serviceWorkers: "block", ignoreHTTPSErrors: true });
  context.on("request", (request) => seen.push({
    url: request.url(), navigation: request.isNavigationRequest(), type: request.resourceType(),
    referer: request.headers()["referer"] ?? null }));
  context.on("requestfailed", (request) => {
    if (!request.url().startsWith(`${ORIGIN}/`)) {
      outside.push(request.url());
    }
  });
  // The tests' own instrument: each `history.replaceState`, with what the page held then.
  await context.addInitScript(() => {
    const replace = history.replaceState.bind(history);
    window.__replaced = [];
    history.replaceState = (...args) => {
      window.__replaced.push({ search: location.search, hash: location.hash,
                               scripts: [...document.scripts].map((s) => s.src) });
      return replace(...args);
    };
  });
  page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    // A refused request is the doors' answer, asserted where it matters; any other console
    // error is the app's.
    if (message.type() === "error" && !/^Failed to load resource/.test(message.text())) {
      errors.push(message.text());
    }
  });
  if (process.env["APP_E2E_DEBUG"]) {
    page.on("requestfailed", (request) => console.error("FAILED", request.url(),
                                                        request.failure()?.errorText));
    page.on("response", (response) => console.error(response.request().method(), response.url(),
                                                    response.status()));
  }
}, { timeout: 300_000 });

/** A screenshot of the page as it stands, when `APP_E2E_SHOTS` names a folder. */
async function shot(name) {
  const dir = process.env["APP_E2E_SHOTS"];
  if (dir) {
    await page.screenshot({ path: join(dir, `${name}.png`), fullPage: true });
  }
}
after(async () => {
  await browser?.close();
  await mf?.dispose();
});

/** The starts the owner's object asked of its recording sandbox. */
async function starts() {
  return (await mf.dispatchFetch(`https://e2e.internal/starts/${WHO}`)).json();
}

/** The runner's post of its run's events, as the sandbox would send it. */
async function report(start, body) {
  const answer = await mf.dispatchFetch(`${EGRESS}/r/${start.run}/events`, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "x-ta-sandbox": start.tokens.egress_token,
               "x-ta-network-generation": "1", "x-ta-runner": start.tokens.runner_token } });
  assert.equal(answer.status, 200, await answer.clone().text());
  return answer.json();
}

test("the owner signs in: the code leaves the address bar first, and a session is kept",
     { timeout: 120_000 }, async () => {
  await page.goto(`${ORIGIN}/app`);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${ORIGIN}/app`);
  await page.getByRole("heading", { name: "Home" }).waitFor();
  assert.match(await page.locator("main").innerText(), /Your plan: Free[\s\S]*Runs at once: 5\./);
  const cookies = await context.cookies(ORIGIN);
  const session = cookies.find((c) => c.name === "__Host-ta-owner");
  assert.ok(session?.httpOnly && session.secure && session.sameSite === "Strict");
  // Nothing of the sign-in's code is left in this tab's history.
  assert.equal(await page.evaluate(() => location.href), `${ORIGIN}/app`);
});

test("the sign-in return page cleared its code before any other script ran", async () => {
  const signedIn = seen.find((r) => r.url.startsWith(`${ORIGIN}/app/signed-in?`));
  assert.ok(signedIn, "the browser came back to /app/signed-in");
  // The instrument lives per document: read the signed-in page's record from a fresh visit.
  const probe = await context.newPage();
  await probe.goto(`${ORIGIN}/app/signed-in?code=probe-code&state=probe-state`);
  const replaced = await probe.evaluate(() => window.__replaced);
  assert.deepEqual(replaced[0], { search: "?code=probe-code&state=probe-state", hash: "",
                                  scripts: [`${ORIGIN}/app/static/signed-in.js`] });
  assert.equal(await probe.evaluate(() => location.href), `${ORIGIN}/app/signed-in`);
  await probe.close();
});

test("the owner connects a model source with a key", { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#sources`);
  const card = page.locator(".card", { hasText: "Groq" });
  await card.locator("input[type=password]").fill(`gsk-good-${randomBytes(8).toString("hex")}`);
  await card.getByRole("button", { name: "Connect with a key" }).click();
  await page.locator(".message.good", { hasText: "Connected Groq" }).waitFor();
  await page.locator("table", { hasText: "Groq" }).waitFor();
});

test("the connect page clears the fragment before any other script runs, sends no " +
     "referrer, and finishes the connect", { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#sources`);
  await page.locator(".card", { hasText: "Test OAuth" })
    .getByRole("button", { name: "Sign in to Test OAuth" }).click();
  await page.waitForURL(`${ORIGIN}/app/connected`);
  await page.locator("#status", { hasText: "Connected Test OAuth." }).waitFor();
  const replaced = await page.evaluate(() => window.__replaced);
  assert.equal(replaced.length, 1);
  assert.deepEqual(replaced[0].scripts, [`${ORIGIN}/app/static/connected.js`]);
  assert.match(replaced[0].hash, /^#state=[^&]+&code=good-code&redirect_uri=/);
  assert.equal(await page.evaluate(() => location.href), `${ORIGIN}/app/connected`);
  const finish = seen.filter((r) => r.url === `${ORIGIN}/app/api/connections/oauth/finish`);
  assert.equal(finish.length, 1);
  assert.ok(!finish[0].referer, `the finish carried a referrer: ${finish[0].referer}`);
  await page.goto(`${ORIGIN}/app#sources`);
  await page.locator("table", { hasText: "Test OAuth" }).waitFor();
});

test("the owner makes an agent, starts a run and sees its live frames and its result",
     { timeout: 120_000 }, async () => {
  await page.goto(`${ORIGIN}/app#agents`);
  await page.getByRole("heading", { name: "New agent" }).waitFor();
  const form = page.locator("section", { hasText: "New agent" });
  await form.getByLabel(/^Name/).fill("main");
  await form.getByLabel(/^Engine/).selectOption("pi");
  await form.getByLabel(/^Runs on/).selectOption("api_key");
  const connection = form.getByLabel(/^API key connection/);
  const groq = await connection.locator("option", { hasText: "groq" }).getAttribute("value");
  await connection.selectOption(groq);
  await form.getByRole("button", { name: "Make the agent" }).click();
  await page.waitForURL(`${ORIGIN}/app#agents/main`);
  await page.getByRole("link", { name: "agent.json" }).waitFor();
  await page.getByRole("link", { name: "AGENTS.md" }).waitFor();

  await page.getByPlaceholder("Say something to main").fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await page.locator(".said.me", { hasText: "hello" }).waitFor();
  let start;
  for (let i = 0; i < 100 && start === undefined; i++) {
    [start] = await starts();
    if (start === undefined) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  assert.ok(start, "the run asked the sandbox to start");
  assert.equal(start.engine, "pi");
  assert.deepEqual(await report(start, { frames: [
    { seq: 1, at: 1, event: { type: "session", id: "s-e2e" } },
    { seq: 2, at: 2, event: { type: "tool_execution_start", tool: "read" } }] }),
                   { accepted: 2, settled: null });
  await page.locator(".frame", { hasText: "s-e2e" }).waitFor();
  assert.deepEqual(await page.locator(".frame b").allInnerTexts(), ["init", "tool_call"]);
  await report(start, { frames: [
    { seq: 3, at: 3, event: { type: "message_end",
                              message: { role: "assistant", content: "Hello from pi" } } }],
                        end: { exit_code: 0 } });
  await page.locator(".said.agent", { hasText: "Hello from pi" }).waitFor();
  assert.match(await page.locator("main").innerText(), /Run succeeded on pi/);
  await shot("agent");
});

test("every screen opens on the doors' answers, and says plainly what is not here yet",
     { timeout: 60_000 }, async () => {
  for (const [hash, heading] of [["home", "Home"], ["agents", "Agents"],
                                 ["rooms", "Rooms and board"], ["sources", "Model sources"],
                                 ["apps", "Connected apps"], ["keys", "API keys"],
                                 ["data", "Your data"]]) {
    await page.goto(`${ORIGIN}/app#${hash}`);
    await page.getByRole("heading", { name: heading, exact: true }).waitFor();
    await page.locator("main p.muted", { hasText: "Loading" }).waitFor({ state: "detached" });
    assert.equal(await page.locator("#notice .message.bad").count(), 0, hash);
    await shot(hash);
  }
  // The owner's mail to its own agent, posted in their room.
  await page.goto(`${ORIGIN}/app#rooms`);
  await page.getByLabel(/^Message/).fill("good morning");
  await page.getByRole("button", { name: "Post" }).click();
  await page.locator(".message.good", { hasText: "Posted." }).waitFor();
  // A key, shown once.
  await page.goto(`${ORIGIN}/app#keys`);
  await page.getByLabel(/^Name/).fill("laptop");
  await page.getByRole("button", { name: "Make the key" }).click();
  assert.match(await page.locator(".secret").innerText(), /^ta_key_[0-9a-f]{16}_/);
  // Code mode off, in the owner's session.
  await page.goto(`${ORIGIN}/app#apps`);
  const codemode = page.locator("section", { hasText: "Code mode" });
  await codemode.getByRole("checkbox").uncheck();
  await codemode.getByRole("button", { name: "Save" }).click();
  await page.locator(".message.good", { hasText: "Saved." }).waitFor();
  assert.equal(await page.locator("section", { hasText: "Code mode" }).getByRole("checkbox")
    .isChecked(), false);
  // No main agent has joined, so no wake-up is offered; the export needs slice P's door:
  // said, never faked.
  await page.goto(`${ORIGIN}/app#apps`);
  await page.getByText("No main agent has joined yet.").waitFor();
  await page.goto(`${ORIGIN}/app#data`);
  await page.getByRole("button", { name: "Export everything" }).click();
  await page.locator(".message.bad", { hasText: "not here yet" }).waitFor();
});

test("the app made no third-party request and threw nothing", () => {
  assert.deepEqual(errors, []);
  assert.deepEqual(outside, []);
  const elsewhere = seen.filter((r) => !r.url.startsWith(`${ORIGIN}/`));
  // Only the two hand-offs the owner is sent to, each a top-level navigation.
  for (const request of elsewhere) {
    assert.ok(request.navigation && request.type === "document", request.url);
    assert.ok(request.url.startsWith(`${UPSTREAM}/authorize?`)
              || request.url.startsWith(`${SOURCE_AUTH}/authorize?`), request.url);
  }
  assert.ok(seen.some((r) => r.url.startsWith(`${ORIGIN}/app/static/app.js`)));
});
