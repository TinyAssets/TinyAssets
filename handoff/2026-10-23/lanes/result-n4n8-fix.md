PR [#5239](https://github.com/TinyAssets/TinyAssets/pull/5239) is updated and pushed at **`bdfb14ed6b00485fcd1331c71e6d292469a02e5e`**.

N4 now extends the existing protected live view and specifies box-local OS isolation from agent bash, with a named isolation test. N8 specifies broker-custodied pairing, an owner-bound `/api` relay, revocation, and deletion of the legacy tray host during implementation. The duplicate companion change was folded into N8; registry rows and created dates were corrected.

Both OpenSpec strict validations passed. The targeted Linux oracle run passed **21 tests**; Ruff and the shape registry check passed. Implementation and live proof remain unchecked because this PR is spec only.