/**
 * The memory legs' own workerd (`memory/workerd.mjs`): a leg measures only its own child, and
 * leaves none behind. Driven through real processes, with a stand-in child (`node -e`) in
 * workerd's place: a child that exits early fails the leg at once, before it reads another
 * figure; a leg ended by SIGTERM, SIGINT or its own exit leaves no child running.
 */
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { PLATFORM } from "./sources";

const OWN = pathToFileURL(join(PLATFORM, "owner", "memory", "workerd.mjs")).href;
const dirs: string[] = [];
/** Every process a test starts, killed after it whatever the test's outcome. */
const started: number[] = [];

/** A leg: it owns a stand-in child running `child`, then runs `rest`. */
function leg(child: string, rest: string): string {
  const dir = mkdtempSync(join(tmpdir(), "memory-leg-"));
  dirs.push(dir);
  const path = join(dir, "leg.mjs");
  writeFileSync(path, `import { ownWorkerd } from ${JSON.stringify(OWN)};
const own = ownWorkerd(process.execPath, ["-e", ${JSON.stringify(child)}]);
console.log(own.child.pid);
${rest}
`);
  return path;
}

/** Each `const name = (async) (...) => {` helper in `text`, with its braced body. */
function helpersOf(text: string): { name: string; start: number; end: number; body: string }[] {
  const found = [];
  for (const hit of text.matchAll(/const (\w+) = (?:async )?\([^)]*\) => \{/g)) {
    const open = hit.index! + hit[0].length - 1;
    let depth = 0;
    let at = open;
    for (; at < text.length; at++) {
      if (text[at] === "{") {
        depth++;
      } else if (text[at] === "}" && --depth === 0) {
        break;
      }
    }
    found.push({ name: hit[1]!, start: open, end: at, body: text.slice(open, at) });
  }
  return found;
}

/** Whether `pid` is gone: no such process, or one that only waits to be reaped. */
function gone(pid: number): boolean {
  try {
    process.kill(pid, 0);
  } catch {
    return true;
  }
  try {
    return readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1]?.startsWith("Z") ?? false;
  } catch {
    return true;
  }
}

async function goneSoon(pid: number): Promise<boolean> {
  for (let n = 0; n < 40 && !gone(pid); n++) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return gone(pid);
}

/** Runs a leg until it prints its child's pid, then sends it `signal`. */
async function signalled(signal: NodeJS.Signals | null) {
  const forever = "setInterval(() => {}, 1000)";
  const run = spawn(process.execPath, [leg(forever, signal === null
    ? "setTimeout(() => {}, 200);" : `${forever};`)], { stdio: ["ignore", "pipe", "pipe"] });
  started.push(run.pid!);
  const pid = await new Promise<number>((resolve) => {
    run.stdout.once("data", (data: Buffer) => resolve(Number(String(data).trim())));
  });
  started.push(pid);
  expect(gone(pid)).toBe(false);
  const exited = new Promise<number | null>((resolve) => run.once("exit", resolve));
  if (signal !== null) {
    run.kill(signal);
  }
  return { status: await exited, pid };
}

afterEach(() => {
  for (const pid of started.splice(0)) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // Already gone, as it should be.
    }
  }
  for (const dir of dirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("a memory leg's own workerd", () => {
  it("fails the leg as soon as its child exits early, before another figure is read", () => {
    const done = spawnSync(process.execPath, [leg("process.exit(3)", `
await new Promise((resolve) => setTimeout(resolve, 1500));
console.log("still measuring");
own.check();
console.log("measured");`)], { encoding: "utf8", timeout: 4000 });
    expect(done.status).toBe(1);
    expect(done.stderr).toContain("exited before the leg was done (code 3");
    // At once: the leg never reached its next step.
    expect(done.stdout).not.toContain("still measuring");
  });

  it("says a child that is not running is not the leg's own", async () => {
    const done = spawnSync(process.execPath, [leg("setInterval(() => {}, 1000)", `
own.stop();
await new Promise((resolve) => setTimeout(resolve, 200));
try { own.check(); console.log("measured"); } catch (error) { console.log(error.message); }`)],
                           { encoding: "utf8", timeout: 4000 });
    const pid = Number(done.stdout.split("\n")[0]);
    started.push(pid);
    expect(done.status).toBe(0);
    expect(done.stdout).toContain("is not running: what answered is not this leg's own");
    expect(done.stdout).not.toContain("measured");
    expect(await goneSoon(pid)).toBe(true);
  });

  it.each([["SIGTERM", 143], ["SIGINT", 130], ["SIGHUP", 129]] as const)(
    "leaves no child behind when the leg gets %s", async (signal, status) => {
      const ended = await signalled(signal);
      expect(ended.status).toBe(status);
      expect(await goneSoon(ended.pid)).toBe(true);
    });

  it("leaves no child behind when the leg ends without stopping it", async () => {
    const ended = await signalled(null);
    expect(ended.status).toBe(0);
    expect(await goneSoon(ended.pid)).toBe(true);
  });

  it("has every request helper of each leg confirm its own workerd after it answers", () => {
    // A helper is a `const name = (async) (...) => {...}` whose body requests a leg port
    // (`fetch(`); each must call `own.check()`. The inspector's polling loop is not one: it
    // runs before the leg has a target, and the check follows it.
    const legs = ["leg.mjs", "egress.mjs", "baselines.mjs"];
    for (const name of legs) {
      const text = readFileSync(join(PLATFORM, "owner", "memory", name), "utf8");
      const helpers = helpersOf(text);
      const requesting = helpers.filter((helper) => helper.body.includes("fetch("));
      expect([name, requesting.length]).not.toEqual([name, 0]);
      for (const helper of requesting) {
        expect([name, helper.name, helper.body.includes("own.check()")])
          .toEqual([name, helper.name, true]);
      }
      // Every other request is the inspector's poll, or inside a helper (`fetch()` with no
      // argument is a Worker's own handler, in the source a leg serves, not a request).
      const outside = [...text.matchAll(/fetch\((?!\))/g)].map((hit) => hit.index!)
        .filter((at) => !helpers.some((helper) => at > helper.start && at < helper.end))
        .map((at) => text.slice(at, text.indexOf("\n", at)));
      expect([name, outside.filter((line) => !line.includes("/json/list"))])
        .toEqual([name, []]);
      expect(text).toContain("own.check();");
    }
  });

  it("leaves no child behind when the leg crashes", async () => {
    const run = spawn(process.execPath, [leg("setInterval(() => {}, 1000)", `
await new Promise((resolve) => setTimeout(resolve, 200));
throw new Error("the leg broke");`)], { stdio: ["ignore", "pipe", "pipe"] });
    started.push(run.pid!);
    const pid = await new Promise<number>((resolve) => {
      run.stdout.once("data", (data: Buffer) => resolve(Number(String(data).trim())));
    });
    started.push(pid);
    const status = await new Promise<number | null>((resolve) => run.once("exit", resolve));
    expect(status).toBe(1);
    expect(await goneSoon(pid)).toBe(true);
  });
});
