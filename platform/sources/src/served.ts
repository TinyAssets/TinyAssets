/**
 * The served CLI records: the platform's records that carry a CLI, the engines a sandbox
 * runs, each checked whole as the catalog reads it, in id order. The template installs the
 * ones npm installs (`npmInstalled`), the runner, the probes and the owner sign in the ones
 * that sign in (`signsIn`), a run invokes one by id (`servedRecord`), and a job's sandbox
 * network is let out to one's vendor hosts (`engineHosts`), so no reader filters the
 * catalog for them again.
 */
import { bundledSources } from "./catalog";
import type { CliSpec, Install, LoginSpec, SourceRecord } from "./schema";

export type Served = SourceRecord & { readonly cli: CliSpec };

/** A served record that signs in by the vendor's own login in the sandbox. */
export type SignsIn = Served & { readonly cli: { readonly login: LoginSpec } };

/** A served record npm installs into the template. */
export type NpmInstalled = Served & {
  readonly cli: { readonly install: Extract<Install, { readonly manager: "npm" }> };
};

/** Every served CLI record, by id, in id order. */
export function served(): ReadonlyMap<string, Served> {
  const out = new Map<string, Served>();
  for (const id of [...bundledSources().ids()].sort()) {
    const record = servedRecord(id);
    if (record !== undefined) {
      out.set(id, record);
    }
  }
  return out;
}

/**
 * The served CLI record `id` names, checked whole as `served()` reads it, or undefined: one
 * record's read, for a reader that needs one engine (the owner, at each run and sign-in).
 */
export function servedRecord(id: string): Served | undefined {
  const record = bundledSources().get(id);
  return record?.cli === undefined ? undefined : record as Served;
}

/**
 * Whether it is a served record that signs in by the vendor's own login: it has a login
 * exactly when it runs on that credential (the record check, `cliSpec`).
 */
export function signsIn(record: Served | undefined): record is SignsIn {
  return record?.cli.login !== undefined;
}

export function npmInstalled(record: Served): record is NpmInstalled {
  return record.cli.install.manager === "npm";
}

/**
 * An engine's vendor hosts, its sign-in's and its model calls', and the chosen region's
 * (`cli.regions`) when it has regions, each once: what a job's sandbox network is let out to
 * for a job on its sign-in (the owner's start and sign-in hand them to the sandbox Worker),
 * and what the template's sign-in check runs under. An engine with no CLI, or a region it
 * lacks, throws.
 */
export function engineHosts(engine: string, region: string | null): string[] {
  const cli = servedRecord(engine)?.cli;
  if (cli === undefined) {
    throw new Error(`engine ${engine} has no CLI record`);
  }
  const chosen = region === null ? undefined : cli.regions?.[region];
  if (region !== null && chosen === undefined) {
    throw new Error(`engine ${engine} has no region ${region}`);
  }
  return [...new Set([...cli.hosts.login, ...cli.hosts.provider,
                      ...(chosen?.hosts.login ?? []), ...(chosen?.hosts.provider ?? [])])];
}
