# Replaying the test Workers' bindings for a Wrangler upgrade

Step 18 requires the comparison established in [T0b](https://github.com/TinyAssets/TinyAssets/pull/5510).
Install each revision's platform lockfile in a separate checkout. Run the same probe against
each installation, using its own Wrangler and Worker kit. A nonzero exit identifies a mismatch
within that revision. The final diff shows changes across the two.

```sh
node scripts/probes/wrangler-bindings.mjs ../base/platform > base-bindings.txt &&
node scripts/probes/wrangler-bindings.mjs ./platform > candidate-bindings.txt &&
diff -u base-bindings.txt candidate-bindings.txt
```

The probe compares the actual vars, KV namespaces, queue producers and consumers, service
entrypoints, Durable Objects, R2 buckets and assets. Wrangler reads copies of the checked-in
config files in a fresh directory with a clean environment. Each required secret receives its
own synthetic value. The report records secret names and hashes the other binding options.
R2 bucket IDs and Wrangler's original bound and unbound class descriptions have their own
digests so an upgrade that changes a representation stays visible.

At main `f934871ad` and Wrangler 4.154.0, both Node 22 and 24 produced the same 30 lines
across all six Workers. Removing a service entrypoint, changing a queue mapping, dropping a
hosted SQLite class, changing a bucket binding, dropping a var and changing the assets routing
policy each failed in its binding family.

The full bump also compares the options the suites actually pass, module bytes and the browser
harness. In two disposable checkouts at the current pin we captured seven Vitest workerd mains,
31 auxiliaries and eight browser Workers, including the eight after Miniflare's option
conversion. The 46 module/script hashes and five source entries matched after normalizing
checkout paths and the browser fixture's random owner, with declared secret values excluded.
A gateway suite override changed this comparison while every underlying Worker config matched.
Repeat the full comparison on the upgrade candidate before using its results.
