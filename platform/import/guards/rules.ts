/**
 * The importer's allow-list, checked by the platform's guard kit (`ci/kit/`).
 *
 * The importer is a job, not a Worker (untangle A1): it reads the platform's vocabulary
 * from the contract alone, so its expected content ids, path rules and limits are the
 * platform's own, and no Worker's `src` (`decisions.md` A1: no Worker imports another
 * Worker's `src`; neither does this). It reads the starter's package to count what the
 * starter seeds. It is Node's: the file system, SQLite and the mount tools, and, in its
 * staging step alone (`stage.ts`, slice 4), the network: the staging bucket over R2's S3 API
 * and the import queue over the Queues HTTP API, with the credentials the run is handed.
 */
import type { ImportRules } from "../../ci/kit/imports";

/** The contract's modules the importer reaches, its own and through each other. */
const CONTRACT = Object.freeze(["content", "digest", "file-defaults", "ids", "import-message",
                                "limits", "refusal", "paths", "sync-ignore", "text"]
  .map((name) => `contract/src/${name}`));

export const IMPORTS: ImportRules = Object.freeze({
  worker: "import",
  job: "import",
  called: "the importer",
  outside: Object.freeze([...CONTRACT, "starter/starter.json"]),
  bare: Object.freeze({
    "node:crypto": null, "node:fs": null, "node:os": null, "node:path": null, "node:sqlite": null,
    "node:child_process": "import/cli.ts",
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
