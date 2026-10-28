/**
 * pi's packages and pi's schema version are pinned (ADR-032; tasks 5).
 *
 * pi's tables sit in the owner's one database beside ours, with their own forward-only
 * schema version (`durable_schema`), which pi migrates when it opens and refuses to run
 * under when it is newer than the code ("a deployment cannot roll back past this
 * release", pi-durable 1.4.0's changelog). So a pi upgrade that moves the version is a
 * one-way deploy: it must deploy alone, behind a canary, and never be rolled back past.
 * This guard makes any such upgrade fail CI until it is made here, on purpose: pi-durable,
 * pi-ai and chord are pinned by exact version and lockfile integrity (agents 0.31.0 takes
 * them as `~1.4.0` peers, and the alarm guard pins agents), and pi's schema version by the
 * number the code migrates to.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { CURRENT_SQLITE_SCHEMA_VERSION } from "@earendil-works/pi-durable/storage/sqlite";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "./sources";

/** The schema version pi 1.4.0 migrates its tables to. */
const PI_SCHEMA_VERSION = 2;
/** The pi packages, as the lockfile resolves them. */
const PI = Object.freeze({
  "@earendil-works/pi-durable": "sha512-EtIyXH1MBSXdJSkodmy+CYXuwfve3gPrD4GNCZ0XmuRWLPupwRlNQ7fd" +
    "aXjRcLoKb6SWkT/yEzK+sMmrqvYK+Q==",
  "@earendil-works/pi-ai": "sha512-hQM/C4+1w8rHYACzDDf+0vQMnEnpzIop1tptIERVgR3ZeT/hHSzBNG8PaWPgx" +
    "WQjqSeKCyBZSyp8uTT6eKK1pQ==",
  "@earendil-works/chord": "sha512-dTbgyz4xgDS5KAUFx1bFENqj0Cr1N3tYxJJOYStc6g6HS78FSBZydyoCwLodd" +
    "iQRF+VgRS0fpgAi+iKxjZ3BQA==",
});
const VERSION = "1.4.0";

type Lock = { packages: Record<string, { version?: string; integrity?: string;
                                         devDependencies?: Record<string, string>;
                                         peerDependencies?: Record<string, string> }> };

describe("pi pin guard", () => {
  const lock = JSON.parse(readFileSync(join(PLATFORM, "package-lock.json"), "utf8")) as Lock;
  const owner = JSON.parse(readFileSync(join(PLATFORM, "owner", "package.json"), "utf8")) as
    { devDependencies: Record<string, string> };

  it("pins pi's schema version: a move is a one-way deploy, made here on purpose", () => {
    expect(CURRENT_SQLITE_SCHEMA_VERSION).toBe(PI_SCHEMA_VERSION);
  });

  it.each(Object.entries(PI))("pins %s by exact version and lockfile integrity",
                              (name, integrity) => {
    const resolved = lock.packages[`node_modules/${name}`]!;
    expect([resolved.version, resolved.integrity]).toEqual([VERSION, integrity]);
    expect(owner.devDependencies[name]).toBe(VERSION);
    expect(lock.packages["owner"]!.devDependencies![name]).toBe(VERSION);
    // The one minor agents supports (its `~1.4.0` peer range) holds the pinned version.
    expect(lock.packages["node_modules/agents"]!.peerDependencies![name]).toBe("~1.4.0");
  });
});
