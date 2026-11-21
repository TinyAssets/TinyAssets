/**
 * The skills projection (§2.1 step 4.3, Direction 3): the agent's skills are the union of its
 * own (`<agent>/.agents/skills`), the owner's shared abilities (`/workspace/.agents/skills`)
 * and the owner's skills (`/workspace/skills`), the agent's own winning a name both hold, then
 * the shared abilities (`skillsOf`). Each skills path an engine's record names
 * (`.claude/skills`, `.agents/skills`), in the agent's directory, holds a link per skill; an
 * engine given each skill by its argv (`skills.argv_each`) is handed `skillsOf`'s directories.
 * A client's sandbox holds only what its feed let it read, so it projects only that. Made by
 * the sync agent's own user, never root; links are never synced, and the last projection's
 * are taken away first.
 */
import { existsSync, lstatSync, readdirSync, symlinkSync, unlinkSync } from "node:fs";
import { join, relative } from "node:path";

import { AGENT_FILES } from "../../contract/src/agent-definition";
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

/** Each skill a source holds: a directory with a `SKILL.md`, by name, in name order. */
function skillsIn(dir: string): string[] {
  if (!isDir(dir)) {
    return [];
  }
  return readdirSync(dir).filter((name) => !name.startsWith(".") && isDir(join(dir, name))
    && existsSync(join(dir, name, "SKILL.md"))).sort();
}

/**
 * The agent's skills (`workspace` the local `/workspace`): each one's directory by its name,
 * the agent's own first, then the shared abilities', then the owner's.
 */
export function skillsOf(workspace: string, agent: string): Map<string, string> {
  const local = (path: string) => join(workspace, relative(WORKSPACE, path));
  const sources = [join(local(AGENTS_DIR), agent, AGENT_FILES.skills),
                   join(local(ABILITIES_DIR), "skills"), local(SKILLS_DIR)];
  const chosen = new Map<string, string>();
  for (const source of sources) {
    for (const name of skillsIn(source)) {
      if (!chosen.has(name)) {
        chosen.set(name, join(source, name));
      }
    }
  }
  return chosen;
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
  const agentDir = join(workspace, relative(WORKSPACE, AGENTS_DIR), agent);
  const chosen = skillsOf(workspace, agent);
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
