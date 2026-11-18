# B24. The old proxy across a handback

The step 12 decision (d3 §Q4) removes the old proxy publisher before routes move. Its push and manual jobs can publish the routes in the old Wrangler configuration. The manifest previously disabled only the old pager. Step 12 deletes the publisher and consumes its manifest entry in the same commit, so the inventory continues to match the checkout between phases. The checklist accounts for deployments already started.

The Worker, internal CNAME and Access service token stay through the founder's hold. Their identifiers come from the old Wrangler configuration, its origin and the Access setup. Check the tunnel UUID against the origin CNAME before retirement. The old preflight says the configured ID was never verified, and the tunnel setup accepts a name chosen by the operator.

Handback uses the saved route record. Once the old stack restarts, the manual handles workflow calls `mcp_public_canary.py --assert-handles`, which imports `_canary_common.py`. Both files must outlive the rest of the old stack in the checkout. This path uses its built-in handle set and the old canary principal. The manual workflow needs the principal and its GitHub production credential even after route handover. This credential is separate from the proxy's Access service token. The optional edge preflight reads the daemon's browser-origin policy, but the handles workflow does not request it.

A local hold rehearsal using only the manifest’s retained probe scripts ran the actual handles command against a loopback fixture. Eleven requests checked both metadata paths, seven advertised tools, the anonymous and canary responses and the status record. With the previous inventory the command was absent.
