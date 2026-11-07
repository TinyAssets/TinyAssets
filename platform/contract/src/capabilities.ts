/**
 * The capabilities a run's MCP door can hold (ADR-034 decision 6): what an agent's
 * `agent.json` names as its `scopes` (`owner/src/agent-file.ts`), and what the owner's app
 * shows the owner of each (`doors/src/app.ts`).
 */

/** What a run's MCP door can hold: every door capability but code mode's (slice M). */
export const RUN_CAPABILITIES = Object.freeze(
  ["read_graph", "write_graph", "propose_policy", "read_files", "write_files", "converse",
   "run_graph"] as const);
export type RunCapability = (typeof RUN_CAPABILITIES)[number];
/** Each capability in plain words, for the owner's app: what a holder of it may do. */
export const CAPABILITY_WORDS: Readonly<Record<RunCapability, string>> = Object.freeze({
  read_graph: "Read rooms, the board and mail",
  write_graph: "Post to rooms and the board",
  propose_policy: "Propose changes to your settings",
  read_files: "Read your files",
  write_files: "Write your files",
  converse: "Talk to your agents",
  run_graph: "Start your agents' runs",
});
