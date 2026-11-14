# I1b as built (2026-11-14)

I1b is lane I's half of B11: the importer adopts `deploy/cloudflare.ts` for the rest of its Cloudflare calls. I1a did B10's half (#5547), and X2 (#5558) built the client and moved the importer's three reads onto it (d3, "X2 as built", 6).

1. **The importer's writes are the client's methods.** `send`, `consumers`, `addPullConsumer`, `pull`, `ack` and `deleteKeys` sit in `deploy/cloudflare.ts`, beside the reads. The client holds the vendor's side of each call:
   - the paths;
   - the envelope check;
   - the pull's field names, `message_backlog_count` and `lease_id`;
   - acks sent in calls of 100, and KV keys deleted in calls of 1000.

   The importer holds its own choices: a batch of 100, a 60-second lease and the pull consumer's settings. `window.ts`'s `api()` goes, and so does the push-and-check that `stage.ts` and `window.ts` each did by hand (`I-H18`, and the push half of `I-H21`).
2. **One Cloudflare API base.** `stage.ts`'s `API` and `pushMessage` go, so `CF_API` in the client is the only base. R2's S3 endpoint and SigV4 stay in `stage.ts`, as B11 says: the kit never speaks S3.
3. **The import queue's name is `QUEUES.IMPORTS`,** from the generated `contract/src/queue-names.ts`. That leaves the job's `facts.json` `queue` key with no reader, so it goes from `IMPORT_NAME_KEYS` and from `facts.ts`'s `IMPORT_NAMES` (one line in lane X's file). The dead-letter queue stays a fact, because `QUEUES` names only the queues a Worker produces to.
4. **The account id (`I-H22`, the account half) stays as `AGENTS.md` says.** The deploy kit resolves it from its token's zone, and the import window reads the repository variable `CLOUDFLARE_ACCOUNT_ID`. Each of the two has one source.
   - The job does not resolve the account from the zone. Mode `offsite` hands the job no API token at all. The window reads no token of the kit's, and the window's own API token would have to be granted zone read, which changes a live token's grant.
   - Inside the job, the account's grammar has one home: `ACCOUNT_ID` in the client, which `zone()` uses too. The job checks it once, where its credentials are parsed (`cli.ts`'s `stageInputs`). So `dlq`, which never checked it, now refuses a bad account as well. `stage.ts`'s `ACCOUNT` and the two copies of the check in `stage()` and `window.ts`'s `checkRun` go.
   - `import-window.yml`'s check of the variable stays. It refuses at dispatch, before any host is made, and a workflow imports nothing of the platform's (d2 §F 12).
5. **A run's id has one check, `stage.ts`'s `checkRun`,** which `stage`, `verify`, `admit` and `wipe` all call. Before, `stage()` and `window.ts` each had their own copy.
6. **`IMPORT_QUEUE` in `contract/src/import-message.ts` is D4's to delete (#5562).** The job never read it. Its only readers are the doors' (`doors/src/index.ts` and `doors/guards/config.test.ts`), and D4 moves them onto `QUEUES.IMPORTS` and deletes it in the same change. I1b neither reads it nor re-adds it.
7. **`window.ts`'s `pulledBody` stays with the importer.** A body that doesn't decode counts as "out of shape" in the importer's tally. If the client decoded it, the client would have to either swallow the error or fail the whole pull.
8. **A pull whose answer holds no pull throws** (the review's fix). `pull` refuses a result that is not an object, so a failed read of the dead-letter queue never reads as an empty one, and `dlq` and `admit` never count it as empty.
