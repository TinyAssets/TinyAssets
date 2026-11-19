/** A failed child can emit exactly the same answers as a passing recorder. */
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";

import { expect, it } from "vitest";

import { PLATFORM } from "../paths";

it("refuses unchanged transcripts from a failed rerun, and accepts a successful retry", () => {
  const dir = mkdtempSync(join(tmpdir(), "record-proof-"));
  try {
    writeFileSync(join(dir, "npx"), `#!/bin/sh
for file do :; done
case "$file" in
  */wire-reach.record.ts) prefix=REC ;;
  */tools.record.ts) prefix=TOOLS ;;
  */rpc.record.ts) prefix=RPC ;;
  */rv.record.ts) prefix=RV ;;
  *) exit 2 ;;
esac
printf '%s\\tanswer\\n' "$prefix"
if [ "$prefix" = RPC ] && [ "$FAIL_RPC" = yes ]; then exit 9; fi
`, { mode: 0o755 });
    const output = join(dir, "capture");
    function run(args: string[], fail = false) {
      return spawnSync(process.execPath, [join(PLATFORM, "ci/record.ts"), ...args], {
        env: { ...process.env, PATH: dir + delimiter + process.env["PATH"],
               FAIL_RPC: fail ? "yes" : "no" }, encoding: "utf8",
      });
    }
    expect(run(["record", output]).status).toBe(0);
    expect(run(["record", output], true).status).toBe(1);
    expect(readFileSync(join(output, "rpc.txt"), "utf8")).toBe("RPC\tanswer\n");
    expect(run(["diff", output, output]).status).toBe(1);
    expect(run(["record", output]).status).toBe(0);
    expect(run(["diff", output, output]).status).toBe(0);
    writeFileSync(join(output, "rpc.txt"), "RPC\tshortened\n");
    expect(run(["diff", output, output]).status).toBe(1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
