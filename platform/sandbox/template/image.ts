/**
 * The sandbox's E2B image, as data (`spec`), and its one version (`tag`). `image.py` turns
 * the spec into E2B's template steps and builds it; nothing else of the image is anywhere
 * else. The image is E2B's own base image with: Node from nodejs.org, pinned by its published
 * SHA-256; every served npm engine record's CLI, installed from the lock committed beside it
 * (`platform/sources/locks/<id>/`, its digest checked against the record's `lock_sha256`);
 * the pool of run users (`ta-run-<n>`, as many as the most live runs an owner may hold,
 * `RUNS_CEILING`, each its own group, in group `ta`, no home: the runner makes each run's)
 * and the sync agent's user (`ta-sync`, its directory 0700), none able to `sudo` (checked);
 * the shared workspace (root:ta 2775); the runner's loader (`ta-runner`, root's, 0700) and
 * the empty directory it installs runners into; and the root-only state directory, its
 * `incoming` the loader's uploads. The names and paths are the runner's own (`protocol.ts`,
 * `users.ts`, `engine.ts`), so the image and the runner name each once; the runner's tests
 * build their sandboxes' layout from this spec too (`runner/test/layout.ts`).
 *
 * The image holds no runner: the sandbox Worker ships it and names it by its digest. The
 * tag is the digest of everything the image's build reads, and of nothing else: `image.py`,
 * the hashed lock of the E2B SDK that runs it (`requirements.txt`), the spec, and every file
 * staged for it (the loader, the engines' locks), each by its path and mode. So a change to
 * the runner, the probes, the checks or this file's own code moves no tag, and any change to
 * the image's inputs does (`guards/template.test.ts`).
 *
 *   node <pair.ts tsTool("image.ts")> [--stage <dir> --spec <file>]
 *
 * prints `name:tag`; with `--stage`, it first stages the image's files in `<dir>` and writes
 * the spec to `<file>`, what `image.py build` reads.
 */
import { createHash } from "node:crypto";
import { chmodSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync }
  from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { RUNS_CEILING } from "../../contract/src/control";
import { bundledSources } from "../../sources/src/catalog";
import { binPath, ENGINE_ROOT, WORKSPACE } from "../runner/engine";
import { INBOX, INCOMING, LOADER, RUNNERS, STATE_DIR } from "../runner/protocol";
import { E2B_USER, POOL_PREFIX, SHARED_GROUP, SYNC_HOME, SYNC_USER } from "../runner/users";

const TEMPLATE = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(TEMPLATE, "..", "..");
const SOURCES = join(PLATFORM, "sources");

/** The template's name at E2B; each build of it is a tag. */
export const NAME = "tinyassets-sandbox";

/** One engine the image installs: its record's id, its binary, and its lifecycle scripts. */
export interface ImageEngine {
  readonly id: string;
  readonly bin: string;
  readonly scripts: boolean;
}

/** Everything the image is made of but its files (`stage`); `image.py` reads nothing else. */
export interface ImageSpec {
  readonly name: string;
  readonly cpu: number;
  readonly memory_mb: number;
  /** Node from nodejs.org, by its published SHA-256, unpacked over `prefix`. */
  readonly node: { readonly version: string; readonly sha256: string; readonly prefix: string };
  readonly loader: { readonly path: string; readonly mode: number };
  /** The run users' and the sync agent's home: none. */
  readonly no_home: string;
  /** E2B's own default user, taken out of `sudo`. */
  readonly e2b_user: string;
  readonly group: string;
  readonly pool: { readonly prefix: string; readonly size: number; readonly shell: string };
  readonly sync: { readonly user: string; readonly shell: string; readonly dir: string;
                   readonly mode: number };
  readonly state: { readonly dir: string; readonly mode: number; readonly inbox: string;
                    readonly incoming: string; readonly incoming_mode: number };
  /** What holds the runners and the engines, root's alone to write. */
  readonly opt: string;
  readonly runners: { readonly dir: string; readonly mode: number };
  readonly engines_dir: string;
  readonly workspace: { readonly dir: string; readonly mode: number };
  readonly engines: readonly ImageEngine[];
}

/**
 * The image's spec. Node 22.19 or later is what the engines' locks need, on E2B's own base
 * image, its provisioning already in it.
 */
export function spec(): ImageSpec {
  const opt = dirname(RUNNERS);
  if (dirname(ENGINE_ROOT) !== opt) {
    throw new Error(`the engines (${ENGINE_ROOT}) and the runners (${RUNNERS}) share a parent`);
  }
  return {
    name: NAME,
    cpu: 2,
    memory_mb: 1024,
    node: { version: "v22.22.0", prefix: "/usr/local",
            sha256: "c33c39ed9c80deddde77c960d00119918b9e352426fd604ba41638d6526a4744" },
    loader: { path: LOADER, mode: 0o700 },
    no_home: "/nonexistent",
    e2b_user: E2B_USER,
    group: SHARED_GROUP,
    pool: { prefix: POOL_PREFIX, size: RUNS_CEILING, shell: "/bin/bash" },
    sync: { user: SYNC_USER, shell: "/usr/sbin/nologin", dir: SYNC_HOME, mode: 0o700 },
    state: { dir: STATE_DIR, mode: 0o700, inbox: INBOX, incoming: INCOMING,
             incoming_mode: 0o700 },
    opt,
    runners: { dir: RUNNERS, mode: 0o755 },
    engines_dir: ENGINE_ROOT,
    workspace: { dir: WORKSPACE, mode: 0o2775 },
    engines: engines(),
  };
}

/**
 * Every served CLI record installed by npm, by id: its binary at the runner's own path for
 * it (`binPath`), its lock the record's (`lock_sha256`) or this throws.
 */
function engines(): ImageEngine[] {
  const sources = bundledSources();
  const out: ImageEngine[] = [];
  for (const id of [...sources.ids()].sort()) {
    const record = sources.get(id)!;
    const install = record.cli?.install;
    if (install === undefined || record.unwired !== undefined || install.manager !== "npm") {
      continue;
    }
    const digest = createHash("sha256").update(readFileSync(lock(id))).digest("hex");
    if (digest !== install.lock_sha256) {
      throw new Error(`${id}: its lock is not the record's (${digest})`);
    }
    if (binPath(record) !== `${ENGINE_ROOT}/${id}/node_modules/.bin/${install.bin}`) {
      throw new Error(`${id}: the runner runs it from ${binPath(record)}, not its npm bin`);
    }
    out.push({ id, bin: install.bin, scripts: install.lifecycle_scripts === true });
  }
  return out;
}

function lock(id: string): string {
  return join(SOURCES, "locks", id, "package-lock.json");
}

/** A file the image's build copies in: where it is staged (`rel`), from where, its mode. */
interface Staged {
  readonly rel: string;
  readonly from: string;
  readonly mode: number;
}

/**
 * The files the image's build copies from its staging directory: the loader, and each
 * engine's lock. A file's mode is git's (0755 if executable, else 0644), whatever umask the
 * checkout had, so the recipe E2B's SDK hashes is the same everywhere.
 */
function staged(image: ImageSpec): Staged[] {
  const files = [{ rel: "ta-runner", from: join(TEMPLATE, "ta-runner") },
                 ...image.engines.flatMap((engine) => ["package.json", "package-lock.json"]
                   .map((name) => ({ rel: `locks/${engine.id}/${name}`,
                                     from: join(SOURCES, "locks", engine.id, name) })))];
  return files.map((file) => ({ ...file,
                                mode: (statSync(file.from).mode & 0o111) === 0 ? 0o644 : 0o755 }));
}

/** JSON with every object's keys sorted: the spec as it is hashed. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** The image's version: `s` and the first 16 hex of the digest of its build's inputs. */
export function tag(image: ImageSpec = spec()): string {
  const digest = createHash("sha256");
  const part = (name: string, data: Uint8Array | string) => {
    digest.update(`${name}\0`);
    digest.update(createHash("sha256").update(data).digest());
  };
  part("image.py", readFileSync(join(TEMPLATE, "image.py")));
  // The SDK turns `image.py`'s steps into the build, so another SDK may make another image.
  part("requirements.txt", readFileSync(join(TEMPLATE, "requirements.txt")));
  part("spec", canonical(image));
  for (const file of staged(image)) {
    part(`${file.rel}\0${file.mode.toString(8)}`, readFileSync(file.from));
  }
  return `s${digest.digest("hex").slice(0, 16)}`;
}

/** The image's files staged in `dir`, each at its path and with its mode (`staged`). */
export function stage(dir: string, image: ImageSpec = spec()): void {
  for (const file of staged(image)) {
    const to = join(dir, file.rel);
    mkdirSync(dirname(to), { recursive: true });
    writeFileSync(to, readFileSync(file.from));
    chmodSync(to, file.mode);
  }
}

if (process.argv[1] !== undefined
    && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const image = spec();
  const at = (flag: string) => {
    const i = args.indexOf(flag);
    return i < 0 ? undefined : args[i + 1];
  };
  const dir = at("--stage");
  const file = at("--spec");
  if ((dir === undefined) !== (file === undefined) || args.length !== (dir ? 4 : 0)) {
    throw new Error("image.ts [--stage <dir> --spec <file>]");
  }
  if (dir !== undefined && file !== undefined) {
    stage(dir, image);
    writeFileSync(file, `${JSON.stringify(image, null, 2)}\n`);
  }
  process.stdout.write(`${NAME}:${tag(image)}\n`);
}
