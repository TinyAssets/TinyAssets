/**
 * The cutover's manifest (`platform/cutover.json`): what the cutover deletes, edits, moves and
 * takes down, phase by phase, so the cutover's change is "do what it lists, then delete it".
 * `ci/guards/cutover.test.ts` holds it to the tree; the layer table (`imports.ts`) reads the
 * paths it deletes as the cutover layer.
 */
import manifest from "../../cutover.json" with { type: "json" };

/** The phases, in order: before mode `routes`, the cutover's change, the 14-day hold's end. */
export const PHASES = Object.freeze(["routes", "cutover", "hold"] as const);
export type Phase = (typeof PHASES)[number];

/** What a live entry is: an object the account or a vendor holds, not a path. */
export const LIVE = Object.freeze(["worker", "route", "bucket", "queue", "secret", "token",
                                   "dns", "address", "droplet", "workflow", "tunnel",
                                   "record", "volume", "image", "webhook",
                                   "redirect"] as const);

/**
 * An entry, each with `what` it is:
 * - `delete`: a path, a file or a folder, deleted whole;
 * - `edit`: a file that stays, less each of the `names` it holds today (`edited`), and less
 *   its `imports`: each file the cutover deletes that it loads today, by an import or by a
 *   path it hands a tool (a test config's setup file);
 * - `move`: a file to `to`, or with `names`, those of it into the file `to`; inside a path
 *   the cutover deletes, it is a carry-out, made before the deletes;
 * - `live`: an object of kind `live` by its `name`, which the file `in` names today;
 * - `keep`: a path that stays as it is although it names what goes, and why.
 */
export type Entry =
  | { readonly delete: string; readonly what: string }
  | { readonly edit: string; readonly names: readonly string[];
      readonly imports?: readonly string[]; readonly what: string }
  | { readonly move: string; readonly to: string; readonly names?: readonly string[];
      readonly what: string }
  | { readonly live: (typeof LIVE)[number]; readonly name: string; readonly in: string;
      readonly what: string }
  | { readonly keep: string; readonly what: string };

/**
 * `text`, the file at `path`, less each of `names` wherever it stands. A name that is whole
 * lines (a name that starts with its indent, only where a line does) goes with the block it
 * heads: a Markdown heading's section, a TOML table, or else the lines after it indented
 * deeper (a YAML key's value, a list item, a Python body), with the line at its indent that
 * closes it (`)`, `}`, `fi`). Any other name goes as its text.
 */
export function edited(path: string, text: string, names: readonly string[]): string {
  let out = "";
  let from = 0;
  for (const [start, end] of reaches(path, text, names)) {
    out += text.slice(from, Math.max(from, start));
    from = Math.max(from, end);
  }
  return out + text.slice(from);
}

/** What a move with `names` takes from `text`, the file at `path`, into its `to`. */
export function taken(path: string, text: string, names: readonly string[]): string {
  return reaches(path, text, names).map(([start, end]) => text.slice(start, end)).join("\n")
    .replace(/\n*$/, "\n");
}

/** Where each of `names` reaches in `text` (`edited`), as offsets, in order. */
function reaches(path: string, text: string,
                 names: readonly string[]): (readonly [number, number])[] {
  const lines = text.split("\n");
  const starts: number[] = [];
  lines.reduce((start, line) => (starts.push(start), start + line.length + 1), 0);
  const lineAt = (offset: number) => starts.findLastIndex((start) => start <= offset);
  const indent = (line: string) => /^\s*/.exec(line)![0].length;
  const heading = (line: string) => path.endsWith(".md") ? /^(#{1,6}) /.exec(line)?.[1]!.length
    : path.endsWith(".toml") && line.startsWith("[") ? 0 : undefined;
  const cut: (readonly [number, number])[] = [];
  for (const name of names) {
    for (let at = text.indexOf(name); at !== -1; at = text.indexOf(name, at + 1)) {
      const first = lineAt(at);
      const last = lineAt(at + name.length - 1);
      const before = text.slice(starts[first], at);
      if (/^\s/.test(name) && before !== "") {
        continue;
      }
      if (before.trim() !== "" || lines[last]!.slice(at + name.length - starts[last]!).trim()
          !== "") {
        cut.push([at, at + name.length]);
        continue;
      }
      const level = heading(lines[first]!);
      let end = last + 1;
      for (let next = end; next < lines.length; next += 1) {
        const line = lines[next]!;
        if (level !== undefined ? (heading(line) ?? 7) <= level
          : line.trim() !== "" && indent(line) <= indent(lines[first]!)) {
          break;
        }
        if (level !== undefined || line.trim() !== "") {
          end = next + 1;
        }
      }
      if (level === undefined && end < lines.length
          && indent(lines[end]!) === indent(lines[first]!)
          && /^(?:[)\]}]+[,;]?|fi|done|esac)$/.test(lines[end]!.trim())) {
        end += 1;
      }
      cut.push([starts[first]!, end < lines.length ? starts[end]! : text.length]);
    }
  }
  return cut.sort(([a], [b]) => a - b);
}

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
  // A file edited in two phases names its imports in each: the layer takes both.
  const entries = new Map<string, readonly string[]>();
  for (const entry of all) {
    if ("edit" in entry && entry.imports !== undefined) {
      entries.set(entry.edit, [...entries.get(entry.edit) ?? [], ...entry.imports]);
    }
  }
  return { gone: all.flatMap((entry) => ("delete" in entry ? [entry.delete] : [])), entries };
}

/** Whether `path`, from the repository root, is one `gone` names or lies inside one. */
export function isGone(gone: readonly string[], path: string): boolean {
  return gone.some((each) => path === each || path.startsWith(`${each}/`));
}

/**
 * The files `source`, a Python module at `from`, imports by name (`import a.b`, `from a.b import
 * c`): each module, or a module it imports from a package, found in `from`'s folder or a
 * folder above it, as the scripts put their own folder or the root on the path.
 */
export function pythonLoads(from: string, source: string,
                            has: (path: string) => boolean): string[] {
  return [...new Set(pythonModules(from, source, has).values())].sort();
}

/** The file each module `source` imports by name is (`pythonLoads`), by its dotted name. */
export function pythonModules(from: string, source: string,
                              has: (path: string) => boolean): Map<string, string> {
  const first = (list: string) => list.split(",").map((each) => each.trim().split(/\s+/)[0]!)
    .filter((each) => each !== "");
  const modules: string[] = [];
  for (const [, module, names, plain] of source.matchAll(
    /^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import[ \t]+\(?([\w, \t]+)|import[ \t]+([\w., \t]+))/gm)) {
    modules.push(...(plain !== undefined ? first(plain)
      : [module!, ...first(names!).map((name) => `${module}.${name}`)]));
  }
  const parts = from.split("/").slice(0, -1);
  const folders = parts.map((_, at) => parts.slice(0, parts.length - at).join("/")).concat([""]);
  const found = new Map<string, string>();
  for (const module of modules) {
    const path = module.replaceAll(".", "/");
    const file = folders.map((folder) => folder === "" ? path : `${folder}/${path}`)
      .flatMap((at) => [`${at}.py`, `${at}/__init__.py`]).find(has);
    if (file !== undefined) {
      found.set(module, file);
    }
  }
  return found;
}

/** A leg of `platform-tests`' matrix: its name and the one command it runs. */
export interface Leg {
  readonly leg: string;
  readonly command: string;
}

/** The legs the dry run runs: `platform-tests`' own, less each of a workspace that goes. */
export function dryRunLegs(legs: readonly Leg[], of: Manifest = MANIFEST): Leg[] {
  const { gone } = cutoverLayer(of);
  return legs.filter(({ command }) => {
    const ws = / -w (\S+)/.exec(command)?.[1];
    if (ws === undefined) {
      throw new Error(`platform-tests' leg ${command} names no workspace`);
    }
    return !isGone(gone, `platform/${ws}`);
  });
}

/** A test file: what a suite runs, never what another file loads. */
export const TEST_FILE = /\.test\.[cm]?[jt]s$|(?:^|\/)tests\/test_[^/]+\.py$/;

/**
 * The tree the dry run makes of the checkout (`ci/dry-run.ts`, `tests.yml` `cutover-dry-run`):
 * all three phases' deletes and carry-outs, which need no hand, so whatever stays and is no edit
 * is shown to need nothing that goes. An edit is the cutover's to write, so what it still
 * loads stays (`kept`, with all that loads in turn), and a test it edits is set aside
 * (`setAside`). A move into a file that stays (`names`, or a move of a file nothing deletes)
 * re-points its importers, which is the cutover's too, and is not made.
 */
export interface DryRun {
  /** Each carry-out, from and to, made before the deletes. */
  readonly carried: readonly (readonly [string, string])[];
  /** What an edit loads that the manifest deletes, and what that loads, kept. */
  readonly kept: readonly string[];
  /** Every other tracked file the manifest deletes. */
  readonly removed: readonly string[];
  /** The test files the manifest edits. */
  readonly setAside: readonly string[];
}

/**
 * The dry run over `tracked`, the checkout's files from the repository root, each file's
 * loads by `loads` (`imports.ts` `relativeLoads`).
 */
export function dryRun(tracked: readonly string[], loads: (path: string) => readonly string[],
                       of: Manifest = MANIFEST): DryRun {
  const { gone, entries } = cutoverLayer(of);
  const all = PHASES.flatMap((phase) => of.phases[phase].entries);
  const kept = new Set<string>();
  const queue = [...entries.values()].flat();
  while (queue.length > 0) {
    const path = queue.pop()!;
    if (!kept.has(path) && isGone(gone, path)) {
      kept.add(path);
      queue.push(...loads(path));
    }
  }
  const carried = all.flatMap((entry) => "move" in entry && entry.names === undefined
    && isGone(gone, entry.move) ? [[entry.move, entry.to] as const] : []);
  const from = new Set(carried.map(([path]) => path));
  const setAside = [...new Set(all.flatMap((entry) => "edit" in entry
    && TEST_FILE.test(entry.edit) ? [entry.edit] : []))].sort();
  return {
    carried, kept: [...kept].sort(), setAside,
    removed: tracked.filter((path) => isGone(gone, path) && !kept.has(path) && !from.has(path)),
  };
}
