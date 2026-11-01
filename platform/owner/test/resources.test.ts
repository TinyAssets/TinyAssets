/**
 * The agent's abilities as files (tasks 5, A5; pi audit F9): skills listed in the `skills`
 * section and read with `read`, a `disable-model-invocation` skill left out, the section
 * rebuilt once for an edit and never per request, `/skill:name args` and prompt templates
 * expanded at the doors, packages selected by owner policy with pi's filters, and a package
 * that declares code refused loudly.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import {
  type AssistantMessage, fauxAssistantMessage, type FauxContentBlock, fauxToolCall,
  type Message,
} from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";

import { DOT, ledgerOn, MCP, MUSE, signedIn } from "./ledger-support";
import { failure } from "./support";
import { type FauxOwnerDO, says } from "./worker";
import { AGENT_DEFINITION } from "../src/agent";
import type { Origin } from "../src/ledger";
import type { Binding } from "../src/owner-do";
import { AGENTS_OWNER_ONLY } from "../src/paths";
import {
  applyPatterns, bodyOf, commandArgs, globMatch, PACKAGE_EXECUTABLE, PACKAGE_REFUSED,
  SKILL_SKIPPED, SKILLS_EXTENSION, substituteArgs,
} from "../src/resources";

const text = (value: string): Uint8Array => new TextEncoder().encode(value);
const P = "/workspace/.agents/packages";

/** A `SKILL.md`. */
function skill(name: string, description: string, body: string, extra = ""): string {
  return `---\nname: ${name}\ndescription: ${description}\n${extra}---\n${body}\n`;
}

/** An owner of its own on the faux-model class, signed in, with a session for policy. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.AGENT.getByName(owner);
  const as = (): Binding => ({ owner, caller: owner, origin: null });
  const at = signedIn();
  const { session } = await stub.sessionOpen({ ...as(), authenticated_at: at });
  const client = (origin: Origin): Binding =>
    ({ owner, caller: owner, origin, source: MCP, authenticated_at: at });
  return {
    stub, as, client,
    /** The client's first verified call records its grant: full, generation 1. */
    admit: async (origin: Origin) => {
      await stub.outsideAgents(client(origin));
      await runInDurableObject(stub, (_instance, state) => ledgerOn(state.storage).register(
        origin, MCP, { command_center_id: "alpha", display_name: "Muse",
                       delivery_method: "pull", recipe_id: null }));
    },
    /** The owner scopes the client to `capabilities` in universe `alpha` as agent `main`. */
    scoped: async (origin: Origin, capabilities: string[]) => {
      const row = (await stub.grantList(as())).clients
        .find((held) => held.client === origin.client);
      return (await stub.grantSet({ ...as(), session }, {
        client: origin.client, source: "mcp", mode: "scoped", name: "",
        scopes: capabilities.map((capability) => ({ universe: "alpha", agent: "main",
                                                    capability })),
        expected_revision: row?.revision ?? 0 })).generation;
    },
    write: (path: string, content: string) =>
      stub.fileWrite(as(), { path, expected_etag: null, content: text(content) }),
    /** The owner selects `packages` in its policy. */
    select: async (packages: unknown[]) => {
      const { generation, policy } = await stub.policyRead(as());
      return stub.policyWrite({ ...as(), session }, {
        expected_generation: generation, policy: { ...policy, packages: packages as never } });
    },
    in: <R>(act: (instance: FauxOwnerDO) => R | Promise<R>) => runInDurableObject(stub, act),
  };
}

/** The skills section as the request's transcript holds it: its system messages replayed. */
function sectionOf(messages: readonly Message[]): string | undefined {
  let shown: string | undefined;
  for (const message of messages as unknown as { role: string;
                                                  sections?: Record<string, string | null> }[]) {
    if (message.role === "system" && message.sections !== undefined
        && "skills" in message.sections) {
      shown = message.sections["skills"] ?? undefined;
    }
  }
  return shown;
}

/** The text of the request's last user message: the input pi was handed. */
function inputOf(messages: readonly Message[]): string {
  const first = [...messages].reverse().find((message) => message.role === "user");
  const content = first?.content;
  return typeof content === "string" ? content
    : (content ?? []).map((part) => (part.type === "text" ? part.text : "")).join("");
}

function calls(...blocks: FauxContentBlock[]): AssistantMessage {
  return fauxAssistantMessage(blocks, { stopReason: "toolUse" });
}

/** The model's answer: the skills section it was shown, or `none`. */
const showsSection = (messages: Message[]) => says(sectionOf(messages) ?? "none");

async function until(check: () => Promise<boolean>, what: string): Promise<void> {
  for (let tries = 0; tries < 500; tries++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

describe("skills, the pi-coding-agent way", () => {
  it("lists a skill's name, description and path, which the model reads with read; a " +
     "skill that disables model invocation is not listed", async () => {
    const r = await rig();
    await r.write("/workspace/skills/pdf-tools/SKILL.md",
                  skill("pdf-tools", "Extract text from PDF files. Use for PDFs.",
                        "# PDF tools\nRun scripts/extract.sh on the file."));
    await r.write("/workspace/.agents/skills/team/deploy/SKILL.md",
                  skill("deploy", "Deploy the site.", "Deploy steps."));
    await r.write("/workspace/.agents/skills/secret/SKILL.md",
                  skill("secret", "Only by its command.", "Secret steps.",
                        "disable-model-invocation: true\n"));
    // Never looked into: a skill's own directory, a dot directory, node_modules.
    await r.write("/workspace/skills/pdf-tools/examples/inner/SKILL.md",
                  skill("inner", "Inside another skill.", "inner"));
    await r.write("/workspace/skills/.cache/stale/SKILL.md", skill("stale", "Hidden.", "x"));
    await r.write("/workspace/skills/node_modules/dep/SKILL.md", skill("dep", "A dep.", "x"));
    const out = await r.in(async (instance) => {
      let section: string | undefined;
      instance.script = [
        (messages) => {
          section = sectionOf(messages);
          return calls(fauxToolCall("read", { path: "/workspace/skills/pdf-tools/SKILL.md" }));
        },
        (messages) => says(JSON.stringify(messages.filter((m) => m.role === "toolResult"))),
      ];
      const answer = await instance.harness.prompt("convert my pdf");
      return { section, answer, offered: instance.offered };
    });
    expect(out.section).toContain("<available_skills>");
    expect(out.section).toContain("Use the read tool to load a skill's file");
    expect(out.section).toContain("<name>pdf-tools</name>");
    expect(out.section).toContain(
      "<description>Extract text from PDF files. Use for PDFs.</description>");
    expect(out.section).toContain("<location>/workspace/skills/pdf-tools/SKILL.md</location>");
    // Found at any depth under the Agent Skills location too, the first root first.
    expect(out.section).toContain("<location>/workspace/.agents/skills/team/deploy/SKILL.md" +
                                  "</location>");
    expect(out.section!.indexOf("deploy")).toBeLessThan(out.section!.indexOf("pdf-tools"));
    expect(out.section).not.toContain("secret");
    for (const hidden of ["inner", "stale", "dep"]) {
      expect(out.section).not.toContain(`<name>${hidden}</name>`);
    }
    // The model read the skill with read: no skill tool, the five tools offered alone.
    expect(out.answer.status).toBe("done");
    expect(out.answer.text).toContain("Run scripts/extract.sh on the file.");
    expect(out.offered.every((tools) => [...tools].sort().join() === "bash,edit,read,ta,write"))
      .toBe(true);
  });

  it("rebuilds the section once for a SKILL.md edit, and never per request", async () => {
    const r = await rig();
    await r.write("/workspace/skills/notes/SKILL.md",
                  skill("notes", "Take notes, first version.", "Write notes."));
    await r.in((instance) => {
      instance.script = Array.from({ length: 10 }, () => showsSection);
    });
    // Three messages through the door, one a slash that names nothing.
    const shown = [];
    for (const [n, ask] of ["one", "/two", "three"].entries()) {
      shown.push((await r.stub.agentSubmit(r.as(), { message: ask, client_send_id: `s-${n}`,
                                                     wait_ms: 10_000 })).text);
    }
    const first = { shown, builds: await r.in(async (instance) => {
      await instance.agent.resources.settled();
      return instance.agent.resources.builds;
    }) };
    // Three requests, one build: the section was rendered from what the first open built.
    expect(first.builds).toBe(1);
    expect(first.shown.every((shown) => shown!.includes("first version"))).toBe(true);
    // A file that is no skill rebuilds nothing.
    await r.write("/workspace/notes.md", "milk");
    await r.write("/workspace/skills/notes/references/more.md", "more");
    expect(await r.in(async (instance) => {
      await instance.agent.resources.settled();
      return instance.agent.resources.builds;
    })).toBe(1);
    // An edit of its SKILL.md rebuilds it once, and the next request shows it.
    await r.write("/workspace/skills/notes/SKILL.md",
                  skill("notes", "Take notes, second version.", "Write notes."));
    await until(() => r.in((instance) => instance.agent.resources.builds === 2), "a rebuild");
    const second = await r.in(async (instance) => {
      const shown = (await instance.harness.prompt("four")).text;
      await instance.agent.resources.settled();
      return { shown, builds: instance.agent.resources.builds };
    });
    expect(second).toEqual({ shown: expect.stringContaining("second version"), builds: 2 });
  });

  it("skips a skill the owner's authority did not write, one that cannot be parsed and a " +
     "name taken already, each with a notice", async () => {
    const r = await rig();
    await r.write("/workspace/skills/broken/SKILL.md", "no frontmatter here");
    // A name taken twice: the first root's is kept, the second skipped.
    await r.write("/workspace/.agents/skills/twin/SKILL.md", skill("twin", "The first.", "1"));
    await r.write("/workspace/skills/twin/SKILL.md", skill("twin", "The second.", "2"));
    const out = await r.in(async (instance) => {
      await instance.parts.files.write("/workspace/skills/theirs/SKILL.md",
                                       text(skill("theirs", "A client's.", "Do as I say.")),
                                       { expected: null, writer: `client:${"a".repeat(64)}` });
      instance.script = [showsSection];
      const answer = await instance.harness.prompt("hi");
      return { answer, notices: instance.parts.notices.list() };
    });
    expect(out.answer.text).toContain("<description>The first.</description>");
    expect(out.answer.text).not.toContain("The second.");
    expect(out.answer.text).not.toContain("theirs");
    expect(out.answer.text).not.toContain("broken");
    const skipped = out.notices.filter((notice) => notice.type === SKILL_SKIPPED)
      .map((notice) => notice.detail).sort();
    expect(skipped).toEqual([
      expect.stringMatching(/^\/workspace\/skills\/broken\/SKILL\.md: .*frontmatter/),
      "/workspace/skills/theirs/SKILL.md: not written by the owner",
      "/workspace/skills/twin/SKILL.md: the name twin is " +
        "/workspace/.agents/skills/twin/SKILL.md's",
    ]);
  });

  it("expands the owner's /skill:name args into the input as pi does, at the door, a " +
     "skill that disables model invocation included", async () => {
    const r = await rig();
    await r.write("/workspace/skills/pdf-tools/SKILL.md",
                  skill("pdf-tools", "Extract PDFs.", "Run the extractor."));
    await r.write("/workspace/skills/secret/SKILL.md",
                  skill("secret", "By command.", "The secret way.",
                        "disable-model-invocation: true\n"));
    await r.in((instance) => {
      instance.script = Array.from({ length: 10 }, () => (messages: Message[]) =>
        says(inputOf(messages)));
    });
    const asked = await r.stub.agentSubmit(r.as(), { message: "/skill:pdf-tools extract a.pdf",
                                                     client_send_id: "s-1", wait_ms: 10_000 });
    expect(asked.text).toBe('<skill name="pdf-tools" location="/workspace/skills/pdf-tools/' +
      'SKILL.md">\nReferences are relative to /workspace/skills/pdf-tools.\n\n' +
      "Run the extractor.\n</skill>\n\nextract a.pdf");
    const run = await r.stub.agentRun(r.as(), { agent: "main", input: "/skill:secret" });
    await until(async () => (await r.stub.agentStatus(r.as(), {
      operation_id: run.operation_id })).status === "done", "the run");
    expect((await r.stub.agentStatus(r.as(), { operation_id: run.operation_id })).text)
      .toBe('<skill name="secret" location="/workspace/skills/secret/SKILL.md">\n' +
            "References are relative to /workspace/skills/secret.\n\nThe secret way.\n</skill>");
    // An unknown skill, and a slash that names nothing, stay as typed.
    const unknown = await r.stub.agentSubmit(r.as(), { message: "/skill:nope go",
                                                       client_send_id: "s-2", wait_ms: 10_000 });
    expect(unknown.text).toBe("/skill:nope go");
  });

  it("refuses loudly an expanded input past the doors' bound", async () => {
    const r = await rig();
    await r.write("/workspace/skills/long/SKILL.md", skill("long", "Long.", "x".repeat(40_000)));
    expect((await failure(() => r.stub.agentSubmit(r.as(), {
      message: "/skill:long", client_send_id: "s-1", wait_ms: 10_000 }))).message)
      .toMatch(/^an expanded input is text of 1 to 32000 characters/);
  });

  it("expands a prompt template with pi's argument substitution", async () => {
    const r = await rig();
    await r.write("/workspace/prompts/review.md",
                  "---\ndescription: Review changes\n---\nReview it. Focus on " +
                  "${1:-correctness}; all: $@.");
    await r.in((instance) => {
      instance.script = Array.from({ length: 10 }, () => (messages: Message[]) =>
        says(inputOf(messages)));
    });
    const focused = await r.stub.agentSubmit(r.as(), { message: "/review \"API shape\" speed",
                                                       client_send_id: "s-1", wait_ms: 10_000 });
    expect(focused.text).toBe("Review it. Focus on API shape; all: API shape speed.");
    const plain = await r.stub.agentSubmit(r.as(), { message: "/review", client_send_id: "s-2",
                                                     wait_ms: 10_000 });
    expect(plain.text).toBe("Review it. Focus on correctness; all: .");
  });
});

describe("declarative packages", () => {
  it("loads a selected package's skills and templates, filtered by owner policy with pi's " +
     "grammar, and nothing of a package not selected", async () => {
    const r = await rig();
    await r.write("/workspace/.agents/packages/kit/package.json", JSON.stringify({
      name: "kit", keywords: ["pi-package"],
      pi: { skills: ["./skills"], prompts: ["./prompts/*.md"] } }));
    for (const name of ["alpha", "beta", "gamma"]) {
      await r.write(`/workspace/.agents/packages/kit/skills/${name}/SKILL.md`,
                    skill(name, `The ${name} skill.`, `${name} steps.`));
    }
    await r.write("/workspace/.agents/packages/kit/prompts/kitcmd.md", "Kit's command, $1.");
    await r.write("/workspace/.agents/packages/other/skills/zeta/SKILL.md",
                  skill("zeta", "Not selected.", "zeta"));
    await r.select([{ source: "/workspace/.agents/packages/kit", skills: ["!beta", "-skills/gamma"] }]);
    await r.in(async (instance) => {
      instance.script = Array.from({ length: 10 }, () => (messages: Message[]) =>
        says(messages.length === 0 ? "" : `${sectionOf(messages) ?? "none"}\n---\n${
          inputOf(messages)}`));
    });
    const shown = await r.stub.agentSubmit(r.as(), { message: "/kitcmd now",
                                                     client_send_id: "s-1", wait_ms: 10_000 });
    expect(shown.text).toContain("<location>/workspace/.agents/packages/kit/skills/alpha/SKILL.md" +
                                 "</location>");
    expect(shown.text).not.toContain("beta");
    expect(shown.text).not.toContain("gamma");
    expect(shown.text).not.toContain("zeta");
    expect(shown.text).toContain("---\nKit's command, now.");
    // A selection that loads none of the package's prompts, and all its skills.
    await r.select([{ source: "/workspace/.agents/packages/kit", prompts: [] }]);
    await until(() => r.in((instance) => instance.agent.resources.builds >= 2),
                "the selection's rebuild");
    await r.in((instance) => instance.agent.resources.settled());
    const again = await r.stub.agentSubmit(r.as(), { message: "/kitcmd later",
                                                     client_send_id: "s-2", wait_ms: 10_000 });
    expect(again.text).toContain("<name>gamma</name>");
    expect(again.text).toContain("<name>beta</name>");
    expect(again.text).toContain("---\n/kitcmd later");
  });

  it("refuses a package that declares code, loudly, with its notice, loading nothing of it",
     async () => {
    const r = await rig();
    await r.write("/workspace/.agents/packages/coded/package.json", JSON.stringify({
      name: "coded", pi: { extensions: ["./index.ts"], skills: ["./skills"] } }));
    await r.write("/workspace/.agents/packages/coded/index.ts", "export default () => {};");
    await r.write("/workspace/.agents/packages/coded/skills/inner/SKILL.md",
                  skill("inner", "Inside a coded package.", "inner"));
    await r.write("/workspace/.agents/packages/folded/extensions/hook.ts", "export default 1;");
    await r.write("/workspace/.agents/packages/folded/skills/folded/SKILL.md",
                  skill("folded", "Beside an extensions directory.", "folded"));
    await r.write("/workspace/.agents/packages/clean/package.json", JSON.stringify({
      name: "clean", version: "1.0.0", keywords: ["pi-package"],
      pi: { skills: ["./skills"], themes: ["./themes/dark.json"], extensions: [] },
      tinyassets: { models: { prefer: ["x"] } } }));
    await r.write("/workspace/.agents/packages/clean/skills/clean/SKILL.md",
                  skill("clean", "A data package.", "clean"));
    // Code in every other form a manifest names it: each package refused whole.
    const coded: Record<string, unknown> = {
      stringed: { pi: { extensions: "./index.js" } }, entry: { main: "index.js" },
      exported: { exports: { ".": "./index.js" } }, binary: { bin: { x: "x.js" } },
      scripted: { scripts: { postinstall: "node x.js" } }, hooked: { pi: { hooks: ["x"] } },
    };
    for (const [name, manifest] of Object.entries(coded)) {
      await r.write(`/workspace/.agents/packages/${name}/package.json`, JSON.stringify(manifest));
      await r.write(`/workspace/.agents/packages/${name}/skills/${name}/SKILL.md`,
                    skill(name, `In ${name}.`, name));
    }
    await r.select(["/workspace/.agents/packages/coded", "/workspace/.agents/packages/folded",
                    "/workspace/.agents/packages/clean",
                    ...Object.keys(coded).map((name) => `/workspace/.agents/packages/${name}`)]);
    const out = await r.in(async (instance) => {
      instance.script = [showsSection];
      const answer = await instance.harness.prompt("hi");
      return { answer, notices: instance.parts.notices.list() };
    });
    expect(out.answer.text).toContain("<name>clean</name>");
    for (const name of ["inner", "folded", ...Object.keys(coded)]) {
      expect(out.answer.text).not.toContain(`<name>${name}</name>`);
    }
    const refused = Object.fromEntries(out.notices
      .filter((notice) => notice.type === PACKAGE_REFUSED)
      .map((notice) => notice.detail.split(": ")));
    const is = (what: string) => `${PACKAGE_EXECUTABLE}: ${what}`;
    expect(refused).toEqual({
      [`${P}/coded`]: PACKAGE_EXECUTABLE, [`${P}/folded`]: PACKAGE_EXECUTABLE,
      [`${P}/stringed`]: PACKAGE_EXECUTABLE, [`${P}/entry`]: PACKAGE_EXECUTABLE,
      [`${P}/exported`]: PACKAGE_EXECUTABLE, [`${P}/binary`]: PACKAGE_EXECUTABLE,
      [`${P}/scripted`]: PACKAGE_EXECUTABLE, [`${P}/hooked`]: PACKAGE_EXECUTABLE,
    });
    const details = out.notices.map((notice) => notice.detail);
    expect(details).toContain(`${P}/coded: ${is("pi.extensions is code, or no data the " +
      "object loads, and the owner object runs no package code")}`);
    expect(details).toContain(`${P}/stringed: ${is("pi.extensions is code, or no data the " +
      "object loads, and the owner object runs no package code")}`);
    expect(details).toContain(`${P}/scripted: ${is("scripts is code, or no data the object " +
      "loads, and the owner object runs no package code")}`);
    expect(details).toContain(`${P}/folded: ${is("its extensions directory is code, and the " +
      "owner object runs no package code")}`);
    // Data the object does not load is skipped with a notice: the tinyassets key's fields,
    // and pi's themes.
    expect(details).toContain(`${P}/clean: tinyassets.models is not loaded`);
    expect(details).toContain(`${P}/clean: pi.themes is not loaded`);
  });

  it("holds the selection to package directories in the owner's own tree, with no code " +
     "filter", async () => {
    const r = await rig();
    for (const bad of [[{ source: `${P}/kit`, extensions: [] }],
                       ["/.ta/tmp/kit"], ["/workspace"], ["relative/kit"], [P],
                       ["/workspace/packages/kit"], ["/workspace/skills/kit"],
                       [`${P}/a`, `${P}/a`], [{ source: `${P}/a`, skills: "all" }]]) {
      expect((await failure(() => r.select(bad))).message).toMatch(/invalid packages policy/);
    }
    await r.select([`${P}/a`, { source: `${P}/b`, skills: ["x"], prompts: [] }]);
  });

  it("installs the section as the object's own extension, which no package may name", () => {
    expect(SKILLS_EXTENSION).toBe("ta.skills");
  });
});

describe("the agent's abilities are the owner's alone", () => {
  it("refuses a client's file anywhere in the skills, templates and packages, at the doors " +
     "and from its conversation", async () => {
    const r = await rig();
    await r.write("/workspace/skills/deploy/SKILL.md",
                  skill("deploy", "Deploy the site.", "Run scripts/run.sh."));
    await r.write(`${P}/kit/skills/kit/SKILL.md`, skill("kit", "A kit.", "kit"));
    await r.select([`${P}/kit`]);
    await r.admit(MUSE);
    const theirs = r.client(MUSE);
    const refused = async (path: string) => (await failure(() => r.stub.fileWrite(theirs, {
      path, expected_etag: null, content: text("planted") }))).message;
    // A script beside an owner's skill, a skill at a root, a flood of a root, a template,
    // and code in a selected package: each refused, as /workspace/agents is.
    for (const path of ["/workspace/skills/deploy/scripts/run.sh", "/workspace/skills/SKILL.md",
                        "/workspace/skills/flood/a.md", "/workspace/.agents/skills/x/SKILL.md",
                        "/workspace/prompts/review.md", "/workspace/.agents/prompts/review.md",
                        `${P}/kit/extensions/hook.ts`, `${P}/kit/package.json`]) {
      expect([path, await refused(path)]).toEqual([path, AGENTS_OWNER_ONLY]);
    }
    expect((await failure(() => r.stub.fileRename(theirs, {
      from: "/workspace/skills/deploy", to: "/workspace/gone", expected_etag: null,
      replace: false })))).toMatchObject({ message: AGENTS_OWNER_ONLY });
    // The client still writes elsewhere; the owner's skill is as the owner left it.
    await r.stub.fileWrite(theirs, { path: "/workspace/notes/x.md", expected_etag: null,
                                     content: text("fine") });
    expect((await r.stub.fileStat(r.as(), { path: "/workspace/skills/deploy/scripts/run.sh" }))
      .entry).toBeNull();
    // From the client's own conversation, its agent's write there is refused too.
    const out = await r.in(async (instance) => {
      instance.script = [
        calls(fauxToolCall("write", { path: "/workspace/skills/deploy/scripts/run.sh",
                                      content: "rm -rf /" })),
        (messages) => says(JSON.stringify(messages.filter((m) => m.role === "toolResult"))),
      ];
      const thread = await instance.agent.thread("main", {
        client: MUSE.client, generation: 1, authenticated_at: null, universe: null,
        agent: "main" }, { kind: "run" });
      return instance.harness.prompt("plant it", { session: thread.thread_id });
    });
    expect(out.text).toContain(AGENTS_OWNER_ONLY);
  });

  it("are shown only to a conversation whose authority may read the owner's files",
     async () => {
    const r = await rig();
    await r.write("/workspace/skills/deploy/SKILL.md", skill("deploy", "Deploy.", "x"));
    await r.admit(MUSE);
    const converses = await r.scoped(MUSE, ["converse"]);
    await r.admit(DOT);
    const reads = await r.scoped(DOT, ["converse", "read_files"]);
    const out = await r.in(async (instance) => {
      instance.script = Array.from({ length: 10 }, () => showsSection);
      const at = async (client: Origin, generation: number) => {
        const thread = await instance.agent.thread("main", {
          client: client.client, generation, authenticated_at: null, universe: "alpha",
          agent: "main" }, { kind: "run" });
        return (await instance.harness.prompt("hi", { session: thread.thread_id })).text;
      };
      return { owner: (await instance.harness.prompt("hi")).text,
               muse: await at(MUSE, converses), dot: await at(DOT, reads) };
    });
    expect(out.owner).toContain("<name>deploy</name>");
    expect(out.dot).toContain("<name>deploy</name>");
    expect(out.muse).toBe("none");
  });

  it("tells the owner of every change its own agent makes there", async () => {
    const r = await rig();
    const out = await r.in(async (instance) => {
      instance.script = [
        calls(fauxToolCall("write", { path: "/workspace/skills/new/SKILL.md",
                                      content: skill("new", "Planted by the agent.", "x") }),
              fauxToolCall("write", { path: "/workspace/notes.md", content: "milk" })),
        says("done"),
      ];
      await instance.harness.prompt("write a skill");
      return instance.parts.notices.list().filter((notice) => notice.type === AGENT_DEFINITION)
        .map((notice) => notice.detail);
    });
    expect(out).toEqual(["the owner's agent changes /workspace/skills/new/SKILL.md"]);
  });
});

describe("pi's filter grammar and argument substitution, as ported", () => {
  const base = "/p";
  const files = ["/p/skills/a/SKILL.md", "/p/skills/b/SKILL.md", "/p/skills/old/c/SKILL.md",
                 "/p/prompts/x.md"];

  it("includes by plain globs, excludes by !, forces in by + and out by -", () => {
    const run = (patterns: string[]) => [...applyPatterns(files, patterns, base)].sort();
    expect(run([])).toEqual([...files].sort());
    expect(run(["skills/**"])).toEqual(files.slice(0, 3).sort());
    expect(run(["!b"])).toEqual([files[0]!, files[2]!, files[3]!].sort());
    expect(run(["!skills/old/**"])).toEqual([files[0]!, files[1]!, files[3]!].sort());
    expect(run(["!*", "+skills/b"])).toEqual([files[1]!]);
    expect(run(["+skills/b", "-skills/b"])).toEqual([files[0]!, files[2]!, files[3]!].sort());
    expect(run(["-/p/skills/a"])).toEqual(files.slice(1).sort());
    expect(run(["!x.md"])).toEqual(files.slice(0, 3).sort());
  });

  it("matches globs as minimatch's common forms", () => {
    expect(globMatch("skills/a/SKILL.md", "skills/**")).toBe(true);
    expect(globMatch("skills/a/SKILL.md", "skills/*")).toBe(false);
    expect(globMatch("skills/a", "skills/*")).toBe(true);
    expect(globMatch("a/b/c.md", "**/*.md")).toBe(true);
    expect(globMatch("c.md", "**/*.md")).toBe(true);
    expect(globMatch("ab.md", "a?.md")).toBe(true);
    expect(globMatch("a/b.md", "a?b.md")).toBe(false);
    expect(globMatch("a.mdx", "*.md")).toBe(false);
  });

  it("substitutes arguments as pi does, and splits them as a shell would", () => {
    expect(commandArgs("one \"two three\" 'four' ''")).toEqual(["one", "two three", "four", ""]);
    const args = ["a", "b", "c"];
    expect(substituteArgs("$1-$2-$9|$@|$ARGUMENTS", args)).toBe("a-b-|a b c|a b c");
    expect(substituteArgs("${2:-x}|${4:-x}|${@:2}|${@:2:1}|${@:-none}", args))
      .toBe("b|x|b c|b|a b c");
    expect(substituteArgs("${@:-none}|${ARGUMENTS:-none}", [])).toBe("none|none");
    expect(bodyOf("---\ndescription: d\n---\nThe body.")).toBe("The body.");
    expect(bodyOf("No frontmatter.")).toBe("No frontmatter.");
  });
});
