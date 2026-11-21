# R5 P-hooks' protocol, the expand deploy (2026-11-21)

R5 P-hooks (`untangle/r5-hooks`) adds the hook helper to the runner as a command of its own, `decide`, which a run's CLI's pre-tool hook runs as the run's user, so the pair check pins it with the runner. A command is part of the runner protocol's record (`protocol.json`), and a protocol change ships in two deploys (`protocol.ts`): this is the first.

1. **Protocol 2 is recorded:** protocol 1's commands but its retired ones (`RETIRED_IN_1`, which the runner takes none of), and `decide`. Its exit codes, paths, documents and answers are protocol 1's, so the Worker writes and reads the same for either.
2. **The Worker speaks 1 and 2 (`SPEAKS`); its runner still speaks 1.** A runner of the next deploy, which speaks 2, is then never refused by this Worker, nor by a rollback to it.
3. **Its pin** (`state-format.test.ts` `FROZEN`): the record's digest, so it never changes once recorded.
4. **Lands and deploys before `untangle/r5-hooks`**, whose runner speaks 2. That branch carries these same lines, so merging main into it once this lands changes nothing there.
