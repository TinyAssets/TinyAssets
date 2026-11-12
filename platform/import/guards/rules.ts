/**
 * The importer's banned names, checked by the platform's guard kit (`ci/kit/`) over its
 * TypeScript: the job's own scripts and the contract modules they reach.
 *
 * The importer is a job, not a Worker (untangle A1): it reads the platform's vocabulary
 * from the contract, so its expected content ids, path rules and limits are the platform's
 * own, and no Worker's `src`; what it may import is the layer table's (`ci/kit/layers.ts`).
 * It runs each old policy through the platform's own validator
 * (`contract/src/policy-document.ts`). Its TypeScript is Node's file system and SQLite: it
 * starts no child process and opens no socket of its own, and its one network is `fetch`,
 * named in `stage.ts` alone (`NETWORK`), which its staging and window steps are handed: the
 * staging and files buckets over R2's S3 API, the import queue and its dead-letter queue over
 * the Queues HTTP API, and the directory over the KV API, with the credentials the run is
 * handed.
 *
 * Outside this guard, as it is outside the TypeScript: the host script (`host.py`) and the
 * window's shell (`window.sh`), which make the copy, mount its images and run the job.
 */
import type { ImportRules } from "../../ci/kit/imports";

export const IMPORTS: ImportRules = Object.freeze({
  worker: "import",
  job: "import",
  called: "the importer",
  banned: Object.freeze([{
    pattern: /^fetch$/,
    home: "import/stage.ts",
    says: "names %s: the importer's network is stage.ts's alone",
  }, {
    pattern: /^(?:WebSocket|getByName|R2Bucket|DurableObject|WorkerEntrypoint)$/,
    says: "names %s: the importer reaches no Worker",
  }, {
    pattern: /^(?:node:)?child_process$|^node:(?:http|https|http2|net|tls|dgram)$/,
    says: "names %s: the importer starts no process and opens no socket of its own",
  }]),
});
