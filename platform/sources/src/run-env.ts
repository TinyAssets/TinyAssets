/**
 * What one engine run's environment is made from, by its record (`runEnv`): the sandbox
 * runner builds the run's process from it, and the owner's object hands it over with the run.
 */
import { forbids } from "../../contract/src/text";

import { type ApiKeyEnv, SourceRecordError, type Sources } from "./schema";
import type { CredentialKind } from "../../contract/src/sandbox";

/**
 * The process basics a run keeps from the sandbox template's own environment, beside
 * `HOME`, which the runner sets to the run's engine home (`{home}`): nothing else of the
 * sandbox's environment reaches a run. `NODE_EXTRA_CA_CERTS` is E2B's egress CA (slice S's
 * probe: E2B's proxy re-signs the HTTPS it sets the egress token on, and Node trusts only
 * its own bundle), so a Node engine's calls to the egress door verify.
 */
export const RUN_BASICS: readonly string[] = Object.freeze([
  "PATH", "LANG", "LC_ALL", "TERM", "TZ", "TMPDIR", "USER", "SHELL", "NODE_EXTRA_CA_CERTS",
]);

/**
 * What one engine run's environment is made from (`sandboxEnv`, `contract/src/egress.ts`): the
 * credential it runs on, the engine's own variables for each provider's key (an API-key
 * run's alone), and the variables that credential forbids.
 */
export interface RunEnv {
  readonly credential: CredentialKind;
  readonly api_key_env: Readonly<Record<string, ApiKeyEnv>>;
  readonly forbid_env: readonly string[];
  /**
   * The run's environment, as an allow-list: `HOME` at the engine home and the record's
   * own variables for this credential (its home, `env`, `no_update` and the credential's
   * own), each a template the runner fills. The runner starts from an empty environment
   * and sets these, the process basics it keeps (`inherit`) and, on an API key alone, the
   * key's variables (`sandboxEnv`); nothing else of the sandbox's environment reaches it.
   */
  readonly env: Readonly<Record<string, string>>;
  readonly inherit: readonly string[];
  /** On an API key, the header variable that carries the run's secret (`run_secret_env`). */
  readonly secret_header?: { readonly env: string; readonly value: string };
}

/**
 * The environment `engine` runs on `credential` with, from its record: refused (a
 * `SourceRecordError`) unless the engine is a served CLI record that runs on that
 * credential. A run on the vendor's own sign-in gets no API-key variables at all.
 */
export function runEnv(sources: Sources, engine: string, credential: CredentialKind): RunEnv {
  const record = sources.get(engine);
  const cli = record?.cli;
  if (record === undefined || cli === undefined || record.unwired !== undefined
      || !cli.credential.includes(credential)) {
    throw new SourceRecordError(`engine ${JSON.stringify(engine)} is not a served CLI ` +
                                `record that runs on ${credential}`);
  }
  const forbidEnv = cli.per_credential?.[credential]?.forbid_env ?? Object.freeze([]);
  const env: Record<string, string> = {
    HOME: "{home}", ...cli.home_env, ...cli.env,
    ...("env" in cli.no_update ? cli.no_update.env : {}),
    ...cli.per_credential?.[credential]?.env,
  };
  const forbidden = Object.keys(env).find((name) => forbids(forbidEnv, name));
  if (forbidden !== undefined) {
    throw new SourceRecordError(`engine ${JSON.stringify(engine)} sets ${forbidden}, which ` +
                                `${credential} forbids`);
  }
  return Object.freeze({
    credential,
    api_key_env: credential === "api_key" ? cli.api_key_env ?? Object.freeze({})
      : Object.freeze({}),
    forbid_env: forbidEnv,
    env: Object.freeze(env),
    inherit: RUN_BASICS,
    ...(credential === "api_key" && cli.run_secret_env !== undefined
      ? { secret_header: cli.run_secret_env } : {}),
  });
}
