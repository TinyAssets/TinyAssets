/**
 * The vendor's own sign-in inside a sandbox (slice SIGNIN; `signins.ts`), in workerd with the
 * recording fake behind `SANDBOX`: the owner's door opens the sandbox's lease with no run and
 * asks the sandbox Worker to start the record's login; the runner's `ta.signin` frames keep
 * the vendor's prompt, renew the lease and reach the owner's app socket; a pasted code goes
 * back; the end lets the lease go. A client's sandbox signs in only inside itself. And the
 * owner's past runs, newest first, a page at a time, held to the caller's grant.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { NO_SOURCE, RUN_ENDED } from "../src/runs";
import type { SandboxSignin, SandboxStart } from "../src/sandbox";
import { WriteGate } from "../src/gate";
import { NO_CODE_WANTED, SIGNIN_LIVE, SIGNIN_OFF, Signins } from "../src/signins";
import { gated, type Gated } from "./gateway-support";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { defineAgent, ownerRun, recorder, runnerBinding, runnerSandbox, startOf,
         TIERS } from "./runs-support";
import { drainDue, failure } from "./support";

/** Claude's sign-in page as its pinned CLI shows it (template s6dec61a38e839d37, live). */
const CLAUDE_PAGE = "https://claude.com/cai/oauth/authorize?code=true&client_id=" +
  "9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2F" +
  "platform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+" +
  "user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload+" +
  "user%3Aplugins&code_challenge=yfhUky71rorbPUGR_0kuaJoQPT0k39HLxYDNc-gUtt8&" +
  "code_challenge_method=S256&state=Lq7TdjukIEmxyMX2EKf2hY4xChr1sfIJOV3krpnv6iE";
const URL_SHOWN = CLAUDE_PAGE;
const CODEX_PAGE = "https://auth.openai.com/codex/device";

/** The sign-in's runner's binding on the egress host, by its lease's runner secret. */
const runnerOfSignin = (r: Gated, signin: SandboxSignin) =>
  runnerSandbox(r, signin as unknown as SandboxStart);

async function lastSignin(r: Gated): Promise<SandboxSignin> {
  return (await recorder(r.stub)).signins.at(-1)!;
}

const frame = (seq: number, event: Record<string, unknown>) =>
  ({ seq, at: Date.now() / 1000, event: { type: "ta.signin", ...event } });

/** Muse admitted on a full grant: an outside client with a sandbox of its own. */
async function client(r: Gated): Promise<void> {
  const bound = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
  await r.stub.outsideAgents(bound);
  const { revision } = (await r.stub.grantList(r.bind())).clients
    .find((entry) => entry.client === MUSE.client)!;
  await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "full",
                                       scopes: [], expected_revision: revision, name: "Muse" });
}

describe("a sign-in", () => {
  it("opens the owner's sandbox's lease with no run, and starts the record's login there",
     async () => {
    const r = await gated();
    const view = await r.stub.signinStart(r.bind(), { engine: "claude-code" });
    expect(view).toMatchObject({ id: expect.stringMatching(/^si_[0-9a-f]{32}$/),
                                 authority: "owner", engine: "claude-code", mode: "in",
                                 state: "starting", url: null });
    const asked = await lastSignin(r);
    expect(asked).toMatchObject({ id: (view as { id: string }).id, engine: "claude-code",
                                  mode: "in", region: null, authority: "owner",
                                  urls: { events: `https://egress.tinyassets.io/signin/${
                                    (view as { id: string }).id}/events` } });
    // It opened the lease: the sandbox is configured by this start (its tokens, its network).
    expect(asked.tokens).not.toBeNull();
    expect(asked.network).not.toBeNull();
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([{ authority: "owner" }]);
    // One at a time in a sandbox.
    expect((await failure(() => r.stub.signinStart(r.bind(), { engine: "codex" }))).message)
      .toBe(SIGNIN_LIVE);
  });

  it("is refused for an engine with no vendor sign-in, a region it does not take, or one " +
     "it needs and lacks", async () => {
    const r = await gated();
    for (const request of [{ engine: "pi" }, { engine: "nope" },
                           { engine: "claude-code", region: "global" },
                           { engine: "kimi" }, { engine: "kimi", region: "moon" },
                           { engine: "claude-code", mode: "sideways" }]) {
      expect((await failure(() => r.stub.signinStart(r.bind(), request as never))).message).toBeTruthy();
    }
    expect((await recorder(r.stub)).signins).toEqual([]);
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
  });

  it("is the owner's own door: no outside client starts one", async () => {
    const r = await gated();
    await client(r);
    const outside = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    expect((await failure(() => r.stub.signinStart(outside, { engine: "claude-code" }))).message).toBeTruthy();
  });

  it("keeps the vendor's prompt, takes a pasted code back, and lets the lease go at its end",
     async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const signin = await lastSignin(r);
    const runner = runnerOfSignin(r, signin);
    expect(await r.stub.signinEvents(runner, { id, frames: [
      frame(1, { state: "starting" }),
      frame(2, { state: "open", url: URL_SHOWN, code: null, accepts_code: true })] }))
      .toEqual({ accepted: 2, ended: false });
    expect((await r.stub.signinList(r.bind())).signins).toEqual([expect.objectContaining({
      id, state: "open", url: URL_SHOWN, code: null, accepts_code: true })]);
    expect(await r.stub.signinCode(r.bind(), { id, code: "abc#def" }))
      .toEqual({ delivered: true });
    expect((await recorder(r.stub)).codes).toEqual([{ authority: "owner", id, code: "abc#def" }]);
    for (const code of ["", "two\nlines", "\u0000"]) {
      expect((await failure(() => r.stub.signinCode(r.bind(), { id, code }))).message).toBeTruthy();
    }
    expect(await r.stub.signinEvents(runner, { id, frames: [frame(3, { state: "signed_in" })],
                                              end: { exit_code: 0 } }))
      .toEqual({ accepted: 1, ended: true });
    expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
    // No run holds the lease: it goes, and its sandbox is released.
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect((await recorder(r.stub)).releases).toEqual([{ lease_id: signin.lease.lease_id }]);
    // Its door is closed after its end.
    expect((await failure(() => r.stub.signinEvents(runner, { id, frames: [] }))).message)
      .toBe(RUN_ENDED);
  });

  it("refuses, outside the VM, a prompt that is not the vendor's own: the CLI stopped",
     async () => {
    const r = await gated();
    for (const [engine, url, code] of [
      ["claude-code", CLAUDE_PAGE.replace("claude.com", "claude.com.phish.test"), null],
      ["claude-code", CLAUDE_PAGE.replace("https:", "http:"), null],
      ["codex", CODEX_PAGE, "ABCDEFGHIJKLMNOPQRS"],
      ["codex", CODEX_PAGE, "AB CD"]] as const) {
      const { id } = await r.stub.signinStart(r.bind(), { engine }) as { id: string };
      const signin = await lastSignin(r);
      expect(await r.stub.signinEvents(runnerOfSignin(r, signin), { id, frames: [
        frame(1, { state: "open", url, code, accepts_code: false })] }))
        .toEqual({ accepted: 1, ended: true });
      expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
      expect((await recorder(r.stub)).signinCancels.at(-1)).toEqual({ authority: "owner", id });
    }
  });

  it("shows the host the vendor's URL sends the owner to", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "kimi", region: "global" }) as
      { id: string };
    await r.stub.signinEvents(runnerOfSignin(r, await lastSignin(r)), { id, frames: [
      frame(1, { state: "open", url: "https://www.kimi.ai/code/authorize_device?user_code=K1-ABC",
                 code: "K1-ABC", accepts_code: false })] });
    expect((await r.stub.signinList(r.bind())).signins[0]).toMatchObject({
      host: "www.kimi.ai", code: "K1-ABC" });
  });

  it("holds the lease a run joins, and leaves it to the run at its end", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const signin = await lastSignin(r);
    await defineAgent(r.stub, r.bind(), "main", { engine: "auto", scopes: [] });
    await r.connect("sk-ant-x");
    const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), { message: "go",
                                                                     client_send_id: "j-1" });
    const start = await startOf(r.stub, run.run_id);
    // The run joined the sign-in's lease: no tokens of its own.
    expect(start.tokens).toBeNull();
    expect(start.lease.lease_id).toBe(signin.lease.lease_id);
    await r.stub.signinEvents(runnerOfSignin(r, signin), { id, frames: [
      frame(1, { state: "cancelled" })], end: { exit_code: 1 } });
    expect(await r.sql("SELECT lease_id FROM sandbox_leases"))
      .toEqual([{ lease_id: signin.lease.lease_id }]);
    // The run's end lets it go.
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
  });

  it("is stopped by the owner, and ends with no sandbox to start in", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "codex" }) as { id: string };
    expect(await r.stub.signinCancel(r.bind(), { id })).toEqual({ cancelled: true });
    expect((await recorder(r.stub)).signinCancels).toEqual([{ authority: "owner", id }]);
    const s = await gated();
    await s.in((instance) => {
      instance.recorder.signinAnswer = { started: false, code: "sandbox_waiting" };
    });
    expect(await s.stub.signinStart(s.bind(), { engine: "codex" }))
      .toEqual({ started: false, code: "sandbox_waiting" });
    expect((await s.stub.signinList(s.bind())).signins).toEqual([]);
    expect(await s.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
  });

  it("signs a client's sandbox in only inside itself", async () => {
    const r = await gated();
    expect((await failure(() => r.stub.signinStart(r.bind(), { engine: "claude-code",
                                                authority: MUSE.client }))).message).toBeTruthy();
    await client(r);
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code",
                                                        authority: MUSE.client }) as { id: string };
    const signin = await lastSignin(r);
    expect(signin.authority).toBe(MUSE.client);
    expect(await r.sql("SELECT authority FROM sandbox_leases"))
      .toEqual([{ authority: MUSE.client }]);
    // Its presence lands on that sandbox's own lease, never the owner's engines.
    await r.stub.signinReport(runnerOfSignin(r, signin), { signins: [
      { engine: "claude-code", signed_in: true }] });
    expect((await r.stub.signinList(r.bind())).engines
      .filter((each) => each.signed_in)).toEqual([]);
    expect(await r.sql("SELECT signins FROM sandbox_leases"))
      .toEqual([{ signins: "claude-code" }]);
    // The owner's own sandbox may sign in beside it: one at a time a sandbox.
    await r.stub.signinStart(r.bind(), { engine: "claude-code" });
    expect((await r.stub.signinList(r.bind())).signins.map((each) => each.id)).toContain(id);
  });

  it("signs out by the same door, its runner deleting the engine's secrets", async () => {
    const r = await gated();
    await r.stub.signinStart(r.bind(), { engine: "kimi", mode: "out" });
    expect(await lastSignin(r)).toMatchObject({ engine: "kimi", mode: "out", region: null });
  });

  it("is watched on the app socket by its id, the owner's own", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const ticket = await r.stub.socketTicket(r.bind({ grant: "g-1",
                                                      expires_at: Date.now() / 1000 + 600 }),
                                             { conversation_id: id });
    expect(ticket.conversation_id).toBe(id);
    expect((await failure(() => r.stub.socketTicket(r.bind({ grant: "g-1", expires_at: Date.now() / 1000 + 600 }),
                                     { conversation_id: `si_${"0".repeat(32)}` }))).message).toBeTruthy();
  });
});

/** A sign-in of `engine` begun, its runner's binding, and its first frames posted. */
async function begun(r: Gated, engine: string, frames: Record<string, unknown>[],
                     region?: string) {
  const { id } = await r.stub.signinStart(r.bind(), { engine, ...(region === undefined ? {}
    : { region }) }) as { id: string };
  const runner = runnerOfSignin(r, await lastSignin(r));
  const answer = await r.stub.signinEvents(runner, {
    id, frames: frames.map((event, i) => frame(i + 1, event)) });
  return { id, runner, answer };
}

// -- the review's attacks (rvsi), each refused --------------------------------------------

describe("what leaves the VM through the trusted side (review M1)", () => {
  it("is a sign-in's fixed states only: a state the VM made up is refused, never stored",
     async () => {
    const r = await gated();
    const secretish = "sk-ant-oat01-AAAAAAAAAAAAAAAAAAA";
    const { id, answer } = await begun(r, "claude-code", [{ state: secretish }]);
    expect(answer).toEqual({ accepted: 1, ended: true });
    expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
    expect(JSON.stringify(await r.sql("SELECT * FROM sandbox_signins"))).not.toContain("sk-ant");
    expect((await recorder(r.stub)).signinCancels.at(-1)).toEqual({ authority: "owner", id });
    // The table takes no state but a sign-in's own.
    const live = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    expect((await failure(() => r.sql("UPDATE sandbox_signins SET state = ? WHERE id = ?",
                                      secretish, live.id))).message).toBeTruthy();
  });

  it("relays no reason the VM wrote: only a refusal's fixed reason", async () => {
    const r = await gated();
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as
      { id: string };
    const { lease } = await lastSignin(r);
    const relayed = await r.in((_instance, state) => {
      const gate = new WriteGate(state.storage);
      return gate.write(() => new Signins(gate.storage).frames(id, lease, [
        { type: "ta.signin", state: "code_sent", reason: "re-enter your password at " +
          "https://claude-login.example/ ; token=sk-ant-oat01-" + "B".repeat(80) },
        { type: "ta.signin", state: "refused", reason: "https://claude-login.example/" },
      ], false, Date.now() / 1000).events);
    });
    expect(relayed).toEqual([{ type: "ta.signin", state: "code_sent" },
                             { type: "ta.signin", state: "refused" }]);
    const s = await gated();
    const other = await s.stub.signinStart(s.bind(), { engine: "claude-code" }) as
      { id: string };
    const held = (await lastSignin(s)).lease;
    const fixed = await s.in((_instance, state) => {
      const gate = new WriteGate(state.storage);
      return gate.write(() => new Signins(gate.storage).frames(other.id, held, [
        { type: "ta.signin", state: "refused", reason: "credential" }], false,
                                                               Date.now() / 1000).events);
    });
    expect(fixed).toEqual([{ type: "ta.signin", state: "refused", reason: "credential" }]);
  });
});

describe("the vendor's page, pinned (review M2)", () => {
  it("is refused when it is another OAuth client's page, a redirect chain, or a stuffed " +
     "query, on the vendor's own host", async () => {
    const r = await gated();
    const cases: [string, string, string | null, string?][] = [
      ["claude-code", CLAUDE_PAGE.replace("9d1c250a-e61b-44d9-88ed-5944d1962f5e", "attacker"),
       null],
      ["claude-code", CLAUDE_PAGE.replace("platform.claude.com%2Foauth%2Fcode%2Fcallback",
                                          "evil.example%2Fcb"), null],
      ["claude-code", "https://claude.ai/oauth/authorize?client_id=attacker&response_type=" +
        "code&redirect_uri=https%3A%2F%2Fevil.example%2Fcb&scope=user%3Ainference", null],
      ["claude-code", `${CLAUDE_PAGE}&extra=1`, null],
      ["claude-code", CLAUDE_PAGE.replace("state=Lq7Tdj", "state=https%3A%2F%2Fevil.example%2F"),
       null],
      ["codex", "https://auth.openai.com/authorize?client_id=app_attacker&redirect_uri=" +
        "https%3A%2F%2Fevil.example%2Fcb&response_type=code", "ABCD-EFGH"],
      ["codex", `${CODEX_PAGE}?refresh_token_x=rt_${"Z".repeat(200)}`, "ABCD-EFGH"],
      ["gemini", "https://accounts.google.com/o/oauth2/v2/auth?client_id=1-attacker.apps." +
        "googleusercontent.com&redirect_uri=https%3A%2F%2Fevil.example&response_type=code" +
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive", null],
      ["gemini", "https://accounts.google.com/Logout?continue=https://appengine.google.com/" +
        "_ah/logout?continue=https://evil.example/", null],
      ["kimi", "https://www.kimi.ai/code/authorize_device?user_code=K1&continue=" +
        "https%3A%2F%2Fevil.example", "K1-ABC", "global"],
      ["kimi", "https://www.kimi.com/code/authorize_device?user_code=K1", "K1-ABC", "global"],
    ];
    for (const [engine, url, code, region] of cases) {
      const { id, answer } = await begun(r, engine, [
        { state: "open", url, code, accepts_code: false }], region);
      expect(answer, url).toEqual({ accepted: 1, ended: true });
      expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
      expect((await recorder(r.stub)).signinCancels.at(-1)).toEqual({ authority: "owner", id });
    }
  });

  it("runs the credential check on the trusted side: a token in a free parameter is refused",
     async () => {
    const r = await gated();
    const jwt = `eyJ${"a".repeat(12)}.${"b".repeat(12)}.${"c".repeat(12)}`;
    const { answer } = await begun(r, "claude-code", [
      { state: "open", url: CLAUDE_PAGE.replace("state=Lq7TdjukIEmxyMX2EKf2hY4xChr1sfIJOV3krpnv6iE",
                                                `state=${jwt}`), code: null }]);
    expect(answer).toEqual({ accepted: 1, ended: true });
  });

  it("is taken as the pinned CLI shows it, on each record", async () => {
    const r = await gated();
    for (const [engine, url, code, region] of [
      ["claude-code", CLAUDE_PAGE, null],
      ["codex", CODEX_PAGE, "ABCD-EFGH2"],
      ["kimi", "https://www.kimi.com/code/authorize_device?user_code=1JAG-4POS", "1JAG-4POS",
       "mainland-cn"],
      ["gemini", "https://accounts.google.com/o/oauth2/v2/auth?redirect_uri=https%3A%2F%2F" +
        "codeassist.google.com%2Fauthcode&access_type=offline&scope=https%3A%2F%2Fwww." +
        "googleapis.com%2Fauth%2Fcloud-platform%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2F" +
        "userinfo.email%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.profile&" +
        "code_challenge_method=S256&code_challenge=abc_DEF-123&state=" + "a1".repeat(32) +
        "&response_type=code&client_id=681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps." +
        "googleusercontent.com", null]] as const) {
      const { id, runner, answer } = await begun(r, engine, [
        { state: "open", url, code }], region);
      expect(answer, engine).toEqual({ accepted: 1, ended: false });
      expect((await r.stub.signinList(r.bind())).signins[0]).toMatchObject({ id, url, code });
      await r.stub.signinEvents(runner, { id, frames: [frame(9, { state: "cancelled" })] });
    }
  });
});

describe("a sign-in's deadline (review M4)", () => {
  it("ends it on the runner's own expiry, which the app is told", async () => {
    const r = await gated();
    const { answer } = await begun(r, "claude-code", [{ state: "expired" }]);
    expect(answer).toEqual({ accepted: 1, ended: true });
    expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
  });

  it("ends it on the trusted side past the vendor's code's lifetime, its CLI stopped, its " +
     "lease freed, however the runner beats", async () => {
    const r = await gated();
    const { id, runner, answer } = await begun(r, "codex", [
      { state: "open", url: CODEX_PAGE, code: "ABCD-EFGH" }]);
    expect(answer).toEqual({ accepted: 1, ended: false });
    const view = (await r.stub.signinList(r.bind())).signins[0]!;
    // Codex's record: 900 s (cited), and one lease TTL for the runner's last frame.
    expect(view.expires_at - view.started_at).toBe(900 + 60);
    await r.sql("UPDATE sandbox_signins SET started_at = started_at - 961 WHERE id = ?", id);
    expect(await r.stub.signinEvents(runner, { id, frames: [] }))
      .toEqual({ accepted: 0, ended: true });
    expect((await recorder(r.stub)).signinCancels.at(-1)).toEqual({ authority: "owner", id });
    expect(await r.sql("SELECT authority FROM sandbox_leases")).toEqual([]);
  });

  it("is a sign-in gone from the list once past, beats or not", async () => {
    const r = await gated();
    const { id } = await begun(r, "claude-code", [{ state: "open", url: CLAUDE_PAGE }]);
    await r.sql("UPDATE sandbox_signins SET started_at = started_at - 100000 WHERE id = ?", id);
    expect((await r.stub.signinList(r.bind())).signins).toEqual([]);
  });
});

describe("the per-source switch, in the owner's object (review H1)", () => {
  it("refuses a sign-in to a source switched off, whatever door asks", async () => {
    const r = await gated();
    expect((await failure(() => r.stub.signinStart(r.bind({ signins: ["claude-code"] }),
                                                   { engine: "gemini" }))).message)
      .toBe(SIGNIN_OFF);
    expect((await recorder(r.stub)).signins).toEqual([]);
    // A door that hands none reads the list last handed: still off.
    expect((await failure(() => r.stub.signinStart(r.bind({ signins: undefined }),
                                                   { engine: "gemini" }))).message)
      .toBe(SIGNIN_OFF);
  });

  it("always lets a source switched off be signed out", async () => {
    const r = await gated();
    expect(await r.stub.signinStart(r.bind({ signins: [] }), { engine: "codex", mode: "out" }))
      .toMatchObject({ engine: "codex", mode: "out", state: "starting" });
  });

  it("takes no pasted code once the source is switched off", async () => {
    const r = await gated();
    const { id } = await begun(r, "claude-code", [{ state: "open", url: CLAUDE_PAGE }]);
    expect((await failure(() => r.stub.signinCode(r.bind({ signins: [] }),
                                                  { id, code: "abc#def" }))).message)
      .toBe(SIGNIN_OFF);
    expect((await recorder(r.stub)).codes).toEqual([]);
  });

  it("never runs on a switched-off source's vendor login, named or by auto", async () => {
    const r = await gated();
    const off = r.bind({ signins: ["claude-code"] });
    await defineAgent(r.stub, off, "coder", { engine: "codex",
                                              credential: "vendor_login_in_sandbox",
                                              scopes: [] });
    expect((await failure(() => ownerRun(r.stub, off, undefined, "coder"))).message)
      .toBe(`${SIGNIN_OFF} (codex)`);
    // Its record's own credentials, none named: refused too, never a silent key.
    await defineAgent(r.stub, off, "plain", { engine: "codex", scopes: [] });
    expect((await failure(() => ownerRun(r.stub, off, undefined, "plain"))).message)
      .toBe(`${SIGNIN_OFF} (codex)`);
    expect((await recorder(r.stub)).starts).toEqual([]);
    // `auto` passes over a signed-in plan whose switch is off.
    const { id } = await begun(r, "claude-code", [{ state: "open", url: CLAUDE_PAGE }]);
    await r.stub.signinReport(runnerOfSignin(r, await lastSignin(r)), { signins: [
      { engine: "codex", signed_in: true }] });
    await r.stub.signinCancel(r.bind(), { id });
    await defineAgent(r.stub, off, "main", { engine: "auto", scopes: [] });
    expect((await failure(() => ownerRun(r.stub, off, undefined, "main"))).message)
      .toBe(NO_SOURCE);
    // Switched on (the binder hands it), the same agent runs on the vendor's login.
    const on = r.bind({ signins: ["claude-code", "codex"] });
    expect((await ownerRun(r.stub, on, undefined, "coder")).start)
      .toMatchObject({ engine: "codex", credential: "vendor_login_in_sandbox" });
  });
});

describe("the runs lane's own read of the switches (review round 2, N1)", () => {
  const OPEN = Object.freeze({ outside: "on", ordinary: "all", canary: [], inbound: false });

  /** `agent`'s schedule due now, the lane run once, with no door call handing a list. */
  async function laneRuns(r: Gated, agent: string):
      Promise<{ engine: string; credential: string }[]> {
    await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent, input: "nightly",
                                                     at: Date.now() / 1000 + 3600 });
    await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                Date.now() / 1000 - 1);
    // A door call that hands no list (`signins` undefined) syncs the schedule to the row.
    await r.stub.fileMkdir(r.bind({ signins: undefined }),
                           { path: `/workspace/d-${crypto.randomUUID()}`, expected_etag: null });
    await drainDue(r.stub);
    return (await recorder(r.stub)).starts;
  }

  it("stops a switched-off source powering the lane's runs, though no door call hands a list",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { engine: "claude-code",
                                                  credential: "vendor_login_in_sandbox",
                                                  scopes: [] });
    try {
      // The founder switches Claude Code off in the control record the object reads.
      await env.CONTROL.put("c:control", JSON.stringify({ ...OPEN,
                                                         signins: { "claude-code": false } }));
      expect(await laneRuns(r, "main")).toEqual([]);
      expect((await r.stub.noticeList(r.bind())).notices.map((notice) => notice.detail))
        .toContainEqual(expect.stringContaining("signin_off"));
      // Switched on again, the lane's next schedule runs on it.
      await env.CONTROL.put("c:control", JSON.stringify({ ...OPEN,
                                                         signins: { "claude-code": true } }));
      expect((await laneRuns(r, "main")).map((start) => [start.engine, start.credential]))
        .toEqual([["claude-code", "vendor_login_in_sandbox"]]);
    } finally {
      await env.CONTROL.delete("c:control");
    }
  });

  it("fails closed: a control record the lane cannot read switches every sign-in off",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { engine: "claude-code",
                                                  credential: "vendor_login_in_sandbox",
                                                  scopes: [] });
    try {
      await env.CONTROL.put("c:control", "{not json");
      expect(await laneRuns(r, "main")).toEqual([]);
      expect((await r.stub.noticeList(r.bind())).notices.map((notice) => notice.detail))
        .toContainEqual(expect.stringContaining("signin_off"));
    } finally {
      await env.CONTROL.delete("c:control");
    }
  });
});

describe("a pasted code (review L1)", () => {
  it("goes only to a sign-in asking for one, within its deadline, a terminal line at most",
     async () => {
    const r = await gated();
    // Codex's device flow takes none.
    const codex = await begun(r, "codex", [{ state: "open", url: CODEX_PAGE, code: "ABCD-EFGH",
                                             accepts_code: true }]);
    expect((await r.stub.signinList(r.bind())).signins[0]).toMatchObject({ accepts_code: false });
    expect((await failure(() => r.stub.signinCode(r.bind(), { id: codex.id, code: "x" })))
      .message).toBe(NO_CODE_WANTED);
    await r.stub.signinEvents(codex.runner, { id: codex.id, frames: [
      frame(9, { state: "cancelled" })] });
    // Claude's, before its prompt shows, and past its deadline.
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as
      { id: string };
    expect((await failure(() => r.stub.signinCode(r.bind(), { id, code: "abc" }))).message)
      .toBe(NO_CODE_WANTED);
    await r.stub.signinEvents(runnerOfSignin(r, await lastSignin(r)), { id, frames: [
      frame(1, { state: "open", url: CLAUDE_PAGE })] });
    expect((await failure(() => r.stub.signinCode(r.bind(), { id, code: "x".repeat(4096) })))
      .message).toMatch(/at most 4095/);
    expect(await r.stub.signinCode(r.bind(), { id, code: "x".repeat(4095) }))
      .toEqual({ delivered: true });
    await r.sql("UPDATE sandbox_signins SET started_at = started_at - 100000 WHERE id = ?", id);
    expect((await failure(() => r.stub.signinCode(r.bind(), { id, code: "abc" }))).message)
      .toBe(NO_CODE_WANTED);
    expect((await recorder(r.stub)).codes.map((each) => each.code.length)).toEqual([4095]);
  });
});

describe("the past runs", () => {
  it("are listed newest first, a page at a time, each its receipt without its result",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { engine: "auto", scopes: [] });
    await r.connect("sk-ant-x");
    const ids: string[] = [];
    for (let i = 0; i < 3; i += 1) {
      const run = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), {
        message: `go ${i}`, client_send_id: `l-${i}` });
      ids.push(run.run_id);
      const start = await startOf(r.stub, run.run_id);
      await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    }
    const first = await r.stub.runList(r.bind(), { limit: 2 });
    expect(first.runs.map((run) => run.run_id)).toEqual([ids[2], ids[1]]);
    expect(first.runs[0]).not.toHaveProperty("result");
    expect(first.next).toBe(first.runs[1]!.seq);
    const second = await r.stub.runList(r.bind(), { limit: 2, before: first.next! });
    expect(second.runs.map((run) => run.run_id)).toEqual([ids[0]]);
    expect(second.next).toBeNull();
    for (const request of [{ limit: 0 }, { limit: 1.5 }, { before: -1 }, { other: 1 }]) {
      expect((await failure(() => r.stub.runList(r.bind(), request as never))).message).toBeTruthy();
    }
  });

  it("are an outside client's own alone", async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", { engine: "auto", scopes: [] });
    await r.connect("sk-ant-x");
    const owners = await r.stub.agentSubmit(r.bind({ tiers: TIERS }), {
      message: "mine", client_send_id: "o-1" });
    await client(r);
    const outside = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    expect((await r.stub.runList(outside, {})).runs).toEqual([]);
    expect((await r.stub.runList(r.bind(), {})).runs.map((run) => run.run_id))
      .toEqual([owners.run_id]);
  });
});
