/**
 * An agent's `agent.json` (ADR-034 decision 2): the one small file beside its `AGENTS.md`,
 * skills and MCP config under `/workspace/agents/<agent>/`, the owner's alone to change
 * (`OWNER_TREES`). It names the agent's engine, model and MCP scopes, and nothing about any
 * provider (Direction 2):
 *
 * - `engine`: a CLI source record's id (`claude-code`, `codex`, `pi`, ...), or `auto`, the
 *   owner's strongest connected source that has an engine (Direction 4);
 * - `credential` (optional): `vendor_login_in_sandbox` or `api_key`, when the agent pins
 *   which the engine runs on;
 * - `connection` (optional): the owner's connection an API-key run spends on;
 * - `model` (optional): the model the engine is asked for;
 * - `scopes`: the capabilities its runs' MCP door holds (`RUN_CAPABILITIES`), which an
 *   owner-started or scheduled run gets whole and a client-started run only where that
 *   client's grant holds them too (ADR-034 decision 6);
 * - `enabled` (optional, true by default): false refuses every run (`AGENT_DISABLED`).
 *
 * Nothing defaults an agent into being: a missing or malformed file refuses its run
 * (`AGENT_UNDEFINED`, `Invalid`), never a stand-in.
 */
import { CONNECTION_ID } from "../../contract/src/ids";
import { CREDENTIAL_KINDS, type CredentialKind } from "../../sources/src/schema";

import { RUN_CAPABILITIES, type RunCapability } from "../../contract/src/capabilities";
import { Invalid } from "../../contract/src/errors";
import { isPrintable } from "../../contract/src/text";

export const AUTO = "auto";
export const AGENT_UNDEFINED = "agent_undefined: the agent has no agent.json";
export const AGENT_DISABLED = "agent_disabled: the agent is disabled in its agent.json";

const KEYS: readonly string[] = Object.freeze(["connection", "credential", "enabled", "engine",
                                               "model", "scopes"]);
const RECORD = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export interface AgentFile {
  readonly engine: string;
  readonly credential: CredentialKind | null;
  readonly connection: string | null;
  readonly model: string | null;
  readonly scopes: readonly RunCapability[];
  readonly enabled: boolean;
}

/** The path of `agent`'s file. */
export function agentFilePath(agent: string): string {
  return `/workspace/agents/${agent}/agent.json`;
}

/** The file's text, parsed and checked whole, or `Invalid`. */
export function agentFile(text: string): AgentFile {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Invalid("agent.json is not JSON");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || !Object.keys(value).every((key) => KEYS.includes(key))) {
    throw new Invalid(`agent.json holds only ${KEYS.join(", ")}`);
  }
  const file = value as Record<string, unknown>;
  const { engine, credential, connection, model, scopes, enabled } = file;
  if (typeof engine !== "string" || !RECORD.test(engine)) {
    throw new Invalid("agent.json's engine is a source record's id, or auto");
  }
  if (credential !== undefined && !CREDENTIAL_KINDS.includes(credential as CredentialKind)) {
    throw new Invalid(`agent.json's credential is one of ${CREDENTIAL_KINDS.join(", ")}`);
  }
  if (connection !== undefined && (typeof connection !== "string"
                                   || !CONNECTION_ID.test(connection))) {
    throw new Invalid("agent.json's connection is a connection id");
  }
  if (connection !== undefined && credential === "vendor_login_in_sandbox") {
    throw new Invalid("a run on the vendor's own sign-in is on no connection");
  }
  if (model !== undefined && (typeof model !== "string" || model.length < 1
                              || model.length > 256 || !isPrintable(model))) {
    throw new Invalid("agent.json's model is 1 to 256 printable characters");
  }
  if (!Array.isArray(scopes) || new Set(scopes).size !== scopes.length
      || !scopes.every((scope) => (RUN_CAPABILITIES as readonly unknown[]).includes(scope))) {
    throw new Invalid(`agent.json's scopes are distinct capabilities of ${
      RUN_CAPABILITIES.join(", ")}`);
  }
  if (enabled !== undefined && typeof enabled !== "boolean") {
    throw new Invalid("agent.json's enabled is true or false");
  }
  return Object.freeze({
    engine, credential: (credential as CredentialKind | undefined) ?? null,
    connection: (connection as string | undefined) ?? null,
    model: (model as string | undefined) ?? null,
    scopes: Object.freeze([...scopes] as RunCapability[]), enabled: enabled ?? true,
  });
}
