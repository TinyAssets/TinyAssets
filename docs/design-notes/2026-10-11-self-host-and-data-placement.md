# Self-hosting the server, and pluggable data placement

Date: 2026-10-11
Status: research note. No code and no site text changed.
This is not legal advice. Facts are labeled evidence. Designs and risk readings are labeled inference.

Two additions, both beside what the hosted app already does. Bring-your-own-model stays the path where the hosted server calls a model the user runs. This note is the other path: the user runs the TinyAssets server itself. Separately, each kind of data can sit in a place the user chooses, and that choice can change later. A regulation such as HIPAA is not a mode in the code. A person with that need picks backends and policies that fit their own review. Whether the result is compliant also depends on contracts and on who operates the machines. Code cannot finish that.

## 1. Server self-host

The owner wants this in addition to bring-your-own-model. The earlier correction still stands for the hosted product: a user connecting their own model endpoint is not the same task as running this server. Both are now in scope. They are different gates.

### What refuses a machine that is not the droplet (evidence)

`tinyassets/platform_runtime_provenance.py` resolves one fact: the DigitalOcean metadata service at `http://169.254.169.254/metadata/v1/id` answers, and the id equals the instance id the deploy wrote down. The module's own text calls this an accidental-start backstop, not attestation. A local root can forge the listener. There is no environment-variable bypass. Tests inject a reader. They do not flip a switch.

Serving startup calls `require_process_cloud_admission` before storage, the scheduler, or the MCP transport. The comment in `create_streamable_http_app` says there is no degraded local mode: an unadmitted process raises instead of serving. `universe_server.py` maps that refusal to exit code 78. The same verdict is required at:

| Site | What an unadmitted process does |
|---|---|
| `universe_server.py` serving startup | Exits 78. Does not serve. |
| `origin_admission.py` | HTTP 503, body `platform_not_cloud`. |
| `runtime/assigned_queue_consumer.py` | Refuses the consumer. |
| `daemon_registry.py` | Refuses cloud-worker registration. |
| `branch_tasks_v2.py` | Returns `platform_not_cloud`. |
| `foreground_run_provider.py`, `background_served_provider.py` | Will not stamp `executor_class` `cloud` unless the cached verdict admits. |
| `command_center_update_maintenance.py` | Refuses. |
| `runtime_reconcile.py` | Refuses stale-fleet retirement. |

`openspec/changes/cloud-only-runtime-admission/proposal.md` records the same rule: serving startup refuses unadmitted, with no degraded local mode, and the repo removed its own ability to enroll a public Cloudflare ingress from a daemon or tray (landed in PR #3913). That removal is what keeps a laptop from becoming `tinyassets.io/mcp` by starting the process.

`docs/specs/2026-06-10-tiny-first-principles-spec.md` §11.3 says self-host is how a regulated org gets full control without a business-associate agreement with TinyAssets, and that the project must not claim certifications it does not hold. **Evidence:** that sentence and the serving comment disagree. The spec describes a path. The server refuses to start off the droplet. **Inference:** the spec is the intent. The admission code is the gate. This note does not weaken the gate on the public server.

Outbound calls from a running server still go through the SSRF checks. Loopback, private addresses, and link-local are refused. That is the bring-your-own-model limit on the hosted app. A self-hosted process that reused those checks unchanged would also refuse a model on the same machine. **Inference:** punching a localhost hole in the shared check would open it for the droplet too. The self-host mode has to carry its own allow, default off, and the cloud process must not take it.

### Smallest safe opt-in (inference)

One mode, `single_user_self_host`, default off. The public server never enters it.

The mode is refused when the process is the production droplet: metadata answers with the id the deploy recorded. A copied env file cannot turn the droplet into a self-host, and a laptop cannot satisfy production admission by setting a variable. Production startup stays `require_process_cloud_admission` with exit 78. No second bypass is added to that function.

When the mode is on and the process is not that droplet:

- Bind `127.0.0.1` only. A non-loopback bind is a different, later choice.
- One owner. No org directory, no second account in the data dir.
- Its own data directory, not the droplet's `/data`.
- It does not enroll a public ingress. That code path is already gone. The mode does not bring it back.
- Authority records say `self_host`, not `cloud`. `admitted_cloud_executor_class` stays cloud-only.
- Model calls use the existing public-HTTPS bring-your-own path. Reaching a model on the same machine is a later switch inside this mode only: loopback, one port the user named, still off by default. The shared SSRF classifier does not grow a localhost exception.

That is enough for one person to run the server on their machine against their own disk and their own HTTPS model. It is not a second multi-tenant host, and it is not the public MCP.

**What this note did not verify.** No self-host process was started. The admission sites above were read. Whether every background thread checks the verdict was not re-proved by running it. **TODO.**

## 2. Pluggable data placement

The owner wants each kind of data storable in a different way, chosen per user, changeable later: hosted cloud, the user's own storage, local disk, S3-compatible, encrypted. A person with a regulatory need such as HIPAA should be able to point the sensitive classes at a setup they believe fits. Power users build and share the backends and the policies. The platform does not hardcode a regulation.

**Not legal advice.** A storage layout does not make a deployment HIPAA-compliant, or compliant with any other regime. Compliance also depends on agreements (who is a business associate, what the contract says), on who can log in to the machines, on subprocessors, and on how the organization actually uses the system. The 2026-06-10 spec already says the project must not claim certifications it does not hold. This note repeats that. Sharing a "HIPAA shape" is sharing someone's policy document. It is not a certification.

### What exists today (evidence)

Everything for a command center is files and SQLite under one root. `tinyassets/storage/__init__.py` `data_dir()` uses `TINYASSETS_DATA_DIR` if set, otherwise `~/.tinyassets` (or the Windows equivalent). The deploy bind-mounts that root at `/data`. There is no second root per data class.

`tinyassets/command_center_layout.py` is the classification table. It splits a home into `user` content (the agent may write it) and `platform` state (the daemon trusts it). Names the owner's list maps onto:

| Class the owner named | Where it sits now |
|---|---|
| Memory | `MEMORY.md` and the other brain files (`user`). `.conversation_memory.db` (`platform`). A `lancedb` directory (`platform`). |
| Files | `workspace`, `workspaces`, `.agent-workspace` (`user`). |
| Logs | `activity.log` (`platform`). Process logs are the host's, not a per-user store in this table. |
| Credentials | `.credential-vault.json`, `.credentials` (`platform`). API keys are stored as `secret_b64` in that vault. `api/universe.py` says base64 is the convention and envelope encryption is deferred. Base64 is not encryption. |
| Run history | `runs`, `executions`, `branch_tasks.json` (`platform`). |

An unknown name is not classified. The migration refuses to guess. That table is the closest thing to a data-class list. It chooses trust (user vs platform). It does not choose a disk, a bucket, or a key.

`docs/specs/2026-06-10-tiny-first-principles-spec.md` §11.3 treats HIPAA as the archetype of a compliance playbook: the procedure is a forkable branch, the evidence is the run journal, and the content is community-built. Crypto-shredding and residency are named as private-tier work, not as a backend interface. **Inference:** the spec puts regulations in shared playbooks, which matches "do not hardcode HIPAA." It does not provide the placement seam. The playbook cannot move a SQLite file to the user's bucket, because every writer opens a path under `data_dir()`.

### Architecture (inference)

Four capabilities, each a package a person can publish. The platform ships one of each, the local unencrypted directory, and nothing that names a statute.

1. **A storage backend** answers put, get, delete, and list for bytes, addressed by class and object id. The built-in backend is the directory under `data_dir()`. An S3-compatible backend, a second disk, or the user's own server is a package. The hosted cloud is the built-in backend running on the droplet, which is what every user has today.
2. **A policy shape** is a document the user attaches. For each data class it names a backend, whether bytes are encrypted, which key id, a retention rule, and whether writes append an audit record. It does not name HIPAA, SOC 2, or any other regime. Someone may publish a shape they use for a regulated clinic. Another user reads it, changes it, and attaches their own. The platform does not score it.
3. **A router** reads the current shape at each write and read. A change applies to the next operation. Objects already stored stay where they were until the user runs a move. "Rules can change at any time" is this read. It is not a rebuild of the server.
4. **Keys** stay with the user when the shape says so. The backend stores ciphertext. The server process receives the key only for the operation the user unlocked, and does not write the key into the vault it is encrypting. A hosted shape may say the platform holds the key. That is a choice in the shape, and it is the wrong choice for a user who needs the operator to be unable to read the bytes. The code can honor the choice. It cannot make the hosted operator disappear.

Audit is its own class. The shape can put it on a different backend from the memory it describes. A policy that writes the audit log into the same bucket as the secrets it is watching is a weak shape. The platform does not forbid it. The user can.

### Gaps (evidence, then inference)

**Evidence.** One root. Classification is trust, not placement. Credentials are base64. Envelope encryption is a comment, not a module. No S3 client is wired as the command-center store. No policy document is consulted on write.

**Inference.** The writers that open SQLite and JSON by path (`credential_vault.py`, the `storage/*.py` connects, LanceDB under the home) do not go through one interface. A backend package cannot redirect them until those opens take the route. Doing that for every file in one change is a migration of the whole server. The first seam is smaller.

### Minimal first step (inference)

Do not build S3, encryption, or a HIPAA shape in the first change. Do not start the self-host process in that change either. Both are specs until a later implementation, and the public admission gate stays.

1. **Self-host, as a spec only.** Record the mode in the paragraph above: default off, refused on the production droplet, loopback, one owner, separate data dir, no public ingress, no `cloud` stamp, SSRF unchanged. The implementation is a later change that adds a verdict beside admission, not a bypass inside it.
2. **Name the classes.** Treat the owner's five (memory, files, logs, credentials, run history) as ids on top of `command_center_layout.py`. The first code change, when there is one, is a single function that today returns the existing path for a class. Every new writer calls it. Existing writers move one class at a time, credentials last, because that file holds secrets.
3. **One local backend.** The function returns that path. A second backend is a package after the function exists, not before.
4. **A policy shape with one field.** Class to backend id, defaulting to `local`. Retention, encryption, and audit are fields the document allows and the first router ignores until a backend implements them. Ignoring them loudly (the write fails if the shape asks for encryption and the backend cannot) is safer than silently storing plaintext. **Inference:** fail closed when the shape asks for something the backend does not do.
5. **No regulation in the tree.** A shared shape may say, in the user's own words, why they chose it. The platform code does not branch on those words.

A user who needs a regulated setup, after those seams exist, runs `single_user_self_host`, points the sensitive classes at a backend they operate, and holds the keys. They still need whatever agreements their regime requires with anyone else who can see the disks or the process. This repository cannot sign those agreements, and this note does not say the setup is compliant.

## What this note did not do

- No self-host process was booted.
- No list of every `sqlite3.connect` under `tinyassets/` was closed. The layout table and `data_dir()` are the evidence that placement is one root.
- No lawyer reviewed the HIPAA sentence. It is a limit on what code can claim, not a compliance opinion.
- No code, no site text, and no change to cloud admission.
