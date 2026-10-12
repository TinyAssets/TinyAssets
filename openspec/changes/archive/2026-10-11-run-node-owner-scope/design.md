## Context

Cell gates conflate run actors with account owners. The mapper binds owner/center pairs from the broker's append-only admission log, which is the authority for the cell identity.

## Goals / Non-Goals

Restore existing graph, automation, sub-agent and shell network execution. No production mutation, ownership migration or new execution authority.

## Decisions

Use one resolver for all execution cells. Read the live, unretired center admission through authenticated broker IPC; accept only its owner or the exact internally bound `universe:<center>` / `command_center:<center>` actor. Reject foreign actors, missing ownership and retired admissions. Keep mapper and inode checks. Do not infer ownership from admin grants or billing records.

Exercise the real server on the restored cutover snapshot, with local vendor/GitHub fixtures and an internal Docker network. Preserve the checking proxy, descriptor pins and jail boundaries.

The graph compiler passes the effect chain's already resolved center to the node sandbox; its `base_path` is the global run database root and is not a cell mount. Curl remains installed in the runtime image (the setup purge previously removed it), and egress-enabled tool cells receive the public CA store read-only. DNS stays in the daemon's checking proxy.

HTTP effectors also use the shared resolver when the acting principal is the exact center actor. The resolved owner then passes the unchanged broker connection/grant/destination checks. This repairs the refusal that became visible only after the node cell itself succeeded.

Audit: node, tool preparation/execution, image decoding, workspace git, video, preview rendering/output, package and provider discovery/execution cells use the shared resolver. Workspace dependency provisioning consumes the resolved principal from the shared remote-git scope, including its four-value return contract. Workspace effects already carry `execution_context.owner_user_id`; remote workspace reconciliation supplies its explicit persisted principal. Provider engine routes still require the independently bound owner route. Account deletion retains its authenticated owner and durable deletion-token contract, including finish after retirement; executing a graph does not grant account-deletion authority.

The oracle's optional isolated metadata fixture shares only a loopback-only network namespace with the daemon. Its fixture sidecar assigns the link-local metadata address; the daemon retains the exact compose capabilities and security options. This avoids conflicts with another lane's fixed Docker subnet without exposing a restored backup to an external network.

## Risks / Trade-offs

Actor confusion could widen access: test foreign owner/center actors, malformed actors, retired admissions and broker failure. Shell fixes must retain denied private-network access. One broker lookup per boundary avoids stale ownership caches.
