/**
 * The skills projection (§2.1 step 4.3, Direction 3): the agent's skills are its skill union
 * (`skillUnion`, the contract's, which the doors serve a main agent too), read from the local
 * `/workspace` (`skillsOf`). Each skills path an engine's record names (`.claude/skills`,
 * `.agents/skills`), in the agent's directory, holds a link per skill; an engine given each
 * skill by its argv (`skills.argv_each`) is handed `skillsOf`'s directories. A client's sandbox
 * holds only what its feed let it read, so it projects only that. Made by the sync agent's own
 * user, never root; links are never synced, and the last projection's are taken away first.
 */
import { existsSync, lstatSync, readdirSync, symlinkSync, unlinkSync } from "node:fs";
import { join, relative } from "node:path";

import { skillUnion } from "../../contract/src/agent-definition";
import { AGENTS_DIR, WORKSPACE } from "../../contract/src/paths";

import { safeDirs } from "./fsafe";

function isDir(path: string): boolean {
  try {
    return lstatSync(path).isDirectory();
  } catch {
    return false;
  }
}

function isFile(path: string): boolean {
  try {
    return lstatSync(path).isFile();
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

/**
 * The agent's skills (`workspace` the local `/workspace`): each one's local directory by its
 * name, in the union's order.
 */
export function skillsOf(workspace: string, agent: string): Map<string, string> {
  const local = (path: string) => join(workspace, relative(WORKSPACE, path));
  const union = skillUnion(agent, {
    dirs: (dir) => isDir(local(dir))
      ? readdirSync(local(dir)).filter((name) => isDir(join(local(dir), name))) : [],
    file: (path) => isFile(local(path)),
  });
  return new Map([...union].map(([name, dir]) => [name, local(dir)]));
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
