/**
 * The owner's agent trees (Direction 3: abilities are editable files), which the owner's
 * object and the sandbox's runner both hold a path to: the agent definitions, the abilities'
 * trees that reach every engine's instructions, the owner's goals and the owner's profile.
 */

/** The owner's agent definitions, `/workspace/agents/<agent>/`: the owner's alone to change. */
export const AGENTS_DIR = "/workspace/agents";
/** The agent's abilities: its `SKILL.md` folders, which every engine discovers itself. */
export const ABILITIES_DIR = "/workspace/.agents";
export const SKILLS_DIR = "/workspace/skills";
export const PROMPTS_DIR = "/workspace/prompts";
/** The abilities' trees, which reach an engine's instructions: every run reads them. */
export const ABILITY_TREES: readonly string[] = Object.freeze([
  ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR]);
/**
 * The owner's goals, one page a goal: what its agents may do toward each without asking is
 * the goal's Agreed section, so a goal is the owner's own word, as an agent's definition is.
 */
export const GOALS_DIR = "/workspace/goals";
/** The owner's profile: its directives are every agent's standing preferences. */
export const PROFILE = "/workspace/USER.md";
/**
 * Every tree only the owner's own authority changes: the agent definitions, the abilities'
 * trees, the goals and the profile. A grant the owner narrowed changes nothing in them
 * (`owner/src/paths.ts`'s `ownersOnly`).
 */
export const OWNER_TREES: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR, GOALS_DIR, PROFILE]);
/**
 * What an outside client's sandbox holds read-only and never sends back: the agent
 * definitions and the abilities' trees, its always-read set (owner-object spec, a client's
 * sandbox). Its goals and the owner's profile are not in it: a client under a full grant
 * may write them, and the owner's object holds a narrowed one to `OWNER_TREES`.
 */
export const CLIENT_READ_ONLY: readonly string[] = Object.freeze([
  AGENTS_DIR, ABILITIES_DIR, SKILLS_DIR, PROMPTS_DIR]);
