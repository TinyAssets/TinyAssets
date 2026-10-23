LANE: S10, platform state outside boxes (registry row S10 in docs/architecture.md: second path "daemon_server.py 5261,
27 CREATE TABLE"; guards test_storage_registry(_complete), test_owner_stores; removed by C6). Worktree
C:\Users\Jonathan\Projects\wf-s10-tables, branch conv/s10-platform-state-out (from main eb582d82d08).
After the C6 cutover the daemon (now the router plus owner daemon in the box) should hold no ad-hoc platform tables:
owner state lives in the owner's box stores, shared platform state in storage/ stores with one owner per fact
(design.md T2, D8 "shared stores stay shared, owner stores move"). Inventory the 27 CREATE TABLE sites in
daemon_server.py, move each to its one home (owner store in the box, or a storage/ store), delete dead ones, and make
the registry guard read zero. Use the generic layout reconciliation already on main (sqlite_schema.py ensure_table with
table lifetimes). No data migration may drop rows: if a table holds live data, the move carries it through the
existing one-shot migration mechanism and says so. Open a draft PR.
