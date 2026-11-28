// The owner app in a real browser (Chromium, through Playwright), against the doors in
// workerd: the doors' harness Worker (`e2e-entry.ts`: the live sign-in against the fake AuthKit
// of `fake-providers.ts`, the test OAuth source beside the bundled records) with the real
// owner, gateway, codemode and files Workers and the fake providers, each bundled by wrangler
// from its own config as `vitest.config.ts` does (`ci/workers.ts`), and the owner's sandbox
// the owner suite's fake sandbox Worker. Each Worker is bound as production binds it: the
// doors' OWNER the owner Worker's objects, the doors' FILES the files Worker's upload
// entrypoint and the owner's its whole service, so a new owner's starter seed really writes
// its files.
//
// The browser believes it talks to the doors' origin: its resolver sends our hosts to the
// local workerd (`--host-resolver-rules`), so every request it makes, the app's live socket
// too, reaches the doors over the network. Two other hosts answer, each a top-level navigation by
// design: the identity provider (`upstream.test`, which signs the owner in at once) and the
// test OAuth source's authorization server (`auth.test`, which approves). Any other request
// is refused and fails the test: the app makes no third-party request.
//
// Run: `npm run test:app` in platform/doors. Playwright is the version the platform lockfile
// pins, and its Chromium must be installed (`npx playwright install chromium`, or the host's
// PLAYWRIGHT_BROWSERS_PATH): with either missing the run fails, never skips.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { chromium } from "playwright";

import { installed } from "../../ci/deps.ts";
import { keyring as newKeyring, OPEN, writeControl } from "../../ci/fixtures.ts";
import { PLATFORM } from "../../ci/paths.ts";
import { auxiliary, fake, pool, workerConfig } from "../../ci/workers.ts";
import { EGRESS_HOST, EGRESS_ORIGIN } from "../../contract/src/egress.ts";
import { CANARY_ONLY, MAINTENANCE } from "../../contract/src/control.ts";
import { HOST, ORIGIN, waitingPage } from "../../contract/src/identity.ts";
import { ERASED_SIGN_IN, STALE_SIGN_IN } from "../../contract/src/signin.ts";
import { AUTHKIT } from "./fake-providers.ts";

installed();
const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
const UPSTREAM = AUTHKIT.OWNER_ISSUER;
const SOURCE_AUTH = "https://auth.test";
/** The hosts the doors' sign-in reaches, the fake AuthKit's: its record's token and JWKS. */
const { token_endpoint: TOKEN_AT, jwks_uri: JWKS_AT } = JSON.parse(AUTHKIT.UPSTREAM);
const AUTHKIT_HOSTS = [TOKEN_AT, JWKS_AT].map((at) => new URL(at).hostname);
const WHO = `user_${randomBytes(16).toString("hex")}`;
/**
 * The hand-off hosts' Worker: the identity provider signs `WHO` in at once, its code the
 * fake AuthKit's (`fake-providers.ts`'s `signInCode`), and the test source's authorization
 * server approves (`fake-providers.ts` takes `good-code`); each sends the browser back.
 */
const HAND_OFF = `
export default {
  fetch(request, env) {
    const url = new URL(request.url);
    const back = new URL(url.searchParams.get("redirect_uri") ?? "${ORIGIN}/");
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
  const gateway = workerConfig(join(PLATFORM, "gateway"));
  const providers = fake(join(DOORS, "test", "fake-providers.ts"));
  const sandbox = fake(join(PLATFORM, "ci", "fake-sandbox.ts"));
  const keyring = newKeyring();
  const routing = newKeyring();
  const webClient = `${ORIGIN}/oauth/clients/web.json`;
  mf = new Miniflare(convertV4MiniflareOptions(pool({
    host: "127.0.0.1", port: 0, https: true,
    workers: [
      // The doors' harness Worker, with everything the doors' config gives it (`auxiliary`):
      // its secrets are the test's, none read from the host.
      await auxiliary(DOORS, {
        bindings: { ...AUTHKIT,
                    ROUTING_KEYRING: routing, WORKOS_API_KEY: "test-client-secret",
                    E2B_WEBHOOK_SECRET: "test-e2b-webhook-signature-secret",
                    OPERATOR_KEYRING: newKeyring() },
        // SANDBOX_LOG reads back the owner's fake sandbox Worker.
        serviceBindings: { PROVIDERS: providers.name,
                           SANDBOX_LOG: { name: sandbox.name, entrypoint: "Log" } },
        // The provider fetches the first-party app's metadata document, as production does
        // over the public internet, and the sign-in reaches the fake AuthKit; nothing else
        // leaves the doors.
        outboundService: async (request) => request.url === webClient
          ? mf.dispatchFetch(webClient)
          : AUTHKIT_HOSTS.includes(new URL(request.url).hostname)
            ? (await mf.getWorker(providers.name)).fetch(request)
            : new Response(`the doors reach no ${request.url} here`, { status: 502 }),
      }, "test/e2e-entry.ts"),
      // The owner Worker, its SANDBOX the owner suite's fake sandbox Worker.
      await auxiliary(join(PLATFORM, "owner"), { bindings: { ROUTING_KEYRING: routing },
                                                 serviceBindings: { SANDBOX: sandbox.name } }),
      await auxiliary(join(PLATFORM, "gateway"), { bindings: { GATEWAY_KEYRING: keyring },
                                                   outboundService: providers.name }),
      // The files Worker: its config's bucket and cipher, and a test keyring.
      await auxiliary(join(PLATFORM, "files"), { bindings: { FILES_KEYRING: keyring } }),
      await auxiliary(join(PLATFORM, "codemode"), {}),
      providers,
      sandbox,
      {
        // The two hand-off hosts, each answering the browser's top-level navigation at once.
        name: "hand-off", routes: ["upstream.test/*", "auth.test/*"],
        bindings: { WHO }, compatibilityDate: gateway.compatibility_date,
        modules: true, script: HAND_OFF,
      },
    ],
  })));
  await mf.ready;
  const control = await mf.getKVNamespace("CONTROL", workerConfig(DOORS).name);
  await writeControl(control, JSON.stringify(OPEN));
}

before(async () => {
  await doors();
  const port = new URL(await mf.ready).port;
  // No proxy: the browser reaches only the local workerd, by the rules below.
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([name]) => !/proxy/i.test(name)));
  const local = [HOST, EGRESS_HOST, "upstream.test", "auth.test"]
    .map((host) => `MAP ${host}:443 127.0.0.1:${port}`).join(", ");
  browser = await chromium.launch({ env, args: [
    "--no-proxy-server",
    // workerd's self-signed certificate, accepted by the browser's network stack itself:
    // the context's `ignoreHTTPSErrors` alone left each WebSocket's first TLS handshake
    // refused (`CertificateUnknown`), and now and then the socket itself died before it
    // opened (1006), so the sign-in panel never saw its end (the merge queue's red run).
    "--ignore-certificate-errors",
    // The attack tests' own pages are public origins, and our hosts resolve to this machine:
    // the browser's local-network checks would refuse their frames and requests before the
    // doors' own answer (frame-ancestors, the session's checks) is ever read, as no public
    // page meets the public app.
    "--disable-features=LocalNetworkAccessChecks",
    // Our hosts and the two hand-off hosts to the local workerd; nothing else resolves.
    `--host-resolver-rules=${local}, MAP * ~NOTFOUND`,
  ] });
  // workerd's own certificate stands in for the public one.
  context = await browser.newContext({ serviceWorkers: "block", ignoreHTTPSErrors: true });
  context.on("request", (request) => seen.push({
    url: request.url(), navigation: request.isNavigationRequest(), type: request.resourceType(),
    referer: request.headers()["referer"] ?? null, main: request.frame().page() === page }));
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
    // Every element made with an inline handler (`on…`) or as inline script, as it is added
    // or given one: what a payload would have to become to run.
    window.__markup = [];
    const made = (el) => {
      if (el.nodeType !== Node.ELEMENT_NODE) {
        return;
      }
      for (const each of [el, ...el.querySelectorAll("*")]) {
        if ((each.tagName === "SCRIPT" && !each.src)
            || [...each.attributes].some((attr) => attr.name.startsWith("on"))) {
          window.__markup.push(each.outerHTML.slice(0, 200));
        }
      }
    };
    new MutationObserver((records) => {
      for (const record of records) {
        record.addedNodes.forEach(made);
        if (record.type === "attributes" && record.attributeName.startsWith("on")) {
          made(record.target);
        }
      }
    }).observe(document, { childList: true, subtree: true, attributes: true });
    // Each JSON answer the page parsed, by URL, recorded a task after the code that awaited
    // it had its turn (which runs in the parse's own microtasks).
    window.__parsed = [];
    const json = Response.prototype.json;
    Response.prototype.json = function parsed() {
      const url = this.url;
      return json.call(this).then((value) => {
        setTimeout(() => window.__parsed.push(url), 0);
        return value;
      });
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

const LEASE_TOKENS = new Map();

/** What E2B's transform sets on every call through the door: the egress token and generation. */
function stampedOf(opened) {
  return opened.network.network.rules[EGRESS_HOST][0].transform.headers;
}

/** The lease's egress token: only the network's transform carries it, as E2B sets it. */
function egressOf(opened) {
  return stampedOf(opened)["x-ta-sandbox"];
}

/** The starts the owner's object asked of its recording sandbox. */
async function starts() {
  const all = await (await mf.dispatchFetch(`https://e2e.internal/starts/${WHO}`)).json();
  // A lease's tokens come only with the start that opened it; later runs on it reuse them.
  for (const start of all) {
    if (start.tokens !== null) {
      LEASE_TOKENS.set(start.lease.lease_id, { ...start.tokens, egress_token: egressOf(start) });
    }
  }
  return all;
}

/** The runner's post of its run's events, as the sandbox would send it. */
async function report(start, body) {
  await starts();
  const tokens = LEASE_TOKENS.get(start.lease.lease_id);
  const answer = await mf.dispatchFetch(`${EGRESS_ORIGIN}/r/${start.run}/events`, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "x-ta-sandbox": tokens.egress_token,
               "x-ta-network-generation": "1", "x-ta-runner": tokens.runner_token } });
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

test("a new owner is seeded: main and the starter's files, and no failure told",
     { timeout: 60_000 }, async () => {
  // The seed runs on the owner's own lane once the sign-in admits it; wait for it to land.
  let starter;
  for (const deadline = Date.now() + 45_000; Date.now() < deadline;) {
    starter = await (await mf.dispatchFetch(`https://e2e.internal/starter/${WHO}`)).json();
    if (starter.version > 0 || starter.failed.length > 0) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  assert.deepEqual(starter.failed, [], "the seed told no failure");
  assert.ok(starter.version > 0, "the seed landed");
  assert.ok(starter.agents.includes("main"), "main is the owner's agent");
  assert.deepEqual(starter.files, { "/workspace/USER.md": true, "/workspace/index.md": true,
                                    "/workspace/agents/main/agent.json": true });
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

test("a connected key with no credit says it runs free models only", { timeout: 60_000 },
     async () => {
  await page.goto(`${ORIGIN}/app#sources`);
  const card = page.locator(".card", { hasText: "OpenRouter" });
  await card.locator("input[type=password]").fill(`sk-or-free-${randomBytes(8).toString("hex")}`);
  await card.getByRole("button", { name: "Connect with a key" }).click();
  await page.locator(".message.good", { hasText: "Connected OpenRouter" }).waitFor();
  // The owner's connections lane reads the key's credit once it is connected.
  const row = page.locator("tr", { hasText: "OpenRouter" });
  for (let tries = 0; await row.getByText("Free models only").count() === 0; tries++) {
    assert.ok(tries < 50, "the sources screen says the key runs free models only");
    await new Promise((resolve) => setTimeout(resolve, 200));
    await page.reload();
  }
  await row.getByRole("button", { name: "Disconnect" }).click();
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
  // An agent of the owner's own making: main is the starter's, seeded at sign-up.
  await form.getByLabel(/^Name/).fill("helper");
  await form.getByLabel(/^Engine/).selectOption("pi");
  await form.getByLabel(/^Runs on/).selectOption("api_key");
  const connection = form.getByLabel(/^API key connection/);
  const groq = await connection.locator("option", { hasText: "groq" }).getAttribute("value");
  const keyName = (await connection.locator("option", { hasText: "groq" }).innerText())
    .replace(/ [(]groq[)]$/, "");
  await connection.selectOption(groq);
  await form.getByRole("checkbox", { name: "Read your files" }).check();
  // Every change the making sends, in order: the agent is its agent.json, so that is the
  // last write, after its folder and its AGENTS.md.
  const changes = [];
  const onRequest = (request) => {
    const path = new URL(request.url()).pathname;
    if (/^\/app\/api\/(?:files\/write|tools\/file_(?:mkdir|write|remove|rename))$/.test(path)) {
      changes.push([path.slice("/app/api/".length), request.postDataJSON().path]);
    }
  };
  page.on("request", onRequest);
  await form.getByRole("button", { name: "Make the agent" }).click();
  await page.waitForURL(`${ORIGIN}/app#agents/helper`);
  await page.getByRole("link", { name: "agent.json" }).waitFor();
  await page.getByRole("link", { name: "AGENTS.md" }).waitFor();
  await page.locator("main p", { hasText: "A run you start now: " }).waitFor();
  const previewed = await page.locator("main").innerText();
  page.off("request", onRequest);
  assert.deepEqual(changes, [["tools/file_mkdir", "/workspace/agents/helper"],
                             ["files/write", "/workspace/agents/helper/AGENTS.md"],
                             ["files/write", "/workspace/agents/helper/agent.json"]]);

  await page.getByPlaceholder("Say something to helper").fill("hello");
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
  // Every run is given its model, never its engine's own default, and the owner sees which:
  // on the agent's page before the run, attended and unattended alike, and on its line.
  assert.equal(typeof start.model, "string");
  for (const who of ["A run you start now", "A run on your schedule, now"]) {
    assert.ok(previewed.includes(`${who}: pi (${start.model}) on ${keyName} (${groq}).`),
              previewed);
  }
  assert.ok((await page.locator("main").innerText())
    .includes(`Run succeeded on pi (${start.model})`));
  await shot("agent");
  // A run ended for a code with no words of its own shows the code as it came.
  await page.getByPlaceholder("Say something to helper").fill("again");
  await page.getByRole("button", { name: "Send" }).click();
  let again;
  for (let i = 0; i < 100 && again === undefined; i++) {
    again = (await starts()).find((each) => each.run !== start.run);
    if (again === undefined) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  await report(again, { frames: [], end: { exit_code: 1 } });
  await page.locator(".said.agent", { hasText: "(exit)" }).waitFor();
  assert.ok((await page.locator("main").innerText())
    .includes(`Run failed on pi (${again.model}): exit`));
});

test("the agent form offers a subscription signed in inside the sandbox, beside an API key " +
     "(SIGNIN, kept through APP's merge)", { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#agents`);
  await page.getByRole("heading", { name: "New agent" }).waitFor();
  const form = page.locator("section", { hasText: "New agent" });
  await form.getByLabel(/^Engine/).selectOption("claude-code");
  const runsOn = form.getByLabel(/^Runs on/);
  assert.deepEqual(await runsOn.locator("option").allInnerTexts(),
                   ["Whichever the engine has", "Your subscription, signed in inside your sandbox",
                    "An API key you connected"]);
  await form.getByLabel(/^Name/).fill("subscribed");
  await runsOn.selectOption("vendor_login_in_sandbox");
  const written = [];
  const onRequest = (request) => {
    if (new URL(request.url()).pathname === "/app/api/files/write") {
      written.push(request.postDataJSON());
    }
  };
  page.on("request", onRequest);
  await form.getByRole("button", { name: "Make the agent" }).click();
  await page.waitForURL(`${ORIGIN}/app#agents/subscribed`);
  page.off("request", onRequest);
  const file = written.find((each) => each.path === "/workspace/agents/subscribed/agent.json");
  assert.ok(file, "the form wrote the agent's agent.json");
  assert.match(JSON.stringify(file), /vendor_login_in_sandbox/);
  // Codex's sign-in is switched off (its terms unclear), so the form offers its key alone;
  // switched on in the control record, the form offers its sign-in beside it.
  const offered = async () => {
    await page.goto(`${ORIGIN}/app#home`);
    await page.goto(`${ORIGIN}/app#agents`);
    const again = page.locator("section", { hasText: "New agent" });
    await again.getByLabel(/^Engine/).selectOption("codex");
    return await again.getByLabel(/^Runs on/).locator("option").allInnerTexts();
  };
  assert.deepEqual(await offered(), ["Whichever the engine has", "An API key you connected"]);
  const control = await mf.getKVNamespace("CONTROL", workerConfig(DOORS).name);
  await writeControl(control, JSON.stringify({ ...OPEN, signins: { codex: true } }));
  try {
    assert.deepEqual(await offered(), ["Whichever the engine has",
                                       "Your subscription, signed in inside your sandbox",
                                       "An API key you connected"]);
  } finally {
    await writeControl(control, JSON.stringify(OPEN));
  }
});

test("the owner signs in to a subscription inside its sandbox: the vendor's page, a code " +
     "pasted back, the end", { timeout: 120_000 }, async () => {
  await page.goto(`${ORIGIN}/app#sources`);
  const card = page.locator(".card", { hasText: "Claude" }).filter({ has: page.locator(".signin") })
    .first();
  /** The `n`th sign-in the app asked the sandbox to start, once it has. */
  const asked = async (n) => {
    for (let i = 0; i < 100; i++) {
      const { signins } = await (await mf.dispatchFetch(`https://e2e.internal/signins/${WHO}`))
        .json();
      if (signins.length > n) {
        return signins[n];
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("the app did not ask the sandbox to start the sign-in");
  };
  await card.getByRole("button", { name: "Sign in to Claude" }).click();
  await card.getByText("Starting the sign-in in your sandbox").waitFor();
  let signin = await asked(0);
  assert.equal(signin.engine, "claude-code");
  const post = async (body) => {
    const answer = await mf.dispatchFetch(`${EGRESS_ORIGIN}/signin/${signin.id}/events`, {
      method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json",
                 ...stampedOf(signin),
                 "x-ta-runner": signin.tokens.runner_token } });
    assert.equal(answer.status, 200, await answer.clone().text());
  };
  // Claude's sign-in page as its pinned CLI shows it (template s6dec61a38e839d37, live).
  const vendor = "https://claude.com/cai/oauth/authorize?code=true&client_id=" +
    "9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2F" +
    "platform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+" +
    "user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload+" +
    "user%3Aplugins&code_challenge=yfhUky71rorbPUGR_0kuaJoQPT0k39HLxYDNc-gUtt8&" +
    "code_challenge_method=S256&state=Lq7TdjukIEmxyMX2EKf2hY4xChr1sfIJOV3krpnv6iE";
  await post({ frames: [{ seq: 1, at: 1, event: { type: "ta.signin", state: "open",
                                                   url: vendor, code: null,
                                                   accepts_code: true } }] });
  const link = card.getByRole("link", { name: "Open Claude's sign-in page" });
  await link.waitFor();
  assert.equal(await link.getAttribute("href"), vendor);
  assert.equal(await link.getAttribute("rel"), "noopener noreferrer");
  await card.getByText("You'll sign in on claude.com.").waitFor();
  await card.getByPlaceholder("Code").fill("pasted-code#state");
  await card.getByRole("button", { name: "Send code" }).click();
  for (let i = 0; i < 100; i++) {
    const { codes } = await (await mf.dispatchFetch(`https://e2e.internal/signins/${WHO}`)).json();
    if (codes.length > 0) {
      assert.deepEqual(codes.map((each) => each.code), ["pasted-code#state"]);
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  await post({ frames: [{ seq: 2, at: 2, event: { type: "ta.signin", state: "signed_in" } }],
               end: { exit_code: 0 } });
  await page.locator(".message.good", { hasText: "Signed in to Claude." }).waitFor();
  await shot("signin");
  // A look-alike page is refused in plain words, and the panel offers a new one.
  /** A new sign-in started from the panel, its page shown (so the app is watching it). */
  const again = async (n) => {
    await card.getByRole("button", { name: /^Sign in/ }).click();
    signin = await asked(n);
    await post({ frames: [{ seq: 1, at: 1, event: { type: "ta.signin", state: "open",
                                                     url: vendor, code: null } }] });
    await card.getByRole("link", { name: "Open Claude's sign-in page" }).waitFor();
  };
  await again(1);
  await post({ frames: [{ seq: 2, at: 2, event: { type: "ta.signin", state: "open",
                                                   url: vendor.replace("client_id=9d1c",
                                                                       "client_id=evil"),
                                                   code: null } }] });
  await page.locator(".message.bad", {
    hasText: "it showed a link that is not the vendor's own sign-in page" }).waitFor();
  // A code past its lifetime ends the sign-in, and the app offers to start again.
  await again(2);
  await post({ frames: [{ seq: 2, at: 2, event: { type: "ta.signin", state: "expired" } }] });
  await page.locator(".message.bad", { hasText: "The code expired; start again." }).waitFor();
  await card.getByRole("button", { name: /^Sign in/ }).waitFor();
});

test("a sign-in that could not start says why in plain words, in view of its button",
     { timeout: 60_000 }, async () => {
  const script = (answer) => mf.dispatchFetch(`https://e2e.internal/script/${WHO}`, {
    method: "POST", body: JSON.stringify({ signinAnswer: answer }) });
  // A short window, so the button is well below the top of the page, as on a phone.
  const was = page.viewportSize();
  await page.setViewportSize({ width: 400, height: 500 });
  await script({ started: false, code: "sandbox_unconfigured" });
  try {
    await page.goto(`${ORIGIN}/app#sources`);
    const card = page.locator(".card", { hasText: "Claude" })
      .filter({ has: page.locator(".signin") }).first();
    const button = card.getByRole("button", { name: /^Sign in/ });
    await button.scrollIntoViewIfNeeded();
    await button.click();
    const message = page.locator("#notice .message.bad",
                                 { hasText: "TinyAssets cannot start sandboxes yet" });
    await message.waitFor();
    assert.equal((await message.textContent()).includes("sandbox_unconfigured"), false);
    const { top, bottom } = await message.evaluate((el) => el.getBoundingClientRect());
    assert.ok(top >= 0 && bottom <= 500, `the refusal is out of view (${top} to ${bottom})`);
    assert.ok(await page.evaluate(() => window.scrollY > 0), "the page was not scrolled");
    // Held in view, so the owner can put it away.
    await message.getByRole("button", { name: "Dismiss" }).click();
    await message.waitFor({ state: "detached" });
  } finally {
    await script({ started: true });
    await page.setViewportSize(was);
  }
});

test("a sign-in whose socket drops is followed on a new socket, and ends as it ends " +
     "(task 68)", { timeout: 120_000 }, async () => {
  // The app's sockets, kept so the test can drop one as a network would.
  await context.addInitScript(() => {
    const Real = window.WebSocket;
    window.__sockets = [];
    window.WebSocket = class extends Real {
      constructor(...args) {
        super(...args);
        window.__sockets.push(this);
      }
    };
  });
  await page.goto(`${ORIGIN}/app#sources`);
  await page.reload();  // a new document, so the script above runs in it
  const card = page.locator(".card", { hasText: "Claude" }).filter({ has: page.locator(".signin") })
    .first();
  const list = async () => (await (await mf.dispatchFetch(`https://e2e.internal/signins/${WHO}`))
    .json()).signins;
  const before = (await list()).length;
  await card.getByRole("button", { name: /^Sign in/ }).click();
  let signin;
  for (let i = 0; i < 100 && signin === undefined; i++) {
    signin = (await list())[before];
    if (signin === undefined) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  assert.ok(signin, "the app did not ask the sandbox to start the sign-in");
  const post = async (body) => {
    const answer = await mf.dispatchFetch(`${EGRESS_ORIGIN}/signin/${signin.id}/events`, {
      method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json",
                 ...stampedOf(signin),
                 "x-ta-runner": signin.tokens.runner_token } });
    assert.equal(answer.status, 200, await answer.clone().text());
  };
  const vendor = "https://claude.com/cai/oauth/authorize?code=true&client_id=" +
    "9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2F" +
    "platform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+" +
    "user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload+" +
    "user%3Aplugins&code_challenge=yfhUky71rorbPUGR_0kuaJoQPT0k39HLxYDNc-gUtt8&" +
    "code_challenge_method=S256&state=Lq7TdjukIEmxyMX2EKf2hY4xChr1sfIJOV3krpnv6iE";
  await post({ frames: [{ seq: 1, at: 1, event: { type: "ta.signin", state: "open", url: vendor,
                                                   code: null, accepts_code: false } }] });
  await card.getByRole("link", { name: "Open Claude's sign-in page" }).waitFor();
  const open = () => page.evaluate(() => window.__sockets
    .filter((socket) => socket.url.includes("/app/api/agent/socket")
                        && socket.readyState === WebSocket.OPEN).length);
  // The link can come from the sign-in's row, read while its socket still connects: wait
  // for the socket to open rather than read it once.
  for (let i = 0; i < 100 && await open() !== 1; i++) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(await open(), 1);
  const made = await page.evaluate(() => window.__sockets.length);
  // The connection drops, as a network drops it: the app never closed it.
  await page.evaluate(() => window.__sockets.filter((socket) =>
    socket.url.includes("/app/api/agent/socket")).at(-1).close(4000, "dropped"));
  for (let i = 0; i < 100; i++) {
    if (await page.evaluate(() => window.__sockets.length) > made && await open() === 1) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(await open(), 1, "no new socket after the drop");
  // The state read again on the new socket: the sign-in is still shown, then ends.
  await card.getByRole("link", { name: "Open Claude's sign-in page" }).waitFor();
  await post({ frames: [{ seq: 2, at: 2, event: { type: "ta.signin", state: "signed_in" } }],
               end: { exit_code: 0 } });
  await page.locator(".message.good", { hasText: "Signed in to Claude." }).last().waitFor();
});

test("a talk whose socket drops mid-run goes on on a new socket, each frame shown once",
     { timeout: 120_000 }, async () => {
  // The sockets are kept by the script the sign-in drop's test added to the context.
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.reload();
  const before = (await starts()).length;
  await page.getByPlaceholder("Say something to helper").fill("again");
  await page.getByRole("button", { name: "Send" }).click();
  let start;
  for (let i = 0; i < 100 && start === undefined; i++) {
    start = (await starts())[before];
    if (start === undefined) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  assert.ok(start, "the run asked the sandbox to start");
  await report(start, { frames: [
    { seq: 1, at: 1, event: { type: "session", id: "s-drop" } },
    { seq: 2, at: 2, event: { type: "tool_execution_start", tool: "read" } }] });
  await page.locator(".frame", { hasText: "s-drop" }).waitFor();
  const live = () => page.evaluate(() => window.__sockets
    .filter((socket) => socket.url.includes("/app/api/agent/socket")
                        && socket.readyState === WebSocket.OPEN).length);
  const made = await page.evaluate(() => window.__sockets.length);
  await page.evaluate(() => window.__sockets.filter((socket) =>
    socket.url.includes("/app/api/agent/socket") && socket.readyState === WebSocket.OPEN)
    .at(-1).close(4000, "dropped"));
  for (let i = 0; i < 100; i++) {
    if (await page.evaluate(() => window.__sockets.length) > made && await live() >= 1) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(await live() >= 1, "no new socket after the drop");
  // The doors sent the run's kept frames again to the new socket: each is shown once.
  await report(start, { frames: [
    { seq: 3, at: 3, event: { type: "message_end",
                              message: { role: "assistant", content: "Again from pi" } } }],
                        end: { exit_code: 0 } });
  await page.locator(".said.agent", { hasText: "Again from pi" }).waitFor();
  assert.equal(await page.locator(".frame", { hasText: "s-drop" }).count(), 1);
  const frames = await page.locator(".frame").allInnerTexts();
  assert.equal(frames.filter((text) => text.includes("s-drop")).length, 1);
  assert.equal(new Set(frames).size, frames.length, `a frame shown twice: ${frames.join(" | ")}`);
});

test("every screen opens on the doors' answers, and says plainly what is not here yet",
     { timeout: 60_000 }, async () => {
  for (const [hash, heading] of [["home", "Home"], ["agents", "Agents"],
                                 ["history", "Past runs"],
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
  // A key, shown once, that writes in the owner's workspace as the platform canary's does.
  await page.goto(`${ORIGIN}/app#keys`);
  await page.getByLabel(/^Name/).fill("laptop");
  await page.getByRole("button", { name: "Make the key" }).click();
  const key = await page.locator(".secret").innerText();
  assert.match(key, /^ta_key_[0-9a-f]{16}_/);
  const keyed = await mf.dispatchFetch(`${ORIGIN}/api/tools/file_write`, { method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ path: "/workspace/beat.txt", expected_etag: "", content: "beat" }) });
  assert.equal(keyed.status, 200, await keyed.text());
  // Code mode, off by default, turned on in the owner's session.
  await page.goto(`${ORIGIN}/app#apps`);
  const codemode = page.locator("section", { hasText: "Code mode" });
  assert.equal(await codemode.getByRole("checkbox").isChecked(), false);
  await codemode.getByRole("checkbox").check();
  await codemode.getByRole("button", { name: "Save" }).click();
  await page.locator(".message.good", { hasText: "Saved." }).waitFor();
  assert.equal(await page.locator("section", { hasText: "Code mode" }).getByRole("checkbox")
    .isChecked(), true);
  // Nothing waits on the owner.
  await page.goto(`${ORIGIN}/app#home`);
  await page.getByText("Nothing is waiting for your answer.").waitFor();
  // No main agent has joined, so no wake-up is offered.
  await page.goto(`${ORIGIN}/app#apps`);
  await page.getByText("No main agent has joined yet.").waitFor();
  // "Every tool" holds every pick on, fixed, so no choice is silently dropped.
  const picks = page.locator("section", { hasText: "Tools your connected apps see" });
  assert.equal(await picks.getByRole("checkbox", { name: /Every tool/ }).isChecked(), true);
  assert.equal(await picks.getByRole("checkbox", { name: "List agents" }).isDisabled(), true);
  await picks.getByRole("checkbox", { name: /Every tool/ }).uncheck();
  assert.equal(await picks.getByRole("checkbox", { name: "List agents" }).isDisabled(), false);
  // The doors this app knows are missing say so before anything is clicked, with no button;
  // past runs are said plainly under Talk.
  await page.goto(`${ORIGIN}/app#data`);
  await page.getByText(/^Exporting everything you hold is not here yet/).waitFor();
  assert.equal(await page.getByRole("button", { name: /Export/ }).count(), 0);
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.getByText(/not kept across reloads/).waitFor();
});

test("a connected app whose agent was deleted says so on its row, and its save names the " +
     "agent chosen", { timeout: 60_000 }, async () => {
  // No app connects in this harness, so the doors' list is answered with one whose grant
  // names an agent since deleted; the save goes to the real doors.
  const gone = { client: "c".repeat(64), source: "mcp", mode: "full", scopes: [], revision: 3,
                 revoked_at: null, vendor: null, vendor_verified: 0, name: "Muse",
                 profile: "custom", agent: "gone", agent_live: false, first_seen: 0,
                 last_seen: 0 };
  const listed = (route) => route.fulfill({ json: { clients: [gone] } });
  await page.route("**/app/api/grants", listed);
  try {
    await page.goto(`${ORIGIN}/app#apps`);
    const row = page.locator("tr", { hasText: "Muse" });
    await row.getByText(/^This agent was deleted/).waitFor();
    const agent = row.getByRole("combobox").nth(1);
    assert.equal(await agent.locator("option:checked").innerText(), "gone (deleted)");
    await agent.selectOption("main");
    const sent = page.waitForRequest((request) =>
      request.url().endsWith("/app/api/grants/set"));
    await row.getByRole("button", { name: "Save" }).click();
    assert.deepEqual((await sent).postDataJSON(), {
      client: gone.client, source: "mcp", mode: "full", scopes: [], expected_revision: 3,
      name: "Muse", profile: "custom", agent: "main" });
    // The real doors know no such client, and say so.
    await page.locator("#notice .message.bad").waitFor();
  } finally {
    await page.unroute("**/app/api/grants", listed);
  }
});

test("a main agent is turned off or deleted from its card, and a disconnected app is let " +
     "connect again", { timeout: 60_000 }, async () => {
  // No app connects in this harness, so the doors' lists are answered with a main agent on,
  // one off and a disconnected app; each answer goes to the real doors.
  const agent = (agent_id, display_name, disabled_reason) => ({ agent_id, display_name,
    disabled_reason, command_center_id: "main", delivery_method: "pull", room_id: null });
  const mains = { agents: [agent("agent_on", "Muse", null),
                           agent("agent_off", "Dot", "turned off by the owner")],
                  owner_agents: [], apps: [] };
  const gone = { client: "d".repeat(64), source: "mcp", mode: "revoked", scopes: [], revision: 4,
                 revoked_at: 1, vendor: null, vendor_verified: 0, name: "Back",
                 profile: "directory", agent: "main", agent_live: true, first_seen: 0,
                 last_seen: 0 };
  const listedAgents = (route) => route.fulfill({ json: mains });
  const listedGrants = (route) => route.fulfill({ json: { clients: [gone] } });
  await page.route("**/app/api/tools/agents_list", listedAgents);
  await page.route("**/app/api/grants", listedGrants);
  const sent = (path) => page.waitForRequest((request) =>
    request.url().endsWith(`/app/api/${path}`));
  try {
    // From another screen: the test before ends on this one, which a goto would not redraw.
    await page.goto(`${ORIGIN}/app#home`);
    await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
    await page.goto(`${ORIGIN}/app#apps`);
    const on = page.locator(".card", { hasText: "Muse" });
    const off = page.locator(".card", { hasText: "Dot" });
    await off.getByText(/^Turned off\. It comes back when its app asks to join again/).waitFor();
    assert.equal(await off.getByRole("button", { name: "Turn off" }).count(), 0);
    const disabling = sent("agent/disable");
    await on.getByRole("button", { name: "Turn off" }).click();
    assert.deepEqual((await disabling).postDataJSON(),
                     { agent_id: "agent_on", reason: "turned off by the owner" });
    // The real doors know no such agent, and say so.
    await page.locator("#notice .message.bad", { hasText: "agent not found" }).waitFor();
    // Delete asks first, naming what goes.
    await off.getByRole("button", { name: "Delete" }).click();
    await off.getByText(/^Delete Dot and all its mail .* This cannot be undone\.$/).waitFor();
    const deleting = sent("agent/delete");
    await off.getByRole("button", { name: "Delete Dot" }).click();
    assert.deepEqual((await deleting).postDataJSON(), { agent_id: "agent_off" });
    await page.locator("#notice .message.bad", { hasText: "agent not found" }).waitFor();
    // A disconnected app is listed apart, and let back by setting its grant again.
    const back = page.locator("li", { hasText: "Back" });
    const setting = sent("grants/set");
    await back.getByRole("button", { name: "Let it connect again" }).click();
    assert.deepEqual((await setting).postDataJSON(), {
      client: gone.client, source: "mcp", mode: "full", scopes: [], expected_revision: 4,
      name: "Back", profile: "directory", agent: "main" });
    await page.locator("#notice .message.bad").waitFor();
  } finally {
    await page.unroute("**/app/api/tools/agents_list", listedAgents);
    await page.unroute("**/app/api/grants", listedGrants);
  }
});

test("a run that mail started shows its mail, and a file's older versions are removed",
     { timeout: 120_000 }, async () => {
  // The owner's mail to its own agent starts that agent's run, which names the mail.
  await page.goto(`${ORIGIN}/app#rooms`);
  await page.getByLabel(/^Message/).fill("read me back");
  await page.getByRole("button", { name: "Post" }).click();
  await page.locator(".message.good", { hasText: "Posted." }).waitFor();
  // The runs lane starts it on the owner's own schedule; wait for Past runs to list it.
  const read = page.getByRole("button", { name: "Read the mail" }).first();
  for (const deadline = Date.now() + 60_000; await read.count() === 0;) {
    assert.ok(Date.now() < deadline, "no run that mail started is listed");
    await page.goto(`${ORIGIN}/app#history`);
    await page.getByRole("heading", { name: "Past runs", exact: true }).waitFor();
    await page.locator("main p.muted", { hasText: "Loading" }).waitFor({ state: "detached" });
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  await read.click();
  await page.getByRole("heading", { name: "The mail", exact: true }).waitFor();
  await page.locator("li", { hasText: /good morning|read me back/ }).first().waitFor();
  // A file saved twice keeps both versions until the owner removes the older one's content,
  // then every older version.
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.getByPlaceholder("skills/notes/SKILL.md").fill("versions.md");
  await page.getByRole("button", { name: "New file" }).click();
  const kept = page.locator("li", { hasText: /: [a-z]+ by / });
  for (const text of ["key=secret", "key=<rotated>"]) {
    await page.locator("textarea.editor").fill(text);
    await page.getByRole("button", { name: "Save" }).click();
    await page.locator(".message.good", { hasText: "Saved" }).waitFor();
  }
  await kept.nth(1).waitFor();
  assert.equal(await kept.count(), 2);
  await kept.first().getByText("(current)").waitFor();
  // Each asks first, saying what goes.
  await kept.nth(1).getByRole("button", { name: "Remove its content" }).click();
  await kept.nth(1).getByText(/^Remove this version's content\? .* cannot be undone\.$/).waitFor();
  await kept.nth(1).getByRole("button", { name: "Remove it", exact: true }).click();
  await page.locator(".message.good", { hasText: "Removed that version's content" }).waitFor();
  await kept.nth(1).getByText("(no content kept)").waitFor();
  await page.getByRole("button", { name: "Remove older versions" }).click();
  await page.getByText(/^Remove every version of .* but the current one/).waitFor();
  await page.getByRole("button", { name: "Remove them", exact: true }).click();
  await page.locator(".message.good", { hasText: "Removed every older version" }).waitFor();
  await page.getByRole("button", { name: "Remove older versions" }).waitFor({ state: "detached" });
  assert.equal(await kept.count(), 1);
  assert.equal(await page.locator("textarea.editor").inputValue(), "key=<rotated>");
});

test("every card waiting on the owner is on Home, and its page, where a refusal, an " +
     "approve_url and a notice lead, shows what it asks and sends each answer by the card " +
     "shown", { timeout: 60_000 }, async () => {
  // No app connects in this harness, so the doors' cards, grants and notices are answered with
  // one app's; each answer goes to the real doors, which hold no such card.
  const client = { client: "c".repeat(64), source: "mcp", mode: "scoped", revision: 1,
                   scopes: [{ universe: "work", agent: "main", capability: "read_graph" }],
                   revoked_at: null, vendor: null, vendor_verified: 0, name: "Muse",
                   profile: "custom", agent: "main", agent_live: true, first_seen: 0,
                   last_seen: 0 };
  const card = { card_id: `card_${"1".repeat(32)}`, digest: "d".repeat(64), client: client.client,
                 family: "f".repeat(64), source_ref: "conn", filed_at: 1_700_000_000,
                 request: { command_center_id: "work", display_name: "Muse Agent",
                            delivery_method: "pull", recipe_id: null } };
  const held = { card_id: `act_${"2".repeat(32)}`, digest: "e".repeat(64), act: "workspace.files",
                 says: "Changing the owner's files.", door: "fileMkdir", agent: "main",
                 client: client.client, conversation: "conv-1",
                 request: '{"expected_etag":null,"path":"/workspace/held"}', cut: false,
                 generation: 1, filed_at: 1_700_000_100 };
  const proposal = { card_id: "card-9", digest: "sha256:" + "f".repeat(64), client: null,
                     filed_at: 1_700_000_200,
                     change: { add: { egress: { destinations: [{ host: "github.com",
                                                                 port: 443 }] } } } };
  const pages = [card, held, proposal].map((each) => waitingPage(each.card_id));
  const fakes = [
    ["**/app/api/registrations", { cards: [card] }], ["**/app/api/actions", { cards: [held] }],
    ["**/app/api/policy/proposals", { cards: [proposal] }],
    ["**/app/api/grants", { clients: [client] }],
    ["**/app/api/notices", { notices: [
      { seq: 1, at: card.filed_at, type: "agent_registration",
        detail: `An app you connected asks to register an agent: approve or decline it at ${
          pages[0]}` },
      { seq: 2, at: held.filed_at, type: "action_request",
        detail: `main asks to workspace.files at fileMkdir: allow or deny it at ${pages[1]}` },
      { seq: 3, at: proposal.filed_at, type: "policy_proposal",
        detail: `Your agent asks to change your settings: apply or decline it at ${pages[2]}` }] }]];
  const answered = fakes.map(([path, json]) => [path, (route) => route.fulfill({ json })]);
  for (const [path, handler] of answered) {
    await page.route(path, handler);
  }
  try {
    // Home lists every kind, oldest first, in one view.
    await page.goto(`${ORIGIN}/app#home`);
    const waiting = page.locator("section", { hasText: "Waiting for you" }).locator(".card");
    await waiting.nth(2).waitFor();
    assert.deepEqual((await waiting.allInnerTexts()).map((text) => text.split("\n")[0]),
                     ["Muse Agent", "Changing the owner's files.", "A change to your settings"]);
    for (const [shown, lines, answers] of [
      [card, ["asked by Muse", "Gets its mail by: pull", "Recipe: none", "Space: work",
              "What its app may do: read_graph in work as main"],
       [["Apply", "registrations/apply"], ["Decline", "registrations/decline"]]],
      [held, ["asked by main, started by Muse", "Act: workspace.files", "At: fileMkdir",
              "In conversation: conv-1", '"path":"/workspace/held"', "resumes its run"],
       [["Allow once", "actions/approve"], ["Deny", "actions/refuse"]]],
      [proposal, ["asked by your own agent", '"host": "github.com"',
                  "Applying makes only these changes", "Nothing else changes",
                  "waits for you to decline it"],
       [["Apply", "policy/apply"], ["Decline", "policy/decline"]]],
    ]) {
      // Each notice on Home links to its card's page.
      await page.goto(`${ORIGIN}/app#home`);
      const at = waitingPage(shown.card_id);
      await page.getByRole("link", { name: at }).click();
      await page.getByRole("heading", { name: "Waiting for your answer" }).waitFor();
      assert.equal(new URL(page.url()).hash, `#home/${shown.card_id}`);
      const one = page.locator(".card");
      assert.equal(await one.count(), 1);
      for (const line of lines) {
        await one.getByText(line, { exact: false }).waitFor();
      }
      for (const [button, path] of answers) {
        const sent = page.waitForRequest((request) => request.url().endsWith(`/app/api/${path}`));
        await one.getByRole("button", { name: button }).click();
        assert.deepEqual((await sent).postDataJSON(), { card_id: shown.card_id,
                                                        digest: shown.digest });
        await page.locator("#notice .message.bad", { hasText: "not the pending card shown" })
          .waitFor();
      }
    }
  } finally {
    for (const [path, handler] of answered) {
      await page.unroute(path, handler);
    }
  }
  // A page whose card no longer waits says so, with no answer to give.
  await page.goto(`${ORIGIN}/app#home`);
  await page.goto(waitingPage(held.card_id));
  await page.getByText(/^This is not waiting for your answer any more/).waitFor();
  assert.equal(await page.getByRole("button", { name: "Allow once" }).count(), 0);
});

// -- Attacks on the app, kept as tests -----------------------------------------------------

const P_IMG = `<img src=x onerror="window.__xss=(window.__xss||0)+1">`;
const P_SVG = `"><svg onload="window.__xss=(window.__xss||0)+1"></svg>`;
const P_SCRIPT = `</textarea><script>window.__xss=(window.__xss||0)+1</script>`;
const PAYLOAD = `${P_IMG}${P_SVG}${P_SCRIPT}`;

/** One owner-surface call from the page itself: its token, and its cookie. */
async function api(path, body = {}) {
  return page.evaluate(async ([p, b]) => {
    const held = JSON.parse(sessionStorage.getItem("ta.app.tokens"));
    const answer = await fetch(`/app/api/${p}`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${held.access_token}` },
      body: JSON.stringify(b) });
    return { status: answer.status, body: await answer.json().catch(() => null) };
  }, [path, body]);
}

/** One owner-surface call with the token alone: no session cookie, as a leaked token. */
async function bare(path, body) {
  const token = await page.evaluate(() =>
    JSON.parse(sessionStorage.getItem("ta.app.tokens")).access_token);
  const answer = await mf.dispatchFetch(`${ORIGIN}/app/api/${path}`, { method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`,
               Origin: ORIGIN, "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify(body) });
  return { status: answer.status, body: await answer.json().catch(() => null) };
}

test("pages.ts serves only its names: traversal, encodings and prototype names are 404",
     async () => {
  const probes = ["/app/static/%2e%2e/index.html", "/app/static/..%2findex.html",
    "/app/static/..%2f..%2fwrangler.jsonc", "/app/static/%2e%2e%2f%2e%2e%2fsrc%2fpages.ts",
    "/app/static/app.js%00", "/app/static/APP.JS", "/app/static/__proto__",
    "/app/static/constructor", "/app/static/hasOwnProperty", "/app/static/toString",
    "/app//static/app.js", "/app/static//app.js", "/app/static/%61pp.js", "/app/signed-in/",
    "/APP", "/wrangler.jsonc", "/app.css", "/index.html", "/app/", "/app/static/app.js;x",
    "/app/static/.%2e/connected.html", "/app/static/app.js%2f", "/app/static/%5c..%5cindex.html",
    "/app/__proto__", "/app/static/signed-in.html", "/app/static/index.html"];
  for (const path of probes) {
    const answer = await mf.dispatchFetch(`${ORIGIN}${path}`);
    const text = await answer.text();
    assert.equal(answer.status, 404, path);
    assert.ok(!/compatibility_date|import type/.test(text), `${path} served source`);
    if (/^\/app(?:\/|$)/.test(path)) {
      assert.ok(answer.headers.get("Content-Security-Policy"), `${path} 404 without the CSP`);
    }
  }
  assert.equal((await mf.dispatchFetch(`${ORIGIN}/app/static/app.js?x=1`)).status, 200);
});

test("the CSP refuses inline script, inline style, HTML sinks, string code and javascript:",
     { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#home`);
  await page.getByRole("heading", { name: "Home" }).waitFor();
  const tried = await page.evaluate(async () => {
    const out = {};
    const attempt = (name, act) => {
      try {
        act();
        out[name] = "ran";
      } catch (e) {
        out[name] = `threw ${e.name}`;
      }
    };
    const s = document.createElement("script");
    attempt("textContent", () => { s.textContent = "window.__inline = 1"; });
    document.head.append(s);
    attempt("insertAdjacentHTML", () =>
      document.body.insertAdjacentHTML("beforeend", "<img src=x onerror='window.__xss=99'>"));
    attempt("innerHTML", () => { document.getElementById("main").innerHTML = "<b>x</b>"; });
    const b = document.createElement("button");
    attempt("onclick", () => b.setAttribute("onclick", "window.__xss=98"));
    document.body.append(b);
    b.click();
    const d = document.createElement("div");
    d.setAttribute("style", "color: rgb(1, 2, 3)");
    document.body.append(d);
    out.style = getComputedStyle(d).color;
    // The javascript: link is the one attempt the page makes later, on its own task: its
    // refusal (inline script, refused) is the record that it never ran.
    const refused = new Promise((resolve) => {
      document.addEventListener("securitypolicyviolation", (event) => {
        if (event.effectiveDirective === "script-src-elem" && event.blockedURI === "inline") {
          resolve();
        }
      });
    });
    const a = document.createElement("a");
    a.href = "javascript:window.__xss=97";
    document.body.append(a);
    a.click();
    attempt("setTimeoutString", () => setTimeout("window.__xss=96", 0));
    // Outside the inspector's own evaluation, which lifts the eval block for itself.
    await new Promise((resolve) => setTimeout(() => {
      attempt("eval", () => (0, eval)("1"));
      attempt("Function", () => new Function("return 1")());
      resolve();
    }, 0));
    await refused;
    out.inline = window.__inline ?? null;
    out.xss = window.__xss ?? null;
    return out;
  });
  assert.equal(tried.inline, null);
  assert.equal(tried.xss, null);
  assert.notEqual(tried.style, "rgb(1, 2, 3)");
  for (const sink of ["textContent", "insertAdjacentHTML", "innerHTML", "onclick",
                      "setTimeoutString", "eval", "Function"]) {
    assert.match(tried[sink], /^threw/, sink);
  }
  // The console's reports of those refusals are this test's own, not the app's.
  errors.splice(0);
});

test("no payload runs through file names, file text, frames, results, posts or the hash",
     { timeout: 180_000 }, async () => {
  const dialogs = [];
  const onDialog = (dialog) => {
    dialogs.push(dialog.message());
    dialog.dismiss();
  };
  page.on("dialog", onDialog);
  for (const path of [`/workspace/agents/helper/${P_IMG}.md`, `/workspace/${P_SVG}.md`]) {
    assert.equal((await api("files/write", { path, expected_etag: "", text: PAYLOAD })).status,
                 200, path);
  }
  const agentsMd = (await api("files/read", { path: "/workspace/agents/helper/AGENTS.md" })).body;
  assert.equal((await api("files/write", { path: "/workspace/agents/helper/AGENTS.md",
    expected_etag: agentsMd.entry.etag, text: PAYLOAD })).status, 200);
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.reload();
  await page.getByRole("link", { name: "AGENTS.md" }).click();
  await page.locator("textarea.editor").waitFor();
  assert.equal(await page.locator("textarea.editor").inputValue(), PAYLOAD);
  await page.getByRole("link", { name: `${P_IMG}.md` }).click();
  await page.locator("textarea.editor").waitFor();
  // A run whose frames and result are the payload, and one frame with no event.
  const before = (await starts()).length;
  await page.getByPlaceholder("Say something to helper").fill(PAYLOAD);
  await page.getByRole("button", { name: "Send" }).click();
  let start;
  for (let i = 0; i < 100 && start === undefined; i++) {
    start = (await starts())[before];
    if (start === undefined) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  assert.ok(start, "the run started");
  await report(start, { frames: [
    { seq: 1, at: 1, event: { type: "session", id: "<img/src=x/onerror=window.__xss=1>" } },
    { seq: 2, at: 2, event: { type: PAYLOAD, tool: PAYLOAD } },
    { seq: 3, at: 3, event: PAYLOAD }, { seq: 4, at: 4, event: null }] });
  await page.locator(".frame", { hasText: "onerror" }).first().waitFor();
  await report(start, { frames: [{ seq: 5, at: 5, event: { type: "message_end",
    message: { role: "assistant", content: PAYLOAD } } }], end: { exit_code: 0 } });
  await page.locator(".said.agent", { hasText: "onerror" }).waitFor();
  // The view opens again on its kept frames, the eventless one among them.
  await page.goto(`${ORIGIN}/app#home`);
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.locator(".frame", { hasText: "onerror" }).first().waitFor();
  // A room post, and the listings of the payload names; the hash as an agent and a folder.
  await page.goto(`${ORIGIN}/app#rooms`);
  await page.getByLabel(/^Message/).fill(PAYLOAD);
  await page.getByRole("button", { name: "Post" }).click();
  await page.locator(".message.good", { hasText: "Posted." }).waitFor();
  await page.goto(`${ORIGIN}/app#data`);
  await page.getByRole("heading", { name: "Files in /workspace" }).waitFor();
  for (const hash of [`#agents/${encodeURIComponent(PAYLOAD)}`,
                      `#data/${encodeURIComponent(PAYLOAD)}`, `#agents/${PAYLOAD}`]) {
    await page.goto(`${ORIGIN}/app${hash}`);
    await page.locator("main p.muted", { hasText: "Loading" }).waitFor({ state: "detached" });
  }
  // Nothing a payload made was markup that could run, in any view this document showed.
  assert.deepEqual(await page.evaluate(() => window.__markup), []);
  assert.equal(await page.evaluate(() => window.__xss ?? 0), 0, "a payload ran");
  assert.deepEqual(dialogs, []);
  page.off("dialog", onDialog);
});

test("a malformed hash opens home, with no page error", { timeout: 30_000 },
     async () => {
  await page.goto(`${ORIGIN}/app#data`);
  await page.getByRole("heading", { name: "Your data" }).waitFor();
  await page.goto(`${ORIGIN}/app#agents/%E0%A4%A`);
  await page.getByRole("heading", { name: "Home" }).waitFor();
  assert.deepEqual(errors, []);
});

test("the app refuses to be framed", { timeout: 30_000 }, async () => {
  const evil = await context.newPage();
  await evil.route("https://evil.test/**", (route) => route.fulfill({ status: 200,
    contentType: "text/html", body: `<iframe src="${ORIGIN}/app"></iframe>` +
      `<iframe src="${ORIGIN}/app/connected"></iframe>` }));
  await evil.goto("https://evil.test/");
  // Each frame read once it holds its own document, the app's or the browser's refusal.
  const framed = evil.frames().slice(1);
  assert.equal(framed.length, 2);
  await Promise.all(framed.map((frame) => frame.waitForLoadState("load")));
  for (const frame of framed) {
    const rendered = await frame.evaluate(() => document.getElementById("main") !== null
      || document.getElementById("status") !== null).catch(() => false);
    assert.equal(rendered, false, frame.url());
  }
  await evil.close();
});

test("no cross-site post changes anything (CSRF)", { timeout: 60_000 }, async () => {
  const policy = await api("policy");
  const generation = policy.body.generation;
  // A cross-site page, and a sibling host: that one is same-site, so the Strict cookie rides
  // its posts and only the doors' own check (`sessionOf`) refuses them.
  for (const attacker of ["https://evil.test", "https://evil.tinyassets.io"]) {
    const evil = await context.newPage();
    await evil.route(`${attacker}/**`, (route) => route.fulfill({ status: 200,
      contentType: "text/html", body: "<p>hi</p>" }));
    await evil.goto(`${attacker}/`);
    const body = JSON.stringify({ expected_generation: generation,
                                  policy: { ...policy.body.policy,
                                            codemode: { on: !policy.body.codemode.on } } });
    await evil.evaluate(async ([origin, b]) => {
      for (const init of [
        { mode: "no-cors", credentials: "include", headers: { "Content-Type": "text/plain" } },
        { mode: "cors", credentials: "include",
          headers: { "Content-Type": "application/json" } }]) {
        await fetch(`${origin}/app/api/policy/write`, { method: "POST", body: b, ...init })
          .catch(() => undefined);
      }
      const form = document.createElement("form");
      form.method = "POST";
      form.enctype = "text/plain";
      form.action = `${origin}/app/api/policy/write`;
      const input = document.createElement("input");
      input.name = b.slice(0, -1);
      input.value = "}";
      form.append(input);
      document.body.append(form);
      form.submit();
    }, [ORIGIN, body]);
    // The form's post, the last of the three, answered and shown: then the policy is read.
    await evil.waitForURL(`${ORIGIN}/app/api/policy/write`);
    await evil.close();
    assert.equal((await api("policy")).body.generation, generation, attacker);
  }
});

test("the return pages exchange no stranger's code and send no one off-site", { timeout: 60_000 },
     async () => {
  const probe = await context.newPage();
  const asked = [];
  probe.on("request", (r) => asked.push({ url: r.url(), referer: r.headers()["referer"] ?? null }));
  await probe.goto(`${ORIGIN}/app/signed-in?code=stolen&state=attacker&iss=` +
                   encodeURIComponent("https://evil.test"));
  await probe.locator("#status", { hasText: "not started in this tab" }).waitFor();
  assert.equal(probe.url(), `${ORIGIN}/app/signed-in`);
  assert.ok(!asked.some((r) => r.url.includes("/oauth/token")), "a stranger's code was exchanged");
  const sub = asked.filter((r) => r.url.includes("/app/static/"));
  assert.ok(sub.length > 0 && sub.every((r) => !r.referer));
  await probe.close();
  const from = seen.length;
  await page.goto(`${ORIGIN}/app/connected#state=forged&code=forged-code&redirect_uri=` +
                  encodeURIComponent("https://evil.test/x"));
  await page.locator("#status", { hasText: "not finished" }).waitFor();
  assert.equal(page.url(), `${ORIGIN}/app/connected`);
  assert.ok(!seen.slice(from).some((r) => r.url.startsWith("https://evil.test")));
});

test("the sign-in return page says each refusal's reason in words, with what to do, only for " +
     "a sign-in its tab began", { timeout: 60_000 }, async () => {
  const probe = await context.newPage();
  // This tab began a sign-in of state `s1`, as the app does before it leaves for /authorize.
  await probe.addInitScript(() => sessionStorage.setItem("ta.app.signin",
    JSON.stringify({ state: "s1", verifier: "v" })));
  const again = "Go back to the app to try again.";
  const shown = async (query) => {
    await probe.goto(`${ORIGIN}/app/signed-in?${new URLSearchParams(query)}`);
    const status = probe.locator("#status");
    await status.filter({ hasText: again }).waitFor();
    return status.textContent();
  };
  const upstream = "the sign-in was refused: access_denied: The user cancelled";
  for (const [error, reason, said] of [
    ["temporarily_unavailable", CANARY_ONLY, "This account can't use TinyAssets here for now: " +
      "only the accounts this deployment is being tested with can (on the stage, sign in with " +
      "a stage account)."],
    ["temporarily_unavailable", MAINTENANCE, "TinyAssets is in maintenance."],
    ["access_denied", STALE_SIGN_IN, "The sign-in is too old to use: sign in again."],
    ["access_denied", ERASED_SIGN_IN, "This account was erased, so it can't sign in again: " +
      "sign in with another account."],
    ["access_denied", upstream, "The sign-in was refused: access_denied: The user cancelled."],
    ["access_denied", null, "The sign-in was cancelled or refused."],
    ["temporarily_unavailable", null, "TinyAssets can't finish a sign-in right now."],
    ["server_error", null, "The sign-in was not finished (server_error)."],
  ]) {
    const query = { error, state: "s1", ...reason === null ? {} : { error_description: reason } };
    assert.equal(await shown(query), `${said} ${again}`, `${error}: ${reason}`);
  }
  // A link made elsewhere, of no sign-in this tab began: its words are never shown.
  assert.equal(await shown({ error: "access_denied", state: "forged",
                             error_description: "call this number to unlock your account" }),
               `This sign-in was not started in this tab. ${again}`);
  await probe.close();
});

test("a leaked token alone changes no agent's definition, and a missing door is a real 404",
     { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#home`);
  await page.getByRole("heading", { name: "Home" }).waitFor();
  const read = await bare("files/read", { path: "/workspace/agents/main/agent.json" });
  assert.equal(read.status, 200);
  const widened = { ...JSON.parse(read.body.text),
                    scopes: ["read_graph", "write_graph", "read_files", "write_files",
                             "propose_policy", "converse", "run_graph"] };
  const SESSION = { error: "interactive owner session required" };
  const wrote = await bare("files/write", { path: "/workspace/agents/main/agent.json",
    expected_etag: read.body.entry.etag, text: JSON.stringify(widened) });
  assert.deepEqual([wrote.status, wrote.body], [403, SESSION]);
  const removed = await bare("tools/file_remove", { path: "/workspace/agents/main/AGENTS.md",
                                                    expected_etag: null, recursive: false });
  assert.deepEqual([removed.status, removed.body], [403, SESSION]);
  assert.equal((await bare("files/read", { path: "/workspace/agents/main/agent.json" }))
    .body.entry.etag, read.body.entry.etag);
  // The policy is the session's too; a revoke goes by grants/revoke alone.
  const policy = await api("policy");
  assert.equal((await bare("policy/write", { expected_generation: policy.body.generation,
                                             policy: policy.body.policy })).status, 403);
  // A name the table lacks is the doors' own 404, never a planned gap.
  for (const name of ["__proto__", "constructor", "nope"]) {
    assert.deepEqual(await api(`tools/${name}`), { status: 404, body: { error: "no such tool" } });
  }
  // An API key or an outside client's token never reaches the owner's surface.
  for (const path of ["account", "policy", "files/read", "tools/file_list",
                      "agent/doorbell/kinds", "sources", "tools", "capabilities"]) {
    const answer = await mf.dispatchFetch(`${ORIGIN}/app/api/${path}`, { method: "POST",
      headers: { "Content-Type": "application/json",
                 Authorization: "Bearer ta_key_0123456789abcdef_x" }, body: "{}" });
    assert.equal(answer.status, 403, path);
  }
});

/** How many JSON answers the page has parsed (`__parsed`). */
const parsedSoFar = () => page.evaluate(() => window.__parsed.length);

/**
 * Once the page parsed, after the `from`th answer, each step's answers, every step after
 * the one before it, and the code awaiting them had its turn.
 */
async function handled(from, ...steps) {
  await page.waitForFunction(([n, wanted]) => {
    let at = n;
    for (const step of wanted) {
      const found = step.map((path) => window.__parsed.findIndex((url, i) =>
        i >= at && new URL(url).pathname === path));
      if (found.includes(-1)) {
        return false;
      }
      at = Math.max(...found) + 1;
    }
    return true;
  }, [from, steps]);
}

/**
 * `body` run with the answers to `pattern` held until its `release()`: each request reaches
 * the doors as it comes, and its answer, as they gave it then, reaches the page only after
 * the release. `first` resolves once the first answer is held.
 *
 * The route lives as long as `body`, which waits on what the released answers made the page
 * do: Playwright loses a request the page makes while a route is being taken away (never
 * sent, never answered), so the route never goes while a released answer is still being
 * acted on. Taking it away first is no better: that lets a held request go on to the doors a
 * second time.
 */
async function holding(pattern, body) {
  let release;
  const released = new Promise((resolve) => { release = resolve; });
  let held;
  const first = new Promise((resolve) => { held = resolve; });
  const going = new Set();
  const handler = async (route) => {
    const request = route.request();
    const headers = Object.fromEntries(Object.entries(await request.allHeaders())
      .filter(([name]) => !name.startsWith(":")));
    const sent = (async () => {
      const answer = await mf.dispatchFetch(request.url(), { method: request.method(), headers,
        body: request.postDataBuffer() ?? undefined });
      const bytes = Buffer.from(await answer.arrayBuffer());
      held();
      await released;
      // The body as read: its length and encoding are the fulfilment's own.
      await route.fulfill({ status: answer.status, body: bytes, headers: Object.fromEntries(
        [...answer.headers].filter(([name]) => !/^content-(?:length|encoding)$/.test(name))) });
    })();
    going.add(sent);
    await sent;
  };
  await page.route(pattern, handler);
  const answered = async () => {
    release();
    await Promise.all(going);
  };
  try {
    await body({ first, release: answered });
  } finally {
    await answered();
    await page.unroute(pattern, handler);
  }
}

test("an agent whose agent.json is refused keeps its page, its runs' lines saying why",
     { timeout: 60_000 }, async () => {
  const path = "/workspace/agents/helper/agent.json";
  const read = await api("files/read", { path });
  const broke = await api("files/write", { path, expected_etag: read.body.entry.etag, text: "{" });
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.locator("main p", { hasText: "A run on your schedule, now: refused, " }).waitFor();
  await page.getByRole("link", { name: "agent.json" }).waitFor();
  const lines = await page.locator("main p", { hasText: ": refused, " }).allInnerTexts();
  assert.deepEqual(lines.map((line) => line.split(": refused, ")[0]),
                   ["A run you start now", "A run on your schedule, now"], lines.join(" | "));
  assert.equal((await api("files/write", { path, expected_etag: broke.body.entry.etag,
                                           text: read.body.text })).status, 200);
});

test("a save re-lists its own folder, never the folder the owner went to since",
     { timeout: 60_000 }, async () => {
  const folder = "/workspace/agents/helper";
  await page.goto(`${ORIGIN}/app#data/${encodeURIComponent(folder)}`);
  await page.getByRole("heading", { name: `Files in ${folder}` }).waitFor();
  await page.getByRole("link", { name: "AGENTS.md" }).click();
  await page.locator("textarea.editor").waitFor();
  await holding("**/app/api/files/write", async (save) => {
    await page.getByRole("button", { name: "Save" }).click();
    await save.first;
    // The owner moves on while the save is in flight; then it lands, and re-lists its folder.
    await page.goto(`${ORIGIN}/app#data`);
    await page.getByRole("heading", { name: "Files in /workspace" }).waitFor();
    const relisted = page.waitForResponse((response) =>
      response.url().endsWith("/app/api/tools/file_list")
      && response.request().postData()?.includes("agents/helper"));
    const from = await parsedSoFar();
    await save.release();
    await page.locator(".message.good", { hasText: "Saved" }).waitFor();
    await relisted;
    // The save's answer, then the re-list's two, handled.
    await handled(from, ["/app/api/files/write"],
                  ["/app/api/tools/file_list", "/app/api/account"]);
  });
  assert.equal(await page.getByRole("heading", { name: "Files in /workspace" }).count(), 1);
  assert.equal(await page.getByRole("heading", { name: `Files in ${folder}` }).count(), 0);
});

test("a screen still loading never paints over the signed-out view",
     { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#home`);
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
  await holding("**/app/api/grants", async (grants) => {
    const from = await parsedSoFar();
    await page.goto(`${ORIGIN}/app#keys`);
    await grants.first;
    // Signed out while API keys still loads; then its answer comes.
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByRole("heading", { name: "Your TinyAssets" }).waitFor();
    const answered = page.waitForResponse((response) =>
      response.url().endsWith("/app/api/grants"));
    await grants.release();
    await answered;
    // The keys screen's answer handled.
    await handled(from, ["/app/api/grants"]);
  });
  assert.equal(await page.getByRole("heading", { name: "Your TinyAssets" }).count(), 1);
  assert.equal(await page.getByRole("heading", { name: "API keys", exact: true }).count(), 0);
});

test("the app made no third-party request and threw nothing", () => {
  assert.deepEqual(errors, []);
  assert.deepEqual(outside, []);
  // The app's own tab: only the two hand-offs the owner is sent to leave the origin, each a
  // top-level navigation (the attack tests' own pages are theirs).
  const elsewhere = seen.filter((r) => r.main && !r.url.startsWith(`${ORIGIN}/`));
  for (const request of elsewhere) {
    assert.ok(request.navigation && request.type === "document", request.url);
    assert.ok(request.url.startsWith(`${UPSTREAM}/authorize?`)
              || request.url.startsWith(`${SOURCE_AUTH}/authorize?`), request.url);
  }
  assert.ok(seen.some((r) => r.url.startsWith(`${ORIGIN}/app/static/app.js`)));
});
