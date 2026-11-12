/**
 * The starter package as the owner reads it (`platform/starter/`, carried as `starter.json`):
 * a version, its files, each `managed` or `personal`, and the paths it once shipped and
 * dropped, checked whole. The one reading of the package, for the owner's object, which seeds
 * and updates by it (`starter.ts`), and for the importer, which counts what the seed writes
 * beside an imported tree (`import/build.ts`).
 */
import starterJson from "../../starter/starter.json" with { type: "json" };

import { agentFile } from "./agent-file";
import { Refusal } from "../../contract/src/refusal";
import { ownersOnly } from "./paths";
import { storedPath, within, WORKSPACE } from "../../contract/src/paths";

/**
 * The most paths a package may name, its files and the paths it once shipped and dropped
 * (`retired`) together: the receipts' bound (one row a path; the owner-bound guard).
 */
export const STARTER_FILES_MAX = 64;

/** The name beside a file that holds its newer version, which no engine loads. */
export const NEWER = ".ta-new";

export type StarterClass = "managed" | "personal";

export interface StarterFile {
  readonly path: string;
  readonly class: StarterClass;
  /** The version whose change to this file widens what the agent may do, and why. */
  readonly widens?: { readonly version: number; readonly why: string };
  /**
   * A personal file's earlier path, retired: an owner who holds a changed file there is
   * seeded here with a copy of it (the old file is left as it is) and told once.
   */
  readonly moved_from?: string;
  readonly text: string;
}

export interface StarterPackage {
  readonly version: number;
  readonly files: readonly StarterFile[];
  /**
   * Paths an earlier version shipped and this one does not: their receipts are kept, so a
   * file the owner deleted stays deleted if a later version ships its path again.
   */
  readonly retired: readonly string[];
}

/**
 * A package checked whole: a positive version, its files and retired paths at most
 * `STARTER_FILES_MAX` together and none twice, `agent.json` last.
 */
export function starterPackage(value: unknown): StarterPackage {
  const pkg = value as { version?: unknown; files?: unknown; retired?: unknown };
  const retired = pkg?.retired ?? [];
  if (typeof pkg !== "object" || pkg === null || !Number.isSafeInteger(pkg.version)
      || (pkg.version as number) < 1 || !Array.isArray(pkg.files) || pkg.files.length < 1
      || !Array.isArray(retired)
      || pkg.files.length + retired.length > STARTER_FILES_MAX) {
    throw new Refusal("invalid",
                      `a starter package is a version and 1 to ${STARTER_FILES_MAX} files ` +
                      "and retired paths");
  }
  const paths = new Set<string>();
  const files = pkg.files.map((file: Partial<StarterFile>): StarterFile => {
    const path = storedPath(file.path);
    // A managed file is instructions, in the owner's trees; a personal one (the profile, the
    // workspace's map) is data, anywhere in the workspace, the owner's to change. Who else
    // may write it is the agent trees' rule: the profile is in `OWNER_TREES`, so a grant the
    // owner narrowed never writes it, and the map is not.
    if (!within(path, WORKSPACE) || path === WORKSPACE || path.endsWith(NEWER)
        || paths.has(path) || (file.class !== "managed" && file.class !== "personal")
        || (file.class === "managed" && !ownersOnly(path)) || typeof file.text !== "string") {
      throw new Refusal("invalid",
                        `starter file ${String(file.path)} is a distinct path in the workspace ` +
                        "(a managed one in the owner's trees), managed or personal, with its " +
                        "text");
    }
    if (file.moved_from !== undefined && (file.class !== "personal"
        || !(Array.isArray(retired) && retired.includes(file.moved_from)))) {
      throw new Refusal("invalid",
                        `starter file ${path} moved from a retired path, and is personal`);
    }
    const widens = file.widens;
    if (widens !== undefined && (!Number.isSafeInteger(widens.version) || widens.version < 2
                                 || widens.version > (pkg.version as number)
                                 || typeof widens.why !== "string" || widens.why === "")) {
      throw new Refusal("invalid", `starter file ${path}'s widens names a later version and why`);
    }
    paths.add(path);
    return Object.freeze({ path, class: file.class, text: file.text,
                           ...(widens === undefined ? {} : { widens }),
                           ...(file.moved_from === undefined ? {}
                             : { moved_from: storedPath(file.moved_from) }) });
  });
  const agents = files.filter((file) => file.path.endsWith("/agent.json"));
  if (agents.length > 0 && files.at(-1) !== agents.at(-1)) {
    throw new Refusal("invalid", "a starter package writes agent.json last");
  }
  for (const agent of agents) {
    agentFile(agent.text);
  }
  const gone = retired.map((path: unknown) => {
    const at = storedPath(path);
    if (paths.has(at)) {
      throw new Refusal("invalid", `starter path ${at} is retired and shipped at once`);
    }
    paths.add(at);
    return at;
  });
  return Object.freeze({ version: pkg.version as number, files: Object.freeze(files),
                         retired: Object.freeze(gone) });
}

/** The package this code carries, checked. */
export function carried(): StarterPackage {
  return starterPackage(starterJson);
}
