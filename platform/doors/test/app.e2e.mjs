// The owner app in a real browser (Chromium, through Playwright), against the doors in
// workerd: the doors' test Worker (`e2e-entry.ts`: the fake upstream sign-in, the real
// owner object with a recording sandbox) with the real gateway, codemode and files Workers and
// the fake providers, each bundled by wrangler from its own config as `vitest.config.ts`
// does (`ci/workers.ts`). The files Worker is bound as production binds it: the doors' FILES its upload
// entrypoint, the owner object's (OWNER_FILES here) its whole service, so a new owner's
// starter seed really writes its files.
//
// The browser believes it talks to https://tinyassets.io: every request it makes is caught
// (`context.route`) and handed to the doors with `dispatchFetch`, the app's live socket
// likewise (`routeWebSocket`). Two other hosts answer, each a top-level navigation by
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

import { keyring as newKeyring, OPEN, writeControl } from "../../ci/fixtures.ts";
import { PLATFORM } from "../../ci/paths.ts";
import { auxiliary, fake, pool, workerConfig } from "../../ci/workers.ts";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
const ORIGIN = "https://tinyassets.io";
const EGRESS = "https://egress.tinyassets.io";
const UPSTREAM = "https://upstream.test";
const SOURCE_AUTH = "https://auth.test";
const WHO = `user_${randomBytes(16).toString("hex")}`;
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
  const gateway = workerConfig(join(PLATFORM, "gateway"));
  const codemode = workerConfig(join(PLATFORM, "codemode"));
  const files = workerConfig(join(PLATFORM, "files"));
  const keyring = newKeyring();
  const webClient = `${ORIGIN}/oauth/clients/web.json`;
  mf = new Miniflare(convertV4MiniflareOptions(pool({
    host: "127.0.0.1", port: 0, https: true,
    workers: [
      // The doors' test Worker, with everything the doors' config gives it (`auxiliary`):
      // its secrets are the test's, none read from the host.
      auxiliary(DOORS, {
        bindings: { OWNER_ISSUER: UPSTREAM,
                    ROUTING_MAC_KEY: randomBytes(32).toString("base64url"),
                    WORKOS_API_KEY: "test-client-secret",
                    E2B_WEBHOOK_SECRET: "test-e2b-webhook-signature-secret" },
        durableObjects: { OWNER: { className: "OwnerDO", useSQLite: true } },
        // CODEMODE_VM and OWNER_FILES: the owner object's, in this script for the test.
        serviceBindings: { CODEMODE_VM: codemode.name, PROVIDERS: "fake-providers",
                           OWNER_FILES: files.name },
        // The provider fetches the first-party app's metadata document, as production does
        // over the public internet; nothing else leaves the doors.
        outboundService: (request) => request.url === webClient ? mf.dispatchFetch(webClient)
          : new Response(`the doors reach no ${request.url} here`, { status: 502 }),
      }, "test/e2e-entry.ts"),
      auxiliary(join(PLATFORM, "gateway"), { bindings: { GATEWAY_KEYRING: keyring },
                                             outboundService: "fake-providers" }),
      // The files Worker: its config's bucket and cipher, and a test keyring.
      auxiliary(join(PLATFORM, "files"), { bindings: { FILES_KEYRING: keyring } }),
      auxiliary(join(PLATFORM, "codemode"), {}),
      fake(join(DOORS, "test", "fake-providers.ts")),
      {
        // The two hand-off hosts, each answering the browser's top-level navigation at once.
        name: "hand-off", routes: ["upstream.test/*", "auth.test/*"],
        bindings: { WHO }, compatibilityDate: gateway.compatibility_date,
        modules: true, script: HAND_OFF,
      },
    ],
  })));
  await mf.ready;
  const control = await mf.getKVNamespace("CONTROL", "tinyassets-doors");
  await writeControl(control, JSON.stringify(OPEN));
}

before(async () => {
  await doors();
  const port = new URL(await mf.ready).port;
  // No proxy: the browser reaches only the local workerd, by the rules below.
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([name]) => !/proxy/i.test(name)));
  const local = ["tinyassets.io", "egress.tinyassets.io", "upstream.test", "auth.test"]
    .map((host) => `MAP ${host}:443 127.0.0.1:${port}`).join(", ");
  browser = await chromium.launch({ env, args: [
    "--no-proxy-server",
    // workerd's self-signed certificate, accepted by the browser's network stack itself:
    // the context's `ignoreHTTPSErrors` alone left each WebSocket's first TLS handshake
    // refused (`CertificateUnknown`), and now and then the socket itself died before it
    // opened (1006), so the sign-in panel never saw its end (the merge queue's red run).
    "--ignore-certificate-errors",
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

/** The lease's egress token: only the network's transform carries it, as E2B sets it. */
function egressOf(opened) {
  return opened.network.network.rules["egress.tinyassets.io"][0].transform.headers["x-ta-sandbox"];
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
  const answer = await mf.dispatchFetch(`${EGRESS}/r/${start.run}/events`, {
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
  await connection.selectOption(groq);
  await form.getByRole("checkbox", { name: "Read your files" }).check();
  // Every change the making sends, in order: the agent is its agent.json, so that is the
  // last write, after its folder and its AGENTS.md (review L3).
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
  assert.match(await page.locator("main").innerText(), /Run succeeded on pi/);
  await shot("agent");
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
  // Kimi signs in only: switched off (its terms unverified), the form says so and offers it
  // not; switched on in the control record, it is an engine a Kimi agent is made on.
  await page.goto(`${ORIGIN}/app#agents`);
  await page.getByRole("heading", { name: "New agent" }).waitFor();
  assert.match(await page.locator("section", { hasText: "New agent" }).innerText(),
               /switched off for [^.]*Kimi/);
  const control = await mf.getKVNamespace("CONTROL", "tinyassets-doors");
  await writeControl(control, JSON.stringify({ ...OPEN, signins: { kimi: true } }));
  try {
    await page.goto(`${ORIGIN}/app#home`);
    await page.goto(`${ORIGIN}/app#agents`);
    const again = page.locator("section", { hasText: "New agent" });
    await again.getByLabel(/^Engine/).selectOption("kimi");
    assert.deepEqual(await again.getByLabel(/^Runs on/).locator("option").allInnerTexts(),
                     ["Whichever the engine has",
                      "Your subscription, signed in inside your sandbox"]);
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
    const answer = await mf.dispatchFetch(`${EGRESS}/signin/${signin.id}/events`, {
      method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json",
                 "x-ta-sandbox": egressOf(signin),
                 "x-ta-network-generation": String(signin.network.generation),
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
  // A look-alike page is refused in plain words (review L6), and the panel offers a new one.
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
  // A code past its lifetime ends the sign-in, and the app offers to start again (review M4).
  await again(2);
  await post({ frames: [{ seq: 2, at: 2, event: { type: "ta.signin", state: "expired" } }] });
  await page.locator(".message.bad", { hasText: "The code expired; start again." }).waitFor();
  await card.getByRole("button", { name: /^Sign in/ }).waitFor();
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
    const answer = await mf.dispatchFetch(`${EGRESS}/signin/${signin.id}/events`, {
      method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json",
                 "x-ta-sandbox": egressOf(signin),
                 "x-ta-network-generation": String(signin.network.generation),
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

test("a talk whose socket drops mid-run goes on on a new socket, each frame shown once " +
     "(review M1)", { timeout: 120_000 }, async () => {
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
  // A key, shown once.
  await page.goto(`${ORIGIN}/app#keys`);
  await page.getByLabel(/^Name/).fill("laptop");
  await page.getByRole("checkbox", { name: "Read your files" }).check();
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
  // No main agent has joined, so no wake-up is offered.
  await page.goto(`${ORIGIN}/app#apps`);
  await page.getByText("No main agent has joined yet.").waitFor();
  // "Every tool" holds every pick on, fixed, so no choice is silently dropped (review L5).
  const picks = page.locator("section", { hasText: "Tools your connected apps see" });
  assert.equal(await picks.getByRole("checkbox", { name: /Every tool/ }).isChecked(), true);
  assert.equal(await picks.getByRole("checkbox", { name: "List agents" }).isDisabled(), true);
  await picks.getByRole("checkbox", { name: /Every tool/ }).uncheck();
  assert.equal(await picks.getByRole("checkbox", { name: "List agents" }).isDisabled(), false);
  // The doors this app knows are missing say so before anything is clicked, with no button;
  // past runs are said plainly under Talk (review M3).
  await page.goto(`${ORIGIN}/app#data`);
  await page.getByText(/^Exporting everything you hold is not here yet/).waitFor();
  assert.equal(await page.getByRole("button", { name: /Export/ }).count(), 0);
  await page.goto(`${ORIGIN}/app#agents/helper`);
  await page.getByText(/not kept across reloads/).waitFor();
});

// -- The review's attacks, kept as tests (slice APP review) ---------------------------------

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
    await new Promise((resolve) => setTimeout(resolve, 300));
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
  // A run whose frames and result are the payload, and one frame with no event (review L2).
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
  await new Promise((resolve) => setTimeout(resolve, 500));
  assert.equal(await page.evaluate(() => window.__xss ?? 0), 0, "a payload ran");
  assert.deepEqual(dialogs, []);
  page.off("dialog", onDialog);
});

test("a malformed hash opens home, with no page error (review L1)", { timeout: 30_000 },
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
  await new Promise((resolve) => setTimeout(resolve, 1500));
  for (const frame of evil.frames().slice(1)) {
    const rendered = await frame.evaluate(() => document.getElementById("main") !== null
      || document.getElementById("status") !== null).catch(() => false);
    assert.equal(rendered, false, frame.url());
  }
  await evil.close();
});

test("no cross-site post changes anything (CSRF)", { timeout: 60_000 }, async () => {
  const policy = await api("policy");
  const generation = policy.body.generation;
  const evil = await context.newPage();
  await evil.route("https://evil.test/**", (route) => route.fulfill({ status: 200,
    contentType: "text/html", body: "<p>hi</p>" }));
  await evil.goto("https://evil.test/");
  const body = JSON.stringify({ expected_generation: generation,
                                policy: { ...policy.body.policy, codemode: { on: true } } });
  await evil.evaluate(async ([origin, b]) => {
    for (const init of [
      { mode: "no-cors", credentials: "include", headers: { "Content-Type": "text/plain" } },
      { mode: "cors", credentials: "include", headers: { "Content-Type": "application/json" } }]) {
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
  await evil.waitForLoadState("load").catch(() => undefined);
  await new Promise((resolve) => setTimeout(resolve, 1000));
  await evil.close();
  assert.equal((await api("policy")).body.generation, generation);
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

test("a leaked token alone changes no agent's definition, and a missing door is a real 404 " +
     "(review M1, M3)", { timeout: 60_000 }, async () => {
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

/** Requests to `pattern` held until `release()`; `seen` resolves when the first one comes. */
async function holding(pattern) {
  let release;
  const released = new Promise((resolve) => { release = resolve; });
  let seen;
  const first = new Promise((resolve) => { seen = resolve; });
  const going = new Set();
  const handler = async (route) => {
    seen();
    const sent = released.then(() => route.continue());
    going.add(sent);
    await sent;
  };
  await page.route(pattern, handler);
  return { first, release: async () => {
    release();
    // Every request held goes on before the route is taken away, so none is handled twice.
    await Promise.all(going);
    await page.unroute(pattern, handler);
  } };
}

test("a save re-lists its own folder, never the folder the owner went to since (review L2)",
     { timeout: 60_000 }, async () => {
  const folder = "/workspace/agents/helper";
  await page.goto(`${ORIGIN}/app#data/${encodeURIComponent(folder)}`);
  await page.getByRole("heading", { name: `Files in ${folder}` }).waitFor();
  await page.getByRole("link", { name: "AGENTS.md" }).click();
  await page.locator("textarea.editor").waitFor();
  const save = await holding("**/app/api/files/write");
  await page.getByRole("button", { name: "Save" }).click();
  await save.first;
  // The owner moves on while the save is in flight; then it lands, and re-lists its folder.
  await page.goto(`${ORIGIN}/app#data`);
  await page.getByRole("heading", { name: "Files in /workspace" }).waitFor();
  const relisted = page.waitForResponse((response) =>
    response.url().endsWith("/app/api/tools/file_list")
    && response.request().postData()?.includes("agents/helper"));
  await save.release();
  await page.locator(".message.good", { hasText: "Saved" }).waitFor();
  await relisted;
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(await page.getByRole("heading", { name: "Files in /workspace" }).count(), 1);
  assert.equal(await page.getByRole("heading", { name: `Files in ${folder}` }).count(), 0);
});

test("a screen still loading never paints over the signed-out view (review L1)",
     { timeout: 60_000 }, async () => {
  await page.goto(`${ORIGIN}/app#home`);
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
  const grants = await holding("**/app/api/grants");
  await page.goto(`${ORIGIN}/app#keys`);
  await grants.first;
  // Signed out while API keys still loads; then its answer comes.
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("heading", { name: "Your TinyAssets" }).waitFor();
  const answered = page.waitForResponse((response) =>
    response.url().endsWith("/app/api/grants"));
  await grants.release();
  await answered;
  await new Promise((resolve) => setTimeout(resolve, 200));
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
