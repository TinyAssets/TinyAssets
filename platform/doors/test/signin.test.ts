/**
 * The vendor's own sign-in through the doors (slice SIGNIN), in workerd through the real owner
 * object (its `SANDBOX` a recording fake, `entry.ts`): the owner app's `signin/*` routes, and
 * the sign-in's event door on the egress host, by the egress token E2B's transform sets and
 * the runner's secret, the prompt it posts reaching the owner's sign-in list; and the past
 * runs' route.
 */
import { env, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import { EGRESS_ORIGIN, GENERATION_HEADER, SANDBOX_HEADER } from "../../contract/src/egress";
import { bundledRecords, catalogOf } from "../../sources/src/catalog";
import type { OwnerDO } from "./entry";
import { appPost, call, OPEN, serveFirstPartyDocument, setControl, signInApp } from "./support";
import type { SandboxSignin } from "../../contract/src/sandbox";

let restore: () => void = () => undefined;

/** Claude's sign-in page as its pinned CLI shows it (template s6dec61a38e839d37, live). */
const CLAUDE_PAGE = "https://claude.com/cai/oauth/authorize?code=true&client_id=" +
  "9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2F" +
  "platform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+" +
  "user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload+" +
  "user%3Aplugins&code_challenge=yfhUky71rorbPUGR_0kuaJoQPT0k39HLxYDNc-gUtt8&" +
  "code_challenge_method=S256&state=Lq7TdjukIEmxyMX2EKf2hY4xChr1sfIJOV3krpnv6iE";

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

async function signedIn() {
  const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
  const app = await signInApp({ owner: who });
  return { who, app };
}

describe("the sign-in routes and door", () => {
  it("start a sign-in, take the runner's prompt on its door, and list it", async () => {
    const { who, app } = await signedIn();
    const started = await appPost("signin/start", app.access_token, null,
                                  { engine: "claude-code" });
    expect(started.status).toBe(200);
    const view = await started.json() as { id: string; state: string };
    expect(view.state).toBe("starting");
    const [asked] = await runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
      (instance as OwnerDO).recorder.signins) as SandboxSignin[];
    const inside = { [SANDBOX_HEADER]: asked!.tokens!.egress_token,
                     [GENERATION_HEADER]: String(asked!.network!.generation),
                     "x-ta-runner": asked!.tokens!.runner_token };
    const url = CLAUDE_PAGE;
    const posted = await call(new Request(`${EGRESS_ORIGIN}/signin/${view.id}/events`, {
      method: "POST", headers: { "Content-Type": "application/json", ...inside },
      body: JSON.stringify({ frames: [{ seq: 1, at: 1, event: {
        type: "ta.signin", state: "open", url, code: null, accepts_code: true } }] }) }));
    expect(posted.status).toBe(200);
    expect(await posted.json()).toEqual({ accepted: 1, ended: false });
    const listed = await (await appPost("signin/list", app.access_token, null, {})).json() as {
      signins: { id: string; url: string; accepts_code: boolean }[] };
    expect(listed.signins).toEqual([expect.objectContaining({ id: view.id, url,
                                                               accepts_code: true })]);
    const pasted = await appPost("signin/code", app.access_token, null,
                                 { id: view.id, code: "abc#def" });
    expect(await pasted.json()).toEqual({ delivered: true });
    const cancelled = await appPost("signin/cancel", app.access_token, null, { id: view.id });
    expect(await cancelled.json()).toEqual({ cancelled: true });
  });

  it("refuse the sign-in door without the runner's secret, or from outside the sandbox",
     async () => {
    const { who, app } = await signedIn();
    const view = await (await appPost("signin/start", app.access_token, null,
                                      { engine: "claude-code" })).json() as { id: string };
    const [asked] = await runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
      (instance as OwnerDO).recorder.signins) as SandboxSignin[];
    const body = JSON.stringify({ frames: [] });
    const at = `${EGRESS_ORIGIN}/signin/${view.id}/events`;
    for (const headers of [
      { [SANDBOX_HEADER]: asked!.tokens!.egress_token,
        [GENERATION_HEADER]: String(asked!.network!.generation) },
      { "x-ta-runner": asked!.tokens!.runner_token }]) {
      const refused = await call(new Request(at, { method: "POST", body,
        headers: { "Content-Type": "application/json", ...headers } }));
      expect(refused.status).toBe(401);
    }
  });

  it("keep each source's sign-in behind its own switch: off by its verdict, on by the record",
     async () => {
    const { app } = await signedIn();
    const off = await appPost("signin/start", app.access_token, null, { engine: "codex" });
    expect(off.status).toBe(403);
    expect(await off.text()).toMatch(/signin_off/);
    const listed = await (await appPost("sources", app.access_token, null, {})).json() as {
      sources: { id: string; signin: { enabled: boolean; hosted_use: { verdict: string } }
                 | null }[] };
    const by = new Map(listed.sources.map((source) => [source.id, source.signin]));
    expect(by.get("claude-code")).toMatchObject({ enabled: true,
                                                  hosted_use: { verdict: "allowed" } });
    for (const id of ["codex", "gemini", "kimi"]) {
      expect(by.get(id)).toMatchObject({ enabled: false });
    }
    await setControl({ ...OPEN, signins: { codex: true, "claude-code": false } });
    expect((await appPost("signin/start", app.access_token, null,
                          { engine: "codex" })).status).toBe(200);
    const claude = await appPost("signin/start", app.access_token, null,
                                 { engine: "claude-code" });
    expect(claude.status).toBe(403);
    expect(await claude.text()).toMatch(/signin_off/);
  });

  it("read each switch off the catalog the doors serve, in the list and in the binding (D5)",
     async () => {
    // A served catalog unlike the bundled one: Codex's sign-in allowed by its verdict, and
    // one more record that signs in, off by its own until the record switches it on.
    const codex = (records: readonly unknown[], id: string, verdict: string) => {
      const record = structuredClone(records.find((each) =>
        (each as { id: string }).id === "codex")) as {
          id: string; cli: { login: { hosted_use: { verdict: string } } } };
      record.id = id;
      record.cli.login.hosted_use.verdict = verdict;
      return record;
    };
    const bundled = bundledRecords();
    const served = catalogOf([
      ...bundled.map((each) => (each as { id: string }).id === "codex"
        ? codex(bundled, "codex", "allowed") : each),
      codex(bundled, "test_login", "unclear")]);
    const { app } = await signedIn();
    const listedOn = async () => {
      const listed = await (await appPost("sources", app.access_token, null, {}, {}, served))
        .json() as { sources: { id: string; signin: { enabled: boolean } | null }[] };
      return new Map(listed.sources.map((source) => [source.id, source.signin?.enabled]));
    };
    const first = await listedOn();
    expect([first.get("codex"), first.get("test_login")]).toEqual([true, false]);
    // The binding the object holds a sign-in to carries the same switch.
    expect((await appPost("signin/start", app.access_token, null, { engine: "codex" }, {},
                          served)).status).toBe(200);
    await setControl({ ...OPEN, signins: { test_login: true } });
    expect((await listedOn()).get("test_login")).toBe(true);
  });

  it("always let a source switched off be signed out, so its login can be removed (review H1)",
     async () => {
    const { app } = await signedIn();
    // The founder turns Codex off (its vendor's terms turn out to forbid hosting it).
    await setControl({ ...OPEN, signins: { codex: false } });
    const signin = await appPost("signin/start", app.access_token, null, { engine: "codex" });
    expect(signin.status).toBe(403);
    expect(await signin.text()).toMatch(/signin_off/);
    const out = await appPost("signin/start", app.access_token, null,
                              { engine: "codex", mode: "out" });
    expect(out.status).toBe(200);
    expect(await out.json()).toMatchObject({ engine: "codex", mode: "out" });
  });

  it("list the owner's past runs", async () => {
    const { app } = await signedIn();
    const listed = await appPost("runs", app.access_token, null, {});
    expect(listed.status).toBe(200);
    expect(await listed.json()).toEqual({ runs: [], next: null });
  });
});
