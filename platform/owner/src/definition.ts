/**
 * An agent's definition, read for the doors to serve a main agent (founder's call 1,
 * `agent-definition-door.md`): `definitionRead`, the agent's `AGENTS.md` and `SOUL.md` and the
 * files of each skill of its union (`skillUnion`, the contract's, which the runner projects
 * too), as stored. The doors serve them as MCP resources, the `agent` prompt and the Skills
 * extension (`doors/src/definition.ts`); nothing here or there runs them.
 *
 * Which agent: the one the call acts as (`actingAgent`): a run's own; an outside client's, the
 * one its grant names (`main` until the owner names another); the owner's, `main`. One that is
 * not a live agent of the owner's is refused (`no_agent`, `agent_retired`), never served as
 * another. The read is held to the caller's grant by its row's capability (`read_files`), as
 * every file read is, there as that agent.
 */
import type { Door, DoorUnits } from "./door";
import type { Content, Files } from "./files";
import { DEFINITION_UNITS, ownerSlots } from "./holds";
import {
  AGENT_FILES, agentPath, type ServedDefinition, type ServedSkill, skillUnion,
} from "../../contract/src/agent-definition";
import { DEFINITION_BYTES_MAX, MAX_LIST } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import { slotted } from "../../contract/src/slots";

/** What `definitionRead` reaches: the owner's files, read in a unit, then their chunks. */
export interface DefinitionDoorNeeds extends Pick<DoorUnits, "door"> {
  readonly files: Pick<Files, "stat" | "list" | "open" | "openFile" | "range">;
  /** One of the owner's live agents by name, else refused (`no_agent`, `agent_retired`). */
  readonly ledger: { liveNative(agent: string): unknown };
}

export const DEFINITION_TOO_LARGE = `result_too_large: the agent's definition passes the ${
  DEFINITION_BYTES_MAX} bytes the doors serve`;

/** The entries directly in `dir`, every page; none when it is no directory. */
function entriesIn(files: DefinitionDoorNeeds["files"], dir: string) {
  if (files.stat(dir)?.kind !== "dir") {
    return [];
  }
  const entries = [];
  let after: string | null = null;
  do {
    const page = files.list(dir, after, MAX_LIST);
    entries.push(...page.entries);
    after = page.next;
  } while (after !== null);
  return entries;
}

/** Every file below `dir`, at any depth, by its path relative to `dir`. */
function filesBelow(files: DefinitionDoorNeeds["files"], dir: string, prefix = ""):
    [string, string][] {
  return entriesIn(files, dir).flatMap((entry) => {
    const name = `${prefix}${entry.path.slice(dir.length + 1)}`;
    return entry.kind === "dir" ? filesBelow(files, entry.path, `${name}/`)
      : [[name, entry.path] as [string, string]];
  });
}

const name = (path: string) => path.slice(path.lastIndexOf("/") + 1);

export const definitionDoors = Object.freeze({
  /**
   * The definition of the agent the caller acts as, a live one: its instructions and soul,
   * null when absent (a folder there refused, `Files.openFile`), and its skills' files, bytes
   * as stored. Pinned in one unit, then read; past `DEFINITION_BYTES_MAX` refused, never cut.
   */
  async definitionRead(o: DefinitionDoorNeeds, door: Door<undefined>):
      Promise<ServedDefinition> {
    const pinned = await o.door(() => {
      const { agent } = door.check();
      o.ledger.liveNative(agent);
      const skills = [...skillUnion(agent, {
        dirs: (dir) => entriesIn(o.files, dir).filter((entry) => entry.kind === "dir")
          .map((entry) => name(entry.path)),
        file: (path) => o.files.stat(path)?.kind === "file",
      })].map(([skill, dir]) => ({ skill, files: filesBelow(o.files, dir)
        .map(([path, stored]) => [path, o.files.open(stored)] as const) }));
      const instructions = o.files.openFile(agentPath(agent, AGENT_FILES.instructions));
      const soul = o.files.openFile(agentPath(agent, AGENT_FILES.soul));
      const all = [instructions, soul,
                   ...skills.flatMap((skill) => skill.files.map(([, content]) => content))];
      if (all.reduce((sum, content) => sum + (content?.entry.size ?? 0), 0)
          > DEFINITION_BYTES_MAX) {
        throw new Refusal("result_too_large", DEFINITION_TOO_LARGE);
      }
      return { agent, instructions, soul, skills };
    });
    const bytes = async (content: Content | null) => content === null ? null
      : content.inline ?? await o.files.range(content.chunks ?? [], 0, content.entry.size);
    return slotted(ownerSlots(), DEFINITION_UNITS, async () => {
      const skills: ServedSkill[] = [];
      for (const { skill, files } of pinned.skills) {
        const read: Record<string, Uint8Array> = {};
        for (const [path, content] of files) {
          read[path] = (await bytes(content))!;
        }
        skills.push({ name: skill, files: read });
      }
      return { agent: pinned.agent, instructions: await bytes(pinned.instructions),
               soul: await bytes(pinned.soul), skills };
    });
  },
});
