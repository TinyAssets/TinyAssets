/**
 * sandbox-template.yml spends money, so its shape is held: main only, in `production`
 * (whose E2B_API_KEY no other ref reads), one build at a time and none twice, every
 * package it installs hashed, and the user token kept out of the push's URL and away
 * from any repository hook.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM, workflow, WORKFLOWS } from "../workflows";

describe("sandbox-template.yml", () => {
  const wf = workflow("sandbox-template.yml");
  const text = readFileSync(join(WORKFLOWS, "sandbox-template.yml"), "utf8");
  const job = wf.jobs.build;

  it("builds on main alone, in production, one at a time", () => {
    expect(job.environment).toBe("production");
    expect(job.if).toBe("github.ref == 'refs/heads/main'");
    expect(wf.concurrency).toEqual({ group: "sandbox-template", "cancel-in-progress": false });
    expect(text).toContain("branches/sandbox-template/${name#*:}");
  });

  it("installs only hashed packages", () => {
    expect(text).toContain("--require-hashes --no-deps");
    expect(text).not.toMatch(/pip"? install [^\n]*e2b==/);
    const lock = readFileSync(join(PLATFORM, "sandbox", "template", "requirements.txt"), "utf8");
    const pins = lock.split("\n").filter((line) => /^[a-z0-9_.-]+==/i.test(line));
    expect(pins.some((line) => line.startsWith("e2b==2.62.0"))).toBe(true);
    expect(pins.every((line) => line.endsWith("\\"))).toBe(true);
  });

  it("keeps the token out of the push URL and runs no hook on it", () => {
    expect(text).not.toMatch(/x-access-token|https:\/\/[^\s"]*\$\{?GH_TOKEN/);
    expect(text).toContain("gh auth setup-git");
    expect(text).toContain("git -c core.hooksPath=/dev/null push");
  });
});
