/**
 * The cutover's manifest (untangle B24, `platform/cutover.json`): what the cutover deletes,
 * edits, moves and takes down, phase by phase, so the cutover's change is "do what it lists,
 * then delete it". `ci/guards/cutover.test.ts` holds it to the tree; the layer table
 * (`imports.ts`) reads the paths it deletes as the cutover layer.
 */
import manifest from "../../cutover.json" with { type: "json" };

/** The phases, in order: before mode `routes`, the cutover's change, the 14-day hold's end. */
export const PHASES = Object.freeze(["routes", "cutover", "hold"] as const);
export type Phase = (typeof PHASES)[number];

/** What a live entry is: an object the account or a vendor holds, not a path. */
export const LIVE = Object.freeze(["worker", "route", "bucket", "queue", "secret", "token",
                                   "dns", "address", "droplet"] as const);

/**
 * An entry, each with `what` it is:
 * - `delete`: a path, a file or a folder, deleted whole;
 * - `edit`: a file that stays, less the `names` it holds (each found in it today), and less
 *   its `imports` of what the cutover deletes (each an import it makes today);
 * - `move`: a file to `to`, or with `names`, those of it into the file `to`;
 * - `live`: an object of kind `live` by its `name`, which the file `in` names today.
 */
export type Entry =
  | { readonly delete: string; readonly what: string }
  | { readonly edit: string; readonly names: readonly string[];
      readonly imports?: readonly string[]; readonly what: string }
  | { readonly move: string; readonly to: string; readonly names?: readonly string[];
      readonly what: string }
  | { readonly live: (typeof LIVE)[number]; readonly name: string; readonly in: string;
      readonly what: string };

export interface Manifest {
  readonly about: string;
  readonly phases: Readonly<Record<Phase, { readonly why: string;
                                            readonly entries: readonly Entry[] }>>;
}

/** The manifest as checked in: its shape is the guard's to hold. */
export const MANIFEST = manifest as unknown as Manifest;

/**
 * The cutover layer: the paths the manifest deletes, whose files may import one another, and,
 * by the file that stays, the imports into them it names (an edit's `imports`), the only ones.
 */
export interface CutoverLayer {
  readonly gone: readonly string[];
  readonly entries: ReadonlyMap<string, readonly string[]>;
}

export function cutoverLayer(of: Manifest = MANIFEST): CutoverLayer {
  const all = PHASES.flatMap((phase) => of.phases[phase].entries);
  return {
    gone: all.flatMap((entry) => ("delete" in entry ? [entry.delete] : [])),
    entries: new Map(all.flatMap((entry) => ("edit" in entry && entry.imports !== undefined
      ? [[entry.edit, entry.imports] as const] : []))),
  };
}

/** Whether `path`, from the repository root, is one `gone` names or lies inside one. */
export function isGone(gone: readonly string[], path: string): boolean {
  return gone.some((each) => path === each || path.startsWith(`${each}/`));
}
