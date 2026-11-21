/**
 * The agent's definition at the MCP door (founder's call 1, `agent-definition-door.md`,
 * research §3): the definition of the agent the caller acts as (`definitionRead`: a run's own,
 * else `main`), served through every channel MCP has for it, from that one read.
 *
 * - **Resources**: its `AGENTS.md` and `SOUL.md` (`tinyassets://agent/<agent>/<file>`), and each
 *   skill's files (`skill://<skill>/<path>`, the Skills extension's own), its `SKILL.md` listed.
 * - **The `agent` prompt**: the two files embedded, and a link to each skill's `SKILL.md`, for a
 *   user to pull the definition in where a client shows prompts (each client needs that step).
 * - **The Skills extension** (`io.modelcontextprotocol/skills`, the SDK's `ext/skills`):
 *   `skills/list` and `skills/get` on 2026-07-28 requests, each file's digest that of the bytes
 *   `resources/read` serves, since both are built from the same bytes. A skill whose `SKILL.md`
 *   is not an Agent Skill's (its name and description in its frontmatter) is not one the
 *   extension serves; its files stay readable through the file tools.
 *
 * The read is made only when one of these methods is asked, once a request: a tool call reads
 * nothing more. It is held to the caller's grant (`read_files`) by its door, as every file read
 * is. Every byte is served as stored (Fact 9), text when it is UTF-8; nothing is run. The
 * `instructions` stay the platform's (`instructions.ts`).
 */
import {
  type GetPromptResult, type McpServer, type Resource, ResourceNotFoundError, ResourceTemplate,
} from "@modelcontextprotocol/server";
import {
  type Skill, SkillCatalogError, type SkillReadResult, SkillsExtension, skillEntry,
} from "@modelcontextprotocol/server/ext/skills";

import { AGENT_FILES, type ServedDefinition } from "../../contract/src/agent-definition";
import { sha256Hex } from "../../contract/src/digest";
import { utf8Decoder } from "../../contract/src/text";

import type { Bound } from "./binder";
import { AGENT_PROMPT } from "./instructions";
import { answered, asRequest } from "./refused";

const MARKDOWN = "text/markdown";
/** Where the definition's own files are served: `<base><agent>/<file>`. */
const FILES_URI = "tinyassets://agent/";

/** One file of the definition as a resource: where it is served, and its bytes. */
interface Served {
  readonly resource: Resource;
  readonly bytes: Uint8Array;
}

/** The definition as the door serves it: its two files and its skills, by URI. */
interface Definition {
  readonly files: readonly Served[];
  readonly skills: readonly Skill[];
  /** Every skill file's bytes, by its canonical URI. */
  readonly skillFiles: ReadonlyMap<string, Uint8Array>;
}

/** `uri` as the WHATWG URL parser serializes it, as the SDK compares URIs. */
function canonical(uri: string): string {
  return new URL(uri).href;
}

/** `bytes` as a resource's contents: text when they are UTF-8, else base64. */
function contents(uri: string, bytes: Uint8Array, mimeType?: string) {
  const typed = mimeType === undefined ? {} : { mimeType };
  try {
    return { uri, ...typed, text: utf8Decoder("keep").decode(bytes) };
  } catch {
    return { uri, ...typed, blob: Buffer.from(bytes).toString("base64") };
  }
}

/**
 * The served definition: its files' resources, and each skill's entry, which the SDK builds
 * from the bytes (`skillEntry`: digests, sizes, its frontmatter), each of the entry's URIs
 * mapped back to its bytes by its digest.
 */
async function definitionOf(served: ServedDefinition): Promise<Definition> {
  const files = ([[AGENT_FILES.instructions, served.instructions],
                  [AGENT_FILES.soul, served.soul]] as const)
    .flatMap(([name, bytes]) => bytes === null ? [] : [{
      resource: { uri: `${FILES_URI}${served.agent}/${name}`, name,
                  mimeType: MARKDOWN, size: bytes.byteLength },
      bytes }]);
  const skills: Skill[] = [];
  const skillFiles = new Map<string, Uint8Array>();
  for (const skill of served.skills) {
    let entry: Skill;
    try {
      entry = await skillEntry(skill.name, skill.files);
    } catch (error) {
      if (!SkillCatalogError.isInstance(error)) {
        throw error;
      }
      console.warn(`skill ${skill.name} is not served: ${
        error.problems.map((problem) => problem.message).join("; ")}`);
      continue;
    }
    const byDigest = new Map(Object.values(skill.files).map((bytes) =>
      [`sha256:${sha256Hex(bytes)}`, bytes]));
    for (const resource of entry.resources === "dynamic" ? [] : entry.resources) {
      skillFiles.set(canonical(resource.uri), byDigest.get(resource.digest)!);
    }
    skills.push(entry);
  }
  return { files, skills, skillFiles };
}

/**
 * Serve the definition on `mcp`: the resources, the `agent` prompt and the Skills extension,
 * each reading the caller's definition once, when first asked.
 */
export function serveDefinition(mcp: McpServer, bound: Bound): void {
  let read: Promise<Definition> | undefined;
  const definition = () => read ??= answered(asRequest, async () =>
    definitionOf(await bound.stub.definitionRead(bound.binding)));

  mcp.registerResource("definition", new ResourceTemplate(`${FILES_URI}{agent}/{file}`, {
    list: async () => ({ resources: (await definition()).files.map((file) => file.resource) }),
  }), { description: "The agent's definition: its AGENTS.md and SOUL.md." },
  async (uri) => {
    const file = (await definition()).files.find((each) =>
      canonical(each.resource.uri) === uri.href);
    if (file === undefined) {
      throw new ResourceNotFoundError(uri.href);
    }
    return { contents: [contents(file.resource.uri, file.bytes, MARKDOWN)] };
  });

  mcp.registerPrompt(AGENT_PROMPT.name, {
    title: AGENT_PROMPT.title, description: AGENT_PROMPT.description,
  }, async (): Promise<GetPromptResult> => {
    const { files, skills } = await definition();
    return {
      messages: [
        ...files.map((file) => ({ role: "user" as const, content: {
          type: "resource" as const,
          resource: contents(file.resource.uri, file.bytes, MARKDOWN) } })),
        ...skills.map((skill) => ({ role: "user" as const, content: {
          type: "resource_link" as const, uri: skill.uri, name: skill.frontmatter.name,
          description: skill.frontmatter.description, mimeType: MARKDOWN } })),
      ],
    };
  });

  new SkillsExtension({
    source: {
      list: async () => ({ skills: [...(await definition()).skills] }),
      get: async (uri) => (await definition()).skills.find((skill) =>
        canonical(skill.uri) === canonical(uri)),
      read: async (uri): Promise<SkillReadResult | undefined> => {
        const bytes = (await definition()).skillFiles.get(canonical(uri));
        if (bytes === undefined) {
          return undefined;
        }
        const served = contents(uri, bytes);
        return "text" in served ? { text: served.text } : { bytes };
      },
      listResources: async () => (await definition()).skills.map((skill) => ({
        uri: skill.uri, name: skill.frontmatter.name,
        description: skill.frontmatter.description, mimeType: MARKDOWN })),
    },
  }).attach(mcp);
}
