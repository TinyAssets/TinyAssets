## Why

K2 cannot project the remote box adapter to four tools until bash can reach the
same owner-authorized capabilities as local ta without receiving credentials.

## What Changes

- Bind a credential-free reverse RPC channel to the existing box execution.
- Dispatch through the existing engine ta broker, with owner, center and turn
  fixed by the trusted caller; expire access when the tool session closes.
- Record request receipts before dispatch so cancellation and lost replies never
  authorize replay of an effect with the same request identity.
- Prove the transport and credential boundary in real Linux processes.

## Capabilities

### New Capabilities
- `remote-box-ta-bridge`: turn-bound capability calls from remote box bash.

### Modified Capabilities

None. The public MCP inventory and provider inventories are unchanged.

## Impact

Owner: Codex. Branch: feat/remote-box-ta-bridge. One draft PR to main.
Affected: agent_loop box-exec routing, ta client transport and Linux proofs.
This consumes the existing executor contract; it does not replace the executor
or duplicate the separate background-job lane.
