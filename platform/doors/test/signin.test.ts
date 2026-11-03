/**
 * The vendor's own sign-in through the doors (slice SIGNIN), in workerd through the real owner
 * object (its `SANDBOX` a recording fake, `entry.ts`): the owner app's `signin/*` routes, and
 * the sign-in's event door on the egress host, by the egress token E2B's transform sets and
 * the runner's secret, the prompt it posts reaching the owner's sign-in list; and the past
 * runs' route.
 */
import { env, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

import { GENERATION_HEADER, SANDBOX_HEADER } from "../../owner/src/network";
import type { SandboxSignin } from "../../owner/src/sandbox";
import { EGRESS_ORIGIN } from "../src/config";
import type { OwnerDO } from "./entry";
import { appPost, call, OPEN, serveFirstPartyDocument, setControl, signInApp } from "./support";

let restore: () => void = () => undefined;

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
    const url = "https://claude.ai/oauth/authorize?code=true&client_id=c1&state=s1";
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
    expect((await appPost("signin/start", app.access_token, null,
                          { engine: "claude-code" })).status).toBe(403);
  });

  it("list the owner's past runs", async () => {
    const { app } = await signedIn();
    const listed = await appPost("runs", app.access_token, null, {});
    expect(listed.status).toBe(200);
    expect(await listed.json()).toEqual({ runs: [], next: null });
  });
});
