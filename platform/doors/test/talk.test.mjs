// The Talk page in Chromium with the owner's answers and socket deliveries held at the
// browser boundary. Navigating away and back replaces the view while its talk remains.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, before, test } from "node:test";
import { chromium } from "playwright";

const origin = "https://doors.test";
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function talk(t) {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.route("**/*", async (route) => {
    const { origin: host, pathname } = new URL(route.request().url());
    const file = pathname === "/app" ? "index.html" : pathname.slice("/app/static/".length);
    if (host !== origin || !["index.html", "app.js", "api.js", "view.js", "app.css"].includes(file)) {
      return route.abort();
    }
    const contentType = file.endsWith(".js") ? "text/javascript" :
      file.endsWith(".css") ? "text/css" : "text/html";
    await route.fulfill({ contentType, body: await readFile(new URL(`../app/${file}`, import.meta.url)) });
  });
  await page.addInitScript(() => {
    sessionStorage.setItem("ta.app.tokens", JSON.stringify({ access_token: "local",
      expires_at: Date.now() + 60_000 }));
    let submits = 0;
    window.reads = [];
    window.WebSocket = class extends EventTarget {
      readyState = 1;
      constructor() { super(); window.liveSocket = this; }
    };
    window.fetch = async (url, options) => {
      const path = url.slice("/app/api/".length);
      const body = JSON.parse(options.body);
      if (path === "tools/file_list") {
        return Response.json({ entries: [] });
      }
      if (path === "agent/socket-ticket") {
        return Response.json({ ticket: "local-ticket" });
      }
      const run = { run_id: body.run_id, conversation_id: "c", state: "running" };
      if (path === "agent/submit") {
        return Response.json({ ...run, run_id: `r${++submits}` });
      }
      if (path === "agent/cancel") {
        return new Promise((resolve) => {
          window.refuseCancel = () => resolve(Response.json({ error: "cancel unavailable" },
                                                             { status: 503 }));
        });
      }
      if (path !== "agent/status") {
        throw Error(`unexpected request: ${path}`);
      }
      window.reads.push(body.run_id);
      if (window.reads.length === 1) {
        return new Promise((resolve) => {
          window.release = (answer) => resolve(Response.json({ ...run, ...answer }));
        });
      }
      return Response.json({ ...run, state: "succeeded", result: "latest answer" });
    };
  });
  await page.goto(`${origin}/app#agents/one`);
  const first = await page.getByRole("button", { name: "Send" }).elementHandle();
  await page.getByRole("textbox", { name: "Message" }).fill("first");
  await first.click();
  await page.waitForFunction(() => typeof window.release === "function");
  return { page, first };
}

async function visit(page, name) {
  await page.evaluate((agent) => { location.hash = `agents/${agent}`; }, name);
  await page.getByRole("heading", { name, exact: true }).waitFor();
}

test("a late status for the previous run cannot replace the current talk", async (t) => {
  const { page, first } = await talk(t);
  await visit(page, "two");
  await visit(page, "one");
  await page.getByRole("textbox", { name: "Message" }).fill("second");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText("latest answer", { exact: true }).waitFor();
  await page.evaluate(() => window.release({ state: "failed", error: "earlier answer" }));
  await page.waitForFunction((button) => !button.disabled, first);
  await visit(page, "two");
  await visit(page, "one");
  assert.equal(await page.getByText("Run succeeded", { exact: true }).count(), 1);
  assert.equal(await page.getByText("latest answer", { exact: true }).count(), 1);
  assert.deepEqual(await page.evaluate(() => window.reads), ["r1", "r2"]);
});

test("frames during one status read are followed by one fresh receipt", async (t) => {
  const { page, first } = await talk(t);
  const before = await page.evaluate(() => {
    for (const seq of [1, 2, 3]) {
      window.liveSocket.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({
        run: "r1", seq, kind: "result", event: { result: "a frame" } }) }));
    }
    return [...window.reads];
  });
  assert.deepEqual(before, ["r1"]);
  await page.evaluate(() => window.release({ state: "running" }));
  await page.waitForFunction((button) => !button.disabled, first);
  assert.deepEqual(await page.evaluate(() => window.reads), ["r1", "r1"]);
  assert.equal(await page.getByText("Run succeeded", { exact: true }).count(), 1);
  assert.equal(await page.getByText("latest answer", { exact: true }).count(), 1);
});

test("a refusal to cancel the preceding run does not report a failure for the current run",
     async (t) => {
  const { page, first } = await talk(t);
  const stop = await page.getByRole("button", { name: "Stop this run" }).elementHandle();
  await stop.click();
  await page.waitForFunction(() => typeof window.refuseCancel === "function");
  await visit(page, "two");
  await visit(page, "one");
  await page.getByRole("textbox", { name: "Message" }).fill("second");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText("latest answer", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.refuseCancel();
    window.release({ state: "failed" });
  });
  await page.waitForFunction((button) => !button.disabled, stop);
  await page.waitForFunction((button) => !button.disabled, first);
  assert.equal(await page.locator("#notice").textContent(), "");
  assert.equal(await page.getByText("Run succeeded", { exact: true }).count(), 1);
});
