import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { keyring } from "./fixtures";
import { control, gate, methodsOf } from "./gate";
import { PLATFORM } from "./paths";
import { project } from "./project";
import { auxiliary, once, workerConfig } from "./workers";

const OWNER = join(PLATFORM, "owner");
const FILES = join(PLATFORM, "files");
/** The owner object's doors, the methods the owner gate has (`contract/doors.json`). */
const DOORS = (JSON.parse(readFileSync(join(PLATFORM, "contract", "doors.json"), "utf8")) as
  { doors: { door: string }[] }).doors.map((row) => row.door);

export default defineConfig({
  test: {
    projects: [
      // The repository's own gates that outlive the Python suite: the platform's CI legs,
      // the test-timeout ban, the production-environment rule and the guard kit's own
      // invariants. Static, in Node.
      project("ci", "guards"),
      // The Worker kit's own Workers, in workerd: the owner and files Workers bundled from
      // their real entries (`auxiliary`), with no test class of theirs, and a gate in front
      // of each (`gate.ts`). OWNER is the owner gate, bound by `scriptName` as the doors'
      // config binds the owner, and REAL_OWNER the owner Worker itself; FILES is the files
      // gate, which the owner's own FILES binding reaches too, and REAL_FILES the files
      // Worker itself. The gates' `Control` entrypoints are the test Worker's alone. The
      // owner's other services are left unbound: these tests reach none of them.
      project("ci", "workerd", {
        plugins: [
          cloudflareTest(once(() => ({
            miniflare: {
              compatibilityDate: workerConfig(OWNER).compatibility_date,
              compatibilityFlags: ["nodejs_compat"],
              durableObjects: {
                OWNER: { className: "OwnerDO", scriptName: "owner-gate", useSQLite: true },
                REAL_OWNER: { className: "OwnerDO", scriptName: workerConfig(OWNER).name,
                              useSQLite: true },
              },
              serviceBindings: {
                FILES: "files-gate", REAL_FILES: workerConfig(FILES).name,
                FILES_GATE: control("files-gate"), OWNER_GATE: control("owner-gate"),
              },
              workers: [
                auxiliary(OWNER, {
                  bindings: { ROUTING_MAC_KEY: randomBytes(32).toString("base64url") },
                  kvNamespaces: ["CONTROL"],
                  serviceBindings: { FILES: "files-gate" },
                }),
                auxiliary(FILES, { bindings: { FILES_KEYRING: keyring() } }),
                gate("files-gate", methodsOf(join(PLATFORM, "contract", "src", "files.ts"),
                                             "ChunkStore"), { worker: FILES }),
                gate("owner-gate", DOORS, { worker: OWNER, className: "OwnerDO" }),
              ],
            },
          }))),
        ],
      }),
    ],
  },
});
