# S28: seal post-C6 broker custody

## Why
PR #4949 targeted daemon vaults and role launchers that C6 replaced. Current broker custody still persists HTTP/OAuth records and native subscription state in plaintext; browser state has independent raw keys.

## What Changes
One broker-only KMS root seam wraps per-owner envelope keys. All HTTP, subscription and browser credential state uses that hierarchy. Rotation and platform backup/restore preserve sealed bytes and wrapped keys. No owner contents are read by shared services, and no key or credential may reach a box. Local roots are explicit test dependencies only.

## Capabilities
- Modified: credential-vault, browser-login-custody.

## Impact
Broker custody, its existing HTTP store, platform backup validation, targeted tests. Cloud provisioning and production cutover are founder actions. Owner: this lane; branch conv/s28-vault-kms; one draft PR; tracker 8.14 / S28.
