/**
 * The sandbox's template and the pair it runs (`template/`):
 * - the image (`image.py`, from `image.ts`'s spec and staged files) is the recipe the pinned
 *   template was built from, as E2B's SDK serializes it: a step changed is a new image;
 * - its tag moves with every input of the image's build, and with nothing else (the runner,
 *   the probes, the checks, the tag's own code);
 * - the runner the Worker ships is the bundle as built, byte for byte, and carries none of
 *   the probes, which are a bundle of their own beside it; a change to the probes alone
 *   moves the pair's tag;
 * - the pinned template passed its standing checks, and a tree whose image passed is pinned
 *   to it. That the pin is this tree's image is the deploy kit's check (`deploy.ts`'s
 *   `template` step), where it is decidable: a build is made from `main` alone, so the pin
 *   follows its build in a PR of its own.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { chmodSync, cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { afterAll, describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { readWrangler } from "../../ci/workers";
import { STEP_NS } from "../../contract/src/sync-ignore";
import { NAME, spec, stage, tag } from "../template/image";
import { bundles, pairTag, tsTool } from "../template/pair";

const TEMPLATE = join(PLATFORM, "sandbox", "template");
/**
 * The image's recipe as E2B's SDK serializes it (`Template.to_json`, e2b 2.62.0): 21 steps,
 * the pinned template's (main's `build.py` gave the same, byte for byte, untangle A6 part b).
 * Its copies' hashes take each staged file's mode: git's, 0644 (`image.ts` stages so).
 */
const RECIPE = "e953e65c6191f91a73e625d0a403e80214d62998eca175c1f69215e51abe22a5";
/** The same recipe from a checkout whose files are 0664 (umask 002), as A6b's research read it. */
const RECIPE_664 = "8266c6a64ea93fb5103a8b604e23ad87d656cf2d9922ad06fa93ac03e397f78b";

/**
 * E2B's SDK as far as the image's steps reach it (`Template`, `Template.to_json`), written
 * out to the letter of e2b 2.62.0's (`template/main.py` `to_json` and `_serialize`,
 * `template/utils.py` `calculate_files_hash` for one regular file), so the recipe is checked
 * here without the SDK: no network, no build. It gave the SDK's own bytes for main's
 * `build.py` and this `image.py`, at both modes (A6 part b's report).
 */
const SDK = `
import hashlib, json, os, stat

class Template:
    def __init__(self, file_context_path=None):
        self._context = str(file_context_path)
        self._steps = []
        self._base = None

    def from_base_image(self):
        self._base = "e2bdev/base"
        return self

    def set_user(self, user):
        self._steps.append({"type": "USER", "args": [user], "force": False})
        return self

    def run_cmd(self, command, user=None):
        if not isinstance(command, str) or user is not None:
            raise ValueError("a step here is one command, as the template's user")
        self._steps.append({"type": "RUN", "args": [command], "force": False})
        return self

    def copy(self, src, dest, user=None, mode=None):
        path = os.path.join(self._context, src)
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{src}: a copy here is one regular file")
        digest = hashlib.sha256(f"COPY {src} {dest}".encode())
        digest.update(os.path.relpath(path, self._context).encode())
        digest.update(str(info.st_mode).encode())
        digest.update(str(info.st_size).encode())
        with open(path, "rb") as file:
            digest.update(file.read())
        self._steps.append({"type": "COPY",
                            "args": [src, str(dest), user or "",
                                     "" if mode is None else f"{mode:04o}"],
                            "force": False, "filesHash": digest.hexdigest()})
        return self

    @staticmethod
    def to_json(template):
        return json.dumps({"steps": template._steps, "force": False,
                           "fromImage": template._base}, indent=2)
`;

/** `image.py`'s template, serialized (argv: the SDK, the template dir, the stage, the spec). */
const SERIALIZE = `
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import image
from e2b import Template
sys.stdout.write(Template.to_json(image.template(Path(sys.argv[3]),
                                                 json.loads(Path(sys.argv[4]).read_text()))))
`;

const dirs: string[] = [];
function temp(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  dirs.push(dir);
  return dir;
}
afterAll(() => {
  for (const dir of dirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

const sha = (data: string | Uint8Array) => createHash("sha256").update(data).digest("hex");

/** The image's recipe from `stage`'s files (`mode`, if given, set on each first). */
function recipe(mode?: number): string {
  const sdk = temp("ta-sdk-");
  writeFileSync(join(sdk, "e2b.py"), SDK);
  const staged = temp("ta-stage-");
  const image = spec();
  stage(staged, image);
  if (mode !== undefined) {
    const files = (dir: string): string[] => readdirSync(dir).flatMap((name) =>
      statSync(join(dir, name)).isDirectory() ? files(join(dir, name)) : [join(dir, name)]);
    for (const file of files(staged)) {
      chmodSync(file, mode);
    }
  }
  const specFile = join(sdk, "spec.json");
  writeFileSync(specFile, JSON.stringify(image));
  return execFileSync("python3", ["-I", "-c", SERIALIZE, sdk, TEMPLATE, staged, specFile],
                      { encoding: "utf8" });
}

function pinned(): string {
  return (readWrangler(join(PLATFORM, "sandbox")) as { vars: { E2B_TEMPLATE: string } })
    .vars.E2B_TEMPLATE;
}

let tree: string | null = null;

/**
 * A copy of what the image, the runner and the probes are made from, to change (each change
 * put back after its test): the contract, the records and their locks, the runner and the
 * template. One for the file.
 */
function copied(): string {
  if (tree === null) {
    tree = temp("ta-tree-");
    for (const part of ["contract/src", "sources", "sandbox/runner", "sandbox/template"]) {
      cpSync(join(PLATFORM, part), join(tree, part), { recursive: true });
    }
  }
  return tree;
}

/** The image's tag as the copy's own `image.ts` computes it, from the copy alone. */
function tagOf(copy: string): string {
  const argv = tsTool("image.ts").map((arg) => arg.replace(PLATFORM, copy));
  return execFileSync(process.execPath, argv, { encoding: "utf8" }).trim();
}

/** `file` in `copy` changed by `change` while `act` runs (and settles), then put back. */
async function changed<T>(copy: string, file: string, change: (text: string) => string,
                          act: () => T | Promise<T>): Promise<T> {
  const path = join(copy, file);
  const was = readFileSync(path, "utf8");
  const now = change(was);
  expect(now, `${file} is changed`).not.toBe(was);
  writeFileSync(path, now);
  try {
    return await act();
  } finally {
    writeFileSync(path, was);
  }
}

/** One bundle as `bundle.mjs` builds it: its bytes, digest and source files. */
interface Built {
  readonly bytes: Buffer;
  readonly sha: string;
  readonly inputs: readonly string[];
}

/** `bundle.mjs`'s own build, both bundles of the platform tree at `root` (this one). */
async function bundler(): Promise<{
  build: (root?: string) => Promise<{ runner: Built; probes: Built }> }> {
  return await import(pathToFileURL(join(TEMPLATE, "bundle.mjs")).href) as {
    build: (root?: string) => Promise<{ runner: Built; probes: Built }> };
}

interface Passed {
  readonly tag: string;
  readonly runner?: string;
  readonly probes?: string;
  readonly logins: readonly { engine: string; region: string | null; ok: boolean }[];
  readonly routes: readonly { engine: string; provider: string; ok: boolean;
                              turns: readonly { exit: number; answered: boolean;
                                                resumed: boolean }[] | null;
                              compacted: boolean | null }[];
  readonly matcher?: { readonly step_ns: number; readonly ceiling_ns: number;
                       readonly ok: boolean };
}

function passing(entry: Passed): boolean {
  return entry.logins.length > 0 && entry.logins.every((each) => each.ok)
    && entry.routes.length > 0 && entry.routes.every((each) => each.ok
      && each.turns?.length === 2 && each.turns.every((one) => one.exit === 0 && one.answered)
      && each.turns[1]!.resumed)
    && entry.matcher !== undefined && entry.matcher.ok && entry.matcher.step_ns <= STEP_NS;
}

describe("the image", () => {
  it("is the recipe the pinned template was built from, as E2B's SDK serializes it", () => {
    const steps = JSON.parse(recipe()) as { steps: { type: string }[]; fromImage: string };
    expect(steps.fromImage).toBe("e2bdev/base");
    expect(steps.steps).toHaveLength(21);
    expect(steps.steps.filter((step) => step.type === "COPY")).toHaveLength(11);
    expect(sha(recipe())).toBe(RECIPE);
    // Staged as an 0664 checkout's files are: the recipe A6b's research read from main.
    expect(sha(recipe(0o664))).toBe(RECIPE_664);
  });

  it("is versioned by its build's inputs: image.py, the spec and the staged files", async () => {
    const copy = copied();
    const image = `${NAME}:${tag()}`;
    expect(tagOf(copy)).toBe(image);
    expect(tag()).toBe(tag(spec()));  // the same every time
    // Each input of the image's build moves it.
    const moves: [string, (text: string) => string][] = [
      ["sandbox/template/image.py", (text) => `${text}# a comment is a new image\n`],
      ["sandbox/template/ta-runner", (text) => `${text}# a new loader\n`],
      ["sources/locks/codex/package.json", (text) => `${text}\n`],
      ["sandbox/runner/users.ts", (text) => text.replace('"ta-run-"', '"ta-job-"')],
      ["contract/src/control.ts", (text) => text.replace("RUNS_CEILING = 1000",
                                                        "RUNS_CEILING = 999")],
      ["sandbox/template/image.ts", (text) => text.replace('version: "v22.22.0"',
                                                          'version: "v22.22.1"')],
      ["sandbox/runner/engine.ts", (text) => text.replace('ENGINE_ROOT = "/opt/ta/engines"',
                                                          'ENGINE_ROOT = "/opt/ta/cli"')],
    ];
    for (const [file, change] of moves) {
      expect(await changed(copy, file, change, () => tagOf(copy)), file).not.toBe(image);
    }
  });

  it("is versioned by nothing else: the runner, the probes, the checks, the tag's own code",
     async () => {
    const copy = copied();
    const image = `${NAME}:${tag()}`;
    const stays: [string, (text: string) => string][] = [
      ["sandbox/runner/runner.ts", (text) => `${text}export const CHANGED = 1;\n`],
      ["sandbox/runner/probes.ts", (text) => `${text}export const CHANGED = 1;\n`],
      ["sandbox/template/checks.ts", (text) => `${text}export const CHANGED = 1;\n`],
      ["sandbox/template/pair.ts", (text) => `${text}export const CHANGED = 1;\n`],
      ["sandbox/template/bundle.mjs", (text) => `${text}// changed\n`],
      ["sandbox/template/image.ts", (text) => `${text}// a comment is no new image\n`],
    ];
    for (const [file, change] of stays) {
      expect(await changed(copy, file, change, () => tagOf(copy)), file).toBe(image);
    }
  });
});

describe("the pair", () => {
  it("ships the runner as built, the same bytes every build, carrying none of the probes",
     async () => {
    const { runner, probes } = bundles();
    expect(bundles().runner.bytes.equals(runner.bytes)).toBe(true);
    // What `src/runner-bytes.ts` imports is this build's own output.
    expect(readFileSync(join(PLATFORM, "sandbox", "dist", "runner.mjs")).equals(runner.bytes))
      .toBe(true);
    // The runner's import graph: no probe, and none of the code the probes judge by.
    const { build } = await bundler();
    const built = await build();
    expect(built.runner.sha).toBe(runner.sha);
    expect(built.probes.sha).toBe(probes.sha);
    expect(built.runner.inputs).not.toContain("sandbox/runner/probes.ts");
    expect(built.runner.inputs).not.toContain("sandbox/runner/route.ts");
    const text = built.runner.bytes.toString("utf8");
    for (const name of ["sandboxEnv", "egressBaseUrl", "runEnv", "stepNanos", "bundledRecords",
                        "routeVerdict"]) {
      expect(text, name).not.toMatch(new RegExp(`function ${name}\\d*\\(`));
    }
    // The probes carry the code they judge by, and never the runner's own entry.
    expect(built.probes.inputs).toEqual(expect.arrayContaining(["sandbox/runner/probes.ts",
                                                                "sandbox/runner/route.ts"]));
    expect(built.probes.inputs).not.toContain("sandbox/runner/runner.ts");
    expect(built.probes.bytes.toString("utf8")).toContain(JSON.stringify(runner.sha));
  });

  /** The copy's bundles as they stand, built once. */
  let built: { runner: Built; probes: Built } | null = null;
  const before = async () => {
    built ??= await (await bundler()).build(copied());
    return built;
  };
  const named = (bundled: { runner: Built; probes: Built }) =>
    pairTag(`${NAME}:${tag()}`, bundled.runner.sha, bundled.probes.sha);

  it("is named by the image and both bundles: a probes change alone moves its tag", async () => {
    const was = await before();
    expect(named(was)).toBe(`${NAME}:${tag()}-r${was.runner.sha.slice(0, 16)}-p${
      was.probes.sha.slice(0, 16)}`);
    expect(() => pairTag(`${NAME}:${tag()}`, was.runner.sha, "probes")).toThrow(/SHA-256/);
    const { build } = await bundler();
    const probed = await changed(copied(), "sandbox/runner/probes.ts",
                                 (text) => `${text}export const CHANGED = 1;\n`,
                                 () => build(copied()));
    expect(probed.runner.sha).toBe(was.runner.sha);
    expect(probed.probes.sha).not.toBe(was.probes.sha);
    expect(named(probed)).not.toBe(named(was));
  });

  it("is named anew by a runner change (which moves no image, above)", async () => {
    const was = await before();
    const { build } = await bundler();
    const ran = await changed(copied(), "sandbox/runner/runner.ts",
                              (text) => `${text}export const CHANGED = 1;\n`,
                              () => build(copied()));
    expect(ran.runner.sha).not.toBe(was.runner.sha);
    expect(named(ran)).not.toBe(named(was));
  });

  it("is pinned only once it passed its standing checks, and a passed tree is pinned", () => {
    const passed = JSON.parse(readFileSync(join(TEMPLATE, "passed.json"), "utf8")) as Passed[];
    const entry = passed.findLast((each) => `${NAME}:${each.tag}` === pinned());
    expect(entry, "the pinned tag has no passing record in template/passed.json").toBeDefined();
    expect(passing(entry!)).toBe(true);
    // This tree's image, once a pair of it passed, is the pin: its pin PR is not forgotten.
    const tree = tag();
    if (passed.some((each) => each.tag === tree && passing(each))) {
      expect(pinned()).toBe(`${NAME}:${tree}`);
    }
    // A pair recorded with its probes names each bundle by its SHA-256.
    for (const each of passed.filter((one) => one.probes !== undefined)) {
      expect([each.runner, each.probes].every((one) => /^[0-9a-f]{64}$/.test(one ?? "")))
        .toBe(true);
    }
  });
});
