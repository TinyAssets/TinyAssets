/**
 * The skills projection (§2.1 step 4.3, Direction 3; slice S review, the shared
 * abilities): each skills path an engine's record names (`.claude/skills`, `.pi/skills`,
 * `.agents/skills`), in the agent's directory, holds a link per skill to the union of the
 * agent's own skills (`<agent>/.agents/skills`), the owner's shared abilities
 * (`/workspace/.agents/skills`) and the owner's skills (`/workspace/skills`), the agent's
 * own winning a name both hold, then the shared abilities. A client's sandbox holds only
 * what its feed let it read, so it projects only that. Made by the sync agent's own user,
 * never root; links are never synced, and the last projection's are taken away first.
 */
import { existsSync, lstatSync, readdirSync, symlinkSync, unlinkSync } from "node:fs";
import { join, relative } from "node:path";

import { ABILITIES_DIR, AGENTS_DIR, SKILLS_DIR, WORKSPACE } from "../../contract/src/paths";

import { safeDirs } from "./fsafe";

function isDir(path: string): boolean {
  try {
    return lstatSync(path).isDirectory();
  } catch {
    return false;
  }
}

function isLink(path: string): boolean {
  try {
    return lstatSync(path).isSymbolicLink();
  } catch {
    return false;
  }
}

/** Each skill a source holds: a directory with a `SKILL.md`, by name. */
function skillsIn(dir: string): string[] {
  if (!isDir(dir)) {
    return [];
  }
  return readdirSync(dir).filter((name) => !name.startsWith(".") && isDir(join(dir, name))
    && existsSync(join(dir, name, "SKILL.md")));
}

/**
 * The agent's projection made (`workspace` the local `/workspace`), the links made returned
 * for the next projection to take away; `previous` the last one's.
 */
export function project(workspace: string, agent: string, skillsPaths: readonly string[],
                        previous: readonly string[]): string[] {
  for (const link of previous) {
    if (isLink(link)) {
      unlinkSync(link);
    }
  }
  const local = (path: string) => join(workspace, relative(WORKSPACE, path));
  const agentDir = join(local(AGENTS_DIR), agent);
  const sources = [join(agentDir, ".agents", "skills"), join(local(ABILITIES_DIR), "skills"),
                   local(SKILLS_DIR)];
  const chosen = new Map<string, string>();
  for (const source of sources) {
    for (const name of skillsIn(source)) {
      if (!chosen.has(name)) {
        chosen.set(name, join(source, name));
      }
    }
  }
  const made: string[] = [];
  for (const path of skillsPaths) {
    const target = join(agentDir, path);
    safeDirs(agentDir, target, 0o2775);
    for (const [name, skill] of chosen) {
      const at = join(target, name);
      if (skill === at || existsSync(at) || isLink(at)) {
        continue;  // the skill itself (an engine reading `.agents/skills`), or one there
      }
      symlinkSync(skill, at);
      made.push(at);
    }
  }
  return made;
}
