/**
 * The owner's own trees (`OWNER_TREES`, Direction 3: abilities are editable files):
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
 * The trees and the stored-path grammar they are paths of are the contract's
 * (`contract/src/paths.ts`).
 */
import { OWNER_TREES, within } from "../../contract/src/paths";

/** The refusal of a change to an `OWNER_TREES` path under a grant the owner narrowed. */
export const AGENTS_OWNER_ONLY = "a grant the owner narrowed changes nothing under " +
  "/workspace/agents, /workspace/.agents, /workspace/skills, /workspace/prompts or " +
  "/workspace/goals, nor /workspace/USER.md";

/** Whether `path` is in a tree only the owner's own authority changes. */
export function ownersOnly(path: string): boolean {
  return OWNER_TREES.some((tree) => within(path, tree));
}
