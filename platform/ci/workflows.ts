/** The repository's workflows, parsed, for the guards that hold their shape. */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { parse } from "yaml";

import { PLATFORM, REPO } from "./paths.ts";

export const WORKFLOWS = join(REPO, ".github", "workflows");

// The workflow YAML is untyped data: each guard reads it field by field.
export type Data = any;

export function workflow(name: string): Data {
  return parse(readFileSync(join(WORKFLOWS, name), "utf8"));
}

/** Every workflow file's name, sorted. */
export function workflowNames(): string[] {
  return readdirSync(WORKFLOWS).filter((name) => /\.ya?ml$/.test(name)).sort();
}

export function json(path: string): Data {
  return JSON.parse(readFileSync(join(PLATFORM, path), "utf8"));
}
