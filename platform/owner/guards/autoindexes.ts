/**
 * The indexes SQLite makes on its own for the owner's schema: one for each key of a rowid
 * table that is not its `INTEGER PRIMARY KEY`, and one for each `UNIQUE` constraint, named
 * `sqlite_autoindex_<table>_<n>`. The schema spells none of them, so the owner-bound guard
 * reads them here, and a workerd test holds this list to the migrated database's own
 * (`test/indexes.test.ts`), so it cannot go stale. Plain data: workerd and Node both read it.
 */
export const AUTOINDEXES: readonly string[] = Object.freeze([
  "sqlite_autoindex_agent_cards_1", "sqlite_autoindex_agent_cards_2",
  "sqlite_autoindex_collection_history_1",
  "sqlite_autoindex_collection_rows_1", "sqlite_autoindex_collection_rows_2",
  "sqlite_autoindex_collection_seqs_1",
  "sqlite_autoindex_egress_reservations_1",
  "sqlite_autoindex_ledger_messages_1", "sqlite_autoindex_ledger_messages_2",
  "sqlite_autoindex_ledger_readers_1",
  "sqlite_autoindex_ledger_recipients_1",
  "sqlite_autoindex_ledger_wakes_1",
  "sqlite_autoindex_message_bodies_1",
  "sqlite_autoindex_model_reservations_1",
  "sqlite_autoindex_outside_agents_1", "sqlite_autoindex_outside_agents_2",
  "sqlite_autoindex_outside_clients_1",
  "sqlite_autoindex_owner_sessions_1",
  "sqlite_autoindex_policy_proposals_1",
  "sqlite_autoindex_proposal_shares_1",
  "sqlite_autoindex_runs_1",
  "sqlite_autoindex_sandbox_signins_2",
]);
