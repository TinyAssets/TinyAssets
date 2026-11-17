# r8-locate as built (2026-11-17)

Lane R's PR owed by #5664 (`r8-account-loss.md` item 7): `locate`, a namespace's owner read from its own generation, and mode `recover`, every owner restored into a new account after the old one's loss; and the gap #5664's review found, a night that hides every mark before `recover`. Researched first, as its own step (Fact 14), on `origin/main` at `0baf94b9d`.

1. **What main holds.**
   - **Who names a namespace's owner.** Only the owner's object (`file_space.ns`) and part 0's first line of each generation, the export header (`{"t":"header",...,"owner":...,"gen":...}`), sealed under the owner's data key, whose wrapped copy is in the generation's root. `restore.begin` holds the header to the object's own owner. The header's format and version were spelled twice, in `export.ts` (which writes it) and `restore.ts` (which reads it).
   - **The files Worker's entrypoints.** `FilesService`, the owner's object's, and `FilesUpload`, the upload door's (`putChunks` alone, review M5-1). RPC serves every public method of a class, so a method on `FilesService` is the owner's object's to call too.
   - **The operator channel.** Every op is addressed to an owner, whose erasure record the doors check first; the kit asks one op at a time (`askOperator`) or one no-arg op of many owners (`askOwners`, the rotation's).
   - **The copy back's reads (`readBack`).** The marks first (`erasures`: the copy as it stands, R2, and the copy at `--at` whole in the copy as it stands), then R2 and the copy listed. The drill counted, as a whole-bucket copy back's writes, every object of every unmarked namespace.
   - **The night (`copy`).** Lists the copy, then R2, then `rclone sync`s, and fails a night that hid more than a tenth only after its hides.

2. **`locate`: the files Worker's third entrypoint, `FilesLocate` (`locate` alone).**
   - Every root of the namespace listed (the keys directly under `o/<ns>/m/`, `delimiter: "/"`, page by page) and each opened under the wrapped key it carries (`getManifestRoot`), at once; the newest by its sealed `at` taken; its part 0 read under that key and held to the root's hash (`getManifestPart`); its first line held to the export header's shape (`contract/src/export-header.ts` `exportHeader`), naming that generation. Answered: `{owner, gen}`, nothing else of the part; null when the bucket holds no root.
   - The newest, since a recover restores the newest the copy held. One a restore can't load fails at its step, named, and mode `restore` takes another (a failed restore no pin held is wiped for any generation).
   - A root or part that does not open, or a part 0 with no header of its own generation, fails loudly: every generation names its owner, so either is corruption or tampering.
   - **On its own entrypoint, not `FilesService`:** a public `locate` there would let any owner's object read any namespace's owner. `FilesLocate` builds a `FilesService` for its call, as `FilesUpload` does.
   - **The header's shape moved to the contract** (`export-header.ts`), its third reader: the export writes it (`headerLine`), the restore and `locate` read it (`exportHeader`).

3. **The op: `restore.locate {namespaces}`, addressed to the platform.**
   - `namespaces` is 1 to `LOCATE_PAGE` (32) distinct namespaces, comma-separated (`locateArgs`, `locatedNamespaces`, the contract's, so the kit and the doors share the one encoding); the answer is `{located: {<ns>: {owner, gen} | null}}` (`LocatedPage`). The doors locate one namespace at a time, so at most one part of up to 4 MiB is in flight a page.
   - **Addressed to `PLATFORM_CALLER`, which no owner is named:** the op table says which ops are the platform's (`PLATFORM_OPS`), and the doors refuse (`invalid`) any op not addressed as its kind is (`addressed`): a platform op to an owner, an owner op to the platform. Owner ops reach an object only through `bindPlatformFor`, whose object refuses that name as an owner, so a misrouted op binds nothing either way.
   - The admission-caller guard lets the operator channel's contract name the constant, beside its definition, the object and the binder; nothing else in a Worker source does.
   - **The doors bind `FILES_LOCATE` to `FilesLocate`;** only `doors/src/operator.ts` reaches it (the binder guard), and the config guard holds it to `locate` alone.
   - **Its page, measured** (workerd, this host): 32 locates, each of a namespace with 20 roots and a full 4 MiB part 0, took 0.48 to 0.62 s, all of it in the files Worker; about 1.7 s at CI's 2.7x. Live, each adds a list, one round of root reads and one part read: a few seconds a page. The bound is the kit's `WAIT_MS` (120 s) for the answer; the files Worker's CPU limit (30 s) is per call, about 20 ms here; the doors' consumer only awaits. Well inside both.

4. **Mode `recover` (`offsite.ts` `recover`, `deploy.ts` mode `recover`),** in the new account after a dark deploy, with copyback's inputs (the copy's read key, a day's R2 write pair, `OPERATOR_KEYRING`), and `--at` alone: no `--owner`.
   1. The marks read (`erasures`), with every refusal a copy back has, before anything else.
   2. With anything to write, the restore record, before the first byte.
   3. Every mark the copy holds that R2 lacks written back first, from the copy as it stands (`--include "/e/*" --ignore-existing`, then a byte check), so R2 holds every erasure again and the next night keeps it. As it stands, since a mark made after `--at` is in it and not in the view at `--at`.
   4. Each namespace no mark names, of which the copy held a root at `--at` (`recovered`): every object of it R2 lacks written back and read back byte for byte. One with no root is left out: no wrapped key of it survives. No row can be asked (no object names a namespace yet): the restore's own sweeps delete what no restored row names (`r-restore-chunks.md`, `backup.ts`'s adoption sweep), as item 7 designed.
   5. Each located (`restore.locate`), a page of 32 a message, a batch push of 100 messages at a time. One located to null, or two to one owner, is a loud stop naming them, before any restore begins.
   6. Each owner's restore begun from that generation (`restore.begin`), a batch at a time, so every written namespace is named by an object, whose erasure deletes it. Each begun is named with the `restore` dispatch that carries it on. A refusal or no answer stops the run once the rest are begun, naming each: an owner who signed up in the new account before the run is not empty, and `restore` with its generation takes it (pin and wipe).
   - The asks stop loudly past `RUN_MS`, inside the apply job's 30 minutes; a run again writes nothing R2 holds and a begin asked again answers as it stands. The copy itself is bounded by the job's timeout, as copyback's is; a run cut short there is run again the same way.
   - **The stops after the writes** (a null locate, one owner twice, a begin refused) leave unmarked namespaces' objects that no object names until a run again begins them. They can't come earlier: `locate` reads the generation in R2. None is an erased namespace: every marked one is left out at step 4.
   - `askOwners` now takes each ask's addressee and args (`Ask`), so the rotation's no-arg asks and recover's go through the one batch reader; `pages` takes its size.

5. **The gap: a night over a bucket that lost its marks copies nothing (`copy`).**
   - A night lists the copy, then R2. A mark (`e/<ns>`) the copy holds and R2 lacks stops the night before its sync, naming the count: nothing deletes a mark, and the copy got it from R2 on an earlier night, so R2 lost it (a new account's bucket, before `recover`). The sync would hide every mark, and every copy back after would refuse every `--at` holding them (the lost-mark stop) and read none as the copy stands.
   - A red night is counted by the canary as not passed and alarms past two nights, so the nights stay red, loudly, until `recover` has written the marks back. Hides of what R2 deleted wait with them; a bucket that lost its marks lost the rest too, so no hide is wanted then.
   - **Rejected:** keeping the nightly off the new account by hand (a step a person must remember; the settings are repository secrets the new account reuses); refusing any night that would hide more than a share before the sync (it would hold an erasure's hides back, which the copy's window forbids: `MAX_DELETE_SHARE`); a list of marks kept off the bucket (a second record of the erasures).

6. **The drill counts what `recover` writes** (`recovered`, one function for both): the marks R2 lacks, and the objects of unmarked namespaces with a root. It used to count a rootless namespace's objects as written.

7. **Not built here: a lost object, the account intact** (item 7's last bullet): every unmarked namespace whose root R2 holds located, the one naming `--owner` taken, then mode `restore`. With it the PR passes 1,500 lines, so it is its own line, `untangle/r8-lost-object` (`tasks.md`).

8. **Found and fixed (untangle).**
   - The export header's format and version, spelled in `export.ts` and `restore.ts`: one shape in the contract (item 2).
   - `askOwners` took no args, and `pages` one size: generalized for their second caller (item 4).
   - The drill's count and a whole-bucket copy back's writes were two rules: one (item 6).
   - `deploy.ts`'s list of modes lacked `copyback`; the checklist's "Not yet" bullet named this PR's work.

9. **The proof.** Fakes and workerd only; nothing live.
   - `files/test/chunks.test.ts`: the newest root's owner answered, nothing else; null with no root or a rootless part; roots listed past a page; a part 0 with no header, another generation's or another version's, and a part moved from another namespace, each a loud failure.
   - `doors/test/operator.test.ts`: an owner's export, then `restore.locate` through the real files Worker answers its namespace's owner and generation, and null for one with no root; the op addressed to an owner, and an owner op addressed to the platform, refused; args out of shape, twice or past a page refused.
   - `doors/guards`: `FILES_LOCATE` bound to `FilesLocate` alone, serving `locate` alone, reached only from `operator.ts`.
   - `deploy/guards/offsite.test.ts`: a night over a copy holding a mark R2 lacks copies nothing; recover writes the record, then the marks, then the objects of unmarked rooted namespaces only, then locates and begins each; run again it writes and records nothing and begins each again; one owner twice, a null locate and a refused begin each stop loudly as item 4 says; the marks unreadable, a name of no layout, or a time outside the window refuse with nothing recorded, written or asked; a refused record writes nothing; the drill leaves a rootless namespace out.
   - `deploy/guards/deploy.test.ts`: mode `recover`'s plan; with `--yes`, the record, the copy back, then `restore.locate` to the platform's principal and `restore.begin` to each owner; pages of 32; a refused page stops before any begin; no `--owner`; each missing key refused before any step.
   - **Mutations**, each failing its test: the night's lost-mark stop dropped; marked namespaces written; rootless namespaces written; the marks not written back; the record not put; one owner's two namespaces begun; a refused begin no stop; locates addressed to an owner; the oldest root taken; the header held to no generation; one page of roots listed; the doors' addressing unchecked; the binder guard's `FILES_LOCATE` rule; the page unbounded.
