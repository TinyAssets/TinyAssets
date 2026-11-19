# Live import rows and the owner's archive

The frozen backup survey identifies each owner's stores and assigns each table a
disposition. The census and bundle builder share the same tree builder. On main
at f934871ad, the builder copies the kept tables and then discovers the two
broker databases again to project the live rows. A local fixture changed one
collection body after its archive was made. The real builder wrote the earlier
body into the owner's archive and the later one into the live import rows on
Node 22 and 24. A second fixture added an optional store after the survey. Its
two rows appeared in the result even though its archive was absent.

- [x] Read the live policy and ordered collections from the same kept-table
  SQLite files that supplied the bundle bytes. Use the survey's carry
  disposition and broker archive path. Whole-file archives and dropped tables
  retain their existing treatment.
- [x] Both focused regression cases pass on this change.
- [ ] Record the full importer and TypeScript results. Reconcile the row
  queries with the staged verbatim-text reader and check the joined behavior.

The cutover still requires a frozen source copy. This change aligns the two
representations within each generated bundle. Earlier bundles and previously
staged owner subsets still require their original provenance and a fresh
deletion comparison.
