/**
 * The importer's allow-list, checked by the platform's guard kit (`ci/kit/`).
 *
 * The importer is a job, not a Worker (untangle A1): it reads the platform's vocabulary
 * from the contract alone, so its expected content ids, path rules and limits are the
 * platform's own, and no Worker's `src` (`decisions.md` A1: no Worker imports another
 * Worker's `src`; neither does this). It reads the starter's package to count what the
 * starter seeds. It runs each old policy through the platform's own validator
 * (`contract/src/policy-document.ts`). It is Node's: the file system and SQLite, and no
 * child process (the host script, `window.sh`, makes the copy and mounts its images), and, in
 * its staging and window steps alone (`stage.ts`, slice 4; `window.ts`, slice 5), the network: the staging and files buckets over R2's S3 API, the import queue
 * and its dead-letter queue over the Queues HTTP API, and the directory over the KV API,
 * with the credentials the run is handed.
 */
import type { ImportRules } from "../../ci/kit/imports";

/** The contract's modules the importer reaches, its own and through each other. */
const CONTRACT = Object.freeze(["capabilities", "content", "digest", "doors", "file-defaults",
                                "ids", "import-message", "limits", "policy-document",
                                "refusal", "paths", "sync-ignore", "text"]
  .map((name) => `contract/src/${name}`));

export const IMPORTS: ImportRules = Object.freeze({
  worker: "import",
  job: "import",
  called: "the importer",
  // The door table's tools and profiles, which the policy's `tools` table is held to.
  outside: Object.freeze([...CONTRACT, "contract/doors.json", "contract/profiles.json",
                          "starter/starter.json"]),
  bare: Object.freeze({
    "node:crypto": null, "node:fs": null, "node:os": null, "node:path": null, "node:sqlite": null,
  }),
  banned: Object.freeze([{
    pattern: /^fetch$/,
    home: "import/stage.ts",
    says: "names %s: the importer reaches the network in its staging step alone (stage.ts)",
  }, {
    pattern: /^(?:WebSocket|getByName|R2Bucket|DurableObject|WorkerEntrypoint)$/,
    says: "names %s: the importer reaches no Worker",
  }]),
});
