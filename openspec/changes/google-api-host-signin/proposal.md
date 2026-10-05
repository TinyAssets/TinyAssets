## Why

The founder's Calendar request fell through to destination-host discovery and key paste. API hosts need to resolve to the directory's issuer with explicit API scopes, including declared subdomains.

## What Changes

- Extend trusted directory `hosts` with explicit `*.domain` coverage, retaining named scope sets and exact host defaults.
- Declare Google's API host coverage and teach connect guidance to specify API hosts and scopes.
- Preserve discovery for uncovered hosts and reject unrelated hosts from directory resolution.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `universe-connection-oauth`: Directory API host coverage and explicit scope routing.

## Impact

OAuth directory resolver/data, connect skill and plugin mirror, regression tests. Owner: Codex; branch `fix/google-api-host-signin`; one draft PR. Existing generic OAuth delivery remains separate; this lane only extends host coverage and guidance.
