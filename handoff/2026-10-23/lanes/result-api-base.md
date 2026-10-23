**Draft PR:** [#5230](https://github.com/TinyAssets/TinyAssets/pull/5230)  
**Head SHA:** `a043196c8f6c1e8dd24c9c64a007f81c397eb7fb`

Discovery now learns custom model API bases from published OpenAPI servers, protected-resource metadata, RFC 9727 API catalogs, and machine-readable root or docs links. It probes bounded, same-site candidates through the existing owner-box egress path. Offline fixtures cover each source.

**Verification:** 43 targeted Linux oracle tests passed; Ruff and `git diff --check` passed. Nothing remains undone. The full suite was not run, as requested.