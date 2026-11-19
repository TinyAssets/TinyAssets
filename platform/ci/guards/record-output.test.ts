/** A review needs the result for every recorder, including those after a large difference. */
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";

import { expect, it } from "vitest";

import { PLATFORM } from "../paths";

it("delivers the whole recorder comparison when its output is piped", () => {
  const dir = mkdtempSync(join(tmpdir(), "record-diff-"));
  try {
    const transcript = join(dir, "answers");
    writeFileSync(join(dir, "npx"), '#!/bin/sh\ncat "$TRANSCRIPT"\n', { mode: 0o755 });
    function run(...args: string[]) {
      return spawnSync(process.execPath, [join(PLATFORM, "ci/record.ts"), ...args], {
        env: { ...process.env, PATH: dir + delimiter + process.env["PATH"], TRANSCRIPT: transcript },
        encoding: "utf8", maxBuffer: 4 * 1024 * 1024,
      });
    }
    const base = join(dir, "base");
    const head = join(dir, "head");
    const answers = (tools: string) => `REC\twire\nTOOLS\t${tools}\nRPC\trpc\nRV\trv\n`;
    writeFileSync(transcript, answers("before"));
    expect(run("record", base).status).toBe(0);
    // A unified tools/list difference can exceed the operating system's pipe buffer.
    const after = "after".repeat(200_000);
    writeFileSync(transcript, answers(after));
    expect(run("record", head).status).toBe(0);
    const diff = run("diff", base, head);
    expect(diff.status).toBe(1);
    expect(diff.stdout.includes(`+TOOLS\t${after}\n`)).toBe(true);
    expect(diff.stdout.endsWith("rpc: the same\nrv: the same\n")).toBe(true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
