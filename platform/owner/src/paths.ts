/**
 * The owner's agent definitions (`OWNER_TREES`, Direction 3: abilities are editable files):
 * `/workspace/agents` (each agent's `AGENTS.md` and `agent.json`), and the agent's abilities,
 * which reach an engine's instructions (A5; ADR-034 decision 2): `/workspace/.agents` (its
 * skills), `/workspace/skills` and `/workspace/prompts`; and the owner's goals,
 * `/workspace/goals`, which say what every agent may do unasked (a goal's Agreed section);
 * and the owner's profile, `/workspace/USER.md`, whose directives every agent follows as the
 * owner's standing preferences (the founder, 2026-11-04, on review Low 3).
 * The founder's rule (2026-11-03):
 * every agent of the owner has full power by default to do what any other can, so the owner,
 * its runs and any agent under a full grant (the default) create, change, move or remove
 * anything there, and a grant the owner narrowed never does (held by the file doors and the
 * sync). The owner's own app changes them only in its live session. The roots are never
 * removed or renamed, so no change outside the trees reaches into them.
 *
 * The stored-path grammar these are paths of is the contract's (`contract/src/paths.ts`).
 */
import { within } from "../../contract/src/paths";

/** The owner's agent definitions, `/workspace/agents/<agent>/`: the owner's alone to change. */
export const AGENTS_DIR = "/workspace/agents";
/** The agent's abilities: its `SKILL.md` folders, which every engine discovers itself. */
export const ABILITIES_DIR = "/workspace/.agents";
/** The abilities' trees, which reach an engine's instructions: every run reads them. */
export const ABILITY_TREES: readonly string[] = Object.freeze([
  ABILITIES_DIR, "/workspace/skills", "/workspace/prompts"]);
/**
 * The owner's goals, one page a goal: what its agents may do toward each without asking is
 * the goal's Agreed section, so a goal is the owner's own word, as an agent's definition is.
 */
export const GOALS_DIR = "/workspace/goals";
/** The owner's profile: its directives are every agent's standing preferences. */
export const PROFILE = "/workspace/USER.md";
/** Every tree only the owner's own authority changes. */
export const OWNER_TREES: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, "/workspace/skills", "/workspace/prompts", GOALS_DIR, PROFILE]);
/** The refusal of a change to an `OWNER_TREES` path under a grant the owner narrowed. */
export const AGENTS_OWNER_ONLY = "a grant the owner narrowed changes nothing under " +
  "/workspace/agents, /workspace/.agents, /workspace/skills, /workspace/prompts or " +
  "/workspace/goals, nor /workspace/USER.md";

/** Whether `path` is in a tree only the owner's own authority changes. */
export function ownersOnly(path: string): boolean {
  return OWNER_TREES.some((tree) => within(path, tree));
}
