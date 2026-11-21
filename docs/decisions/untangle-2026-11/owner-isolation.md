# owner-isolation: how a trusted service knows whose call it serves (2026-11-21)

`untangle/owner-isolation`. The gap is the verifier's row I.sandbox-owner-arg
(`verify_uptime.md`, ADR-032.1 PARTLY). The sandbox Worker picks an owner's
`SandboxControl` by an owner name that its caller passes as an argument. Every call site in
the owner object passes its own name, but only by convention. So one bug in owner-object
code could reach another owner's sandbox, with that owner's synced files and vendor sign-in.
The files Worker's namespace-wide operations take a namespace and no key.

Research comes first (Fact 14), then the choice, then the build.

## 1. What Cloudflare lets a callee know about its caller

Sources: the `cloudflare/cloudflare-docs` repository at `35025d7` (2026-10-30), read
2026-11-21 from a local checkout. `developers.cloudflare.com` cannot be resolved from this
session, so the pages are cited by their path in that repository. Each one is the source of
the page at the matching `developers.cloudflare.com` path.

1. **Service binding `props`** (`workers/runtime-apis/context.mdx`, "`props`"). A service
   binding's config can carry `props`, which the callee reads as `this.ctx.props`. The page
   says: "The Workers platform is designed to ensure that `ctx.props` can only be set by
   someone who has permission to edit and deploy the worker to which it is being delivered."
   Props are fixed per binding in the caller's config. So they name the calling Worker, or
   one resource the binding grants ("a custom binding"). They cannot name one of the many
   objects that a single Worker hosts.
2. **Dynamic props through `ctx.exports`** (same page, "Specifying `ctx.props` when using
   `ctx.exports`"). Loopback bindings let the caller choose props "because the caller is the
   same Worker, and thus can be presumed to be trusted". The resulting binding "is to be
   passed to another Worker over RPC". So only the callee's own Worker can mint a binding
   whose props name an owner, and it would have to learn that owner from its caller first.
   The owner Worker turns `ctx.exports` off (`disable_ctx_exports`), and so does the sandbox
   Worker.
3. **Stubs and `RpcTarget`s passed over RPC as capabilities** (`workers/runtime-apis/rpc/index.mdx`,
   "Structured cloneable types, and more" and "Forwarding RPC stubs"; `rpc/lifecycle.mdx`).
   Functions, `RpcTarget` instances and "RPC stubs themselves" cross RPC as stubs. But a
   forwarded stub's proxy "only lasts until the end of the Workers' execution contexts. A
   proxy connection cannot be persisted for later use." Stubs received as parameters are
   disposed when the call returns unless `dup()`ed, and every stub is disposed when its
   event is done. An owner object acts on its own alarms, with no caller in flight, so it
   cannot keep such a capability. A trusted factory that returns an owner-bound `RpcTarget`
   still has to be told the owner, by an argument.
4. **The object's own name** (`durable-objects/api/id.mdx`, "`name`"). `ctx.id.name` is the
   name the caller used with `idFromName()` or `getByName()`. It is set by the runtime, and
   it is kept for alarms: "When the alarm fires, `ctx.id.name` holds the same name the
   object was originally accessed with." It is `undefined` for `idFromString()`,
   `newUniqueId()` and names over 1,024 bytes. A `DurableObjectId` is made only by a
   namespace's methods (`durable-objects/api/namespace.mdx`).
5. **No documented way for a callee to learn which Durable Object called it.** I searched the
   Workers and Durable Objects docs for caller identity. Nothing gives a callee the calling
   object's id.

### Signed owner tokens

The sandbox Worker could refuse a wrong owner by itself if each call carried a token minted
outside the owner Worker, for example an HMAC of the owner under a key that only the doors
and the sandbox hold. The token would be kept in the owner's own storage. An object would
then hold only its own token, and runtime storage isolation would keep it there. That is the
only shape here in which the callee checks the caller without trusting the owner Worker's
code. It is not chosen:

- **It is a check, not a shape.** A wrong owner can still be written down; it is refused
  afterwards.
- **It adds machinery:**
  - a new secret on two Workers, with its rotation;
  - a new field in the binder's `Binding` contract, since a token must reach the object
    from a verified call;
  - a stored row;
  - restore and erasure handling.
- **It breaks Direction 8 for an owner that is only on a schedule.** Such an object reaches
  the sandbox from its alarm and holds no token until a door call delivers one.
- **It buys little over the shape chosen below.** In that shape the only code that turns an
  object into an owner name is one line, and it reads the runtime's `ctx.id`.

### Where an owner's state is selected by something only that owner holds

The services the verifier's §4 map lists, sorted by what picks the owner's state.

| Service | What picks the owner's state | Can owner A's object name owner B's? |
|---|---|---|
| Owner object | the runtime (`ctx.id.name`, private storage) | no |
| Gateway | the owner's sealed blob, whose additional data binds `owner`, connection, version and kid (`gateway/src/keyring.ts`). The blob lives in the owner's rows. | no. A's blob does not open as B's, and B's blob is in B's storage. |
| Files Worker | the namespace, 128 random bits that the object mints or adopts and keeps in its own rows (`file_space`) | only through a namespace from outside: a restore's `{namespace, gen}` from the operator channel. `restoreBegin`, `restorePin` and `restoreHold` act only once part 0's header names this object's owner (`restore.ts` step 1; `restore.test.ts` "another owner's generation"). |
| Egress door | a token MAC'd under `ROUTING_MAC_KEY`, checked by the object against its own stored secret hash | no. A token minted for B reaches B's object, which holds no matching secret. |
| **Sandbox Worker** | **the owner's name, a public id (the AuthKit `sub`), passed by the caller** | **yes, by one bug** |

So the sandbox is the one service whose selector is a value that anyone could write. Every
other service is selected by something held only in that owner's object.

**The files Worker's namespace operations.** The verifier proposed making them take the
wrapped key and open it. That would add nothing. `getManifestRoot(ns, gen)` answers the
root's wrapped key for the namespace alone (`files/src/index.ts`), and so it must: a
restore after the object's loss has only the bucket. So a caller holding a namespace can
get its key, and the namespace is the capability. It is held only in the owner's rows, and
it enters from outside only through the restore's header-checked steps. The owner Worker
binds `FilesService`, which lists no namespaces; only the operator's `FilesLocate` does. So
the files Worker is left as it is. This section records why. A key check that opens a key
the namespace already hands out is a check with no effect.

## 2. The shape chosen: the owner's name is bound once, from the runtime

**A wrong owner cannot be written at any call site.**

1. **The owner side.** `owner/src/sandbox.ts` `boundSandbox(service, id)` is the only code
   that names the `SANDBOX` binding's owner argument. It turns the binding into the owner's
   own `OwnerSandbox`: the same methods, with no owner parameter. Each call reads the owner
   from the `DurableObjectId` it was given, and refuses when that id has no name, as the
   object's own `owner()` does.
   - It is built once, in the `OwnerDO` constructor, from `ctx.id`.
   - The owner Worker binds no Durable Object namespace and is handed no `ctx.exports`. The
     config and self-reach guards hold this, so the only `DurableObjectId` its code can
     hold is its own object's.
   - Every part takes `sandbox: OwnerSandbox` (`ObjectReads.sandbox`), and the env that
     parts see has no `SANDBOX` (`ObjectReads.env` is `Omit<OwnerEnv, "SANDBOX">`).
   - The guard `owner/guards/sandbox-bound.test.ts` holds that `SANDBOX` is read in
     `owner/src` only as the argument of that one `boundSandbox(…, ctx.id)` call. A cast
     around the type is caught too.
2. **The sandbox side.** `SandboxControl` serves only the owner it is named by
   (`ctx.id.name`), as `OwnerDO` does (ADR-032.2). Its methods take no owner: `start`,
   `signin`, `erase` and the new `unwait` read the object's own name. Before, `start` and
   `signin` took the owner a second time, and `erase` took it again through `principal()`:
   a hidden link that lets the name passed disagree with the object called. A control
   reached with no name (`idFromString`, `newUniqueId`) refuses every call. The `Sandbox`
   entry turns the wire's owner into a control in one place (`#owner`), and every
   per-owner effect follows from that control's name. `unwait` now goes through the control,
   so the entry no longer reaches the platform's line itself.
3. **The wire is unchanged.** `SandboxService` keeps its owner argument. Between Workers it
   is the trust boundary of ADR-034 decision 4.2: only the owner Worker binds the sandbox
   Worker, which the owner's config guard `SERVICES` and the sandbox's lack of routes hold.
   `props` (§1.1) could only restate that binding. No storage changes and no class is
   renamed, so the migration locks are untouched.

**Against the intent.**
- ADR-032.1: "The object's code has no way to another owner's object". That extends to the
  owner's sandbox object: the object's code holds only a handle bound to its own name.
- ADR-032.2: an object "serves only the owner it is named by (`ctx.id.name`)". That now
  holds of `SandboxControl` too.
- The Clean-Sheet doc (rev 87): per-owner objects are "isolated by the runtime", and for
  files "the binder picks the owner, the owner's own object holds its namespace and
  manifest". The owner object is the unit of isolation. Each service either selects by
  something only that object holds, or, for the sandbox, by the name the runtime gave the
  object, bound in one place.
- Direction 2: no provider-specific code is added.
- Direction 8: nothing here needs a caller in flight, so an object that acts on its own
  alarm reaches its sandbox as before.

## 3. Left, with reasons

- **The gateway's `owner` argument.** It is checked by the seal's additional data against a
  blob only the owner holds (the table above). Binding it in a handle too would be a second
  guard on a property that cryptography already holds.
- **The files Worker** (§1, the namespace operations).
- **`ROUTING_MAC_KEY` in the owner Worker.** The owner Worker can mint any owner's token
  (the verifier's inference). A token reaches only the owner it names, and that owner's
  object checks its own secret, so a wrong token reaches no one's data. Taking the key out
  of the owner Worker is ADR-034's routing design. It is not this gap.
