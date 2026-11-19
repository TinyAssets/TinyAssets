# R5 policy census as built (2026-11-18)

The existing offline census reports how many policy documents the new validator would archive. That number cannot tell whether an owner configured rules, spending limits or an egress ask. The census now also reports a fleet-wide `legacy_policy` tally, using the policy rows that the bundle builder reads. Run the existing `census` command against a verified backup copy to obtain actual counts.

The old broker's provisioner in `tinyassets/broker/policy.py` stored an empty entries array, four null spend settings and an empty map of source ceilings by default. For the subset of the document the tally reads:

- `with_entries` counts documents with explicit entries; `entries` counts the stored rows. Explicit settings can repeat a default. These numbers describe configuration, not which decisions would change under translation.
- `with_spend_settings` counts documents carrying at least one spend setting; `spend_settings` counts each non-null daily, monthly, transaction or ask threshold and each named source ceiling, including zero.
- `with_egress_ask` counts an explicit ask. A legacy document from before egress existed still contributes its stored entries and caps.
- `classified_documents` counts documents with recognisable sections. Other known policy rows, including malformed documents or ones on a tree the survey cannot read safely, contribute to `unclassified_documents`. Store and backup failures remain visible through the ordinary census checks. An owner's oversized file alone does not hide their policy. Withheld owners are excluded.

No owner ID, rule contents, source, cap amount or destination is included in these counters. The archived bytes, bundle and admission policy are unaffected. Translation of the action defaults, the archive disposition and the founder’s notice wording remain on P-import.

## Shared text reader at integration, November 19

- [x] Rechecked the policy reader and both consumers against the staged verbatim-row change. Keep the single `policyDocuments` call in the census and pass those documents into the bundle builder. Its account reader must select named columns through the shared `tableRows` implementation, including its explicit collection ordering.
- [x] The ordinary regression on `a248b68e` distinguishes the two integrations on Node 22. The five-stage rehearsal at `70110c7d` without the shared text reader truncated an unrecognized stored document in its readable archive, even though its ordinary Node 24 run passed that case. Adding the verbatim reader to the same five stages passed all 181 ordinary importer cases and the full platform typecheck on Node 22. Five independent synthetic documents per source and runtime confirmed both the readable file and the separate archived SQLite copy, recorded at `bf36bfa9`.
- [ ] Real representative census, legacy mapping decisions and integration on main remain open.
