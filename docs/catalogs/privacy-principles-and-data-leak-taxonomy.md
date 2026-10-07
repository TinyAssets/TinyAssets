# Privacy Principles + Data-Leak Taxonomy — Catalog v1

**Status:** v1 catalog. Platform ships this file; chatbot reasons against it when helping a user design or edit a node.
**Audience:** primary consumer is the chatbot (Claude.ai / other MCP clients) when acting as a co-designer. Secondary: human contributors writing new nodes.
**Source of truth:** `project_privacy_per_piece_chatbot_judged.md` (host 2026-04-18) + `docs/specs/2026-04-18-full-platform-schema-sketch.md` §1.2 dual-layer model + §17 of the full-platform design note.
**Licensing:** CC0-1.0 — like all workflow content.
**Versioning:** `catalog_config.privacy_taxonomy_version` stores the active version; chatbots should name the version when citing.

This catalog is the **reasoning surface** for per-piece privacy decisions. When the chatbot is helping a user design a node, it consults this document — structured as (a) principles, (b) leak taxonomy, (c) decision matrix, (d) usage guide.

---

## 1. Principles

Ordered by precedence. When two principles conflict, the higher-listed one wins.

### P1. Every part that can be public is public.

The commons only compounds if the default for concept-layer content is public. Private is the exception, not the default. The chatbot's bias is toward surfacing "this is safely publishable" unless concrete evidence says otherwise. Not "might leak something" — **evidence**: identifiable PII, credentials, instance data the user can't afford to expose.

### P2. The user's data is the user's. Period.

Private-instance data never leaves the owner's host. Not to Supabase. Not to the catalog repo. Not to analytics. Not as training data. Not under any reasoning that starts with "but it would be useful if…" — the answer is still no.

### P3. Concepts are commons-owned, instances are user-owned.

Once a user publishes a concept, they've contributed it to the commons. They cannot later retract it (cascade-delete would destroy derivatives). They CAN anonymize attribution via wiki-orphan deletion (per `project_q10_q11_q12_resolutions.md` Q12b). Instance data remains user-owned and IS fully deletable.

### P4. The chatbot defers to the user, not the platform.

When in doubt, the chatbot asks. "I'm marking this field public because it looks like a generic technique — keep private instead?" is the right question. "I'm marking this private because our privacy model says X" is pattern-matching without user context. The catalog guides reasoning; it doesn't pre-empt the user.

### P5. Structural enforcement > trust.

Anything marked private is enforced at the Postgres-RLS + role level, not by "we promise not to read it." Training-data exclusion is a separate Postgres role with column-level permissions per `project_privacy_per_piece_chatbot_judged.md` §5.

### P6. Reversibility matters more than perfection.

A field marked public that turns out to be private can be re-marked and the catalog export re-emits a delete-diff (per #32 §2.4 public→private flip behavior). No permanent damage from a good-faith mistake. A field marked private that turns out to be publishable is also easy — flip visibility, re-export, it appears in the catalog on next batch. The chatbot should bias toward **marking uncertain items private** because that's the safer direction to err in.

### P7. Transparency at the surface.

Every `artifact_field_visibility` row is user-visible (owner only). The user can always see why a field was marked the way it was (`reason` column). "Black box privacy" is a failure mode — the user should always be able to ask "why did you mark this private?" and get a concrete rationale.

---

## 2. Leak taxonomy — what "private" actually means

Five categories of data a user might put in a node that should NOT land in the public catalog. The chatbot checks each incoming field against these categories.

### T1. Identifiable PII of non-consenting parties

Real names, addresses, phone numbers, emails of people who didn't agree to be in this workflow. Also: facial photos, biometric identifiers, government ID numbers.

**Examples:**
- Invoice workflow: the supplier contact's email, the AP manager's name.
- Research workflow: a study participant's identifier.
- Screenplay workflow: a real person's unauthorized biographical detail.

**Decision default:** **private**. Test: could a stranger Google this and find the individual? If yes → private.

### T2. Credentials + secrets

API keys, passwords, OAuth tokens, database connection strings, private keys, signed URLs with embedded tokens.

**Examples:**
- `sk-abc123...` (OpenAI key pattern)
- `postgres://user:password@host/db` connection strings
- `.env` file contents
- Keys embedded in YAML front-matter by accident

**Decision default:** **private AND training-excluded AND trigger an alert to the user.** The `training_excluded=true` column applies. These shouldn't exist anywhere in concept-layer — the chatbot should actively flag "this looks like a credential, are you sure you want to include it?"

### T3. Instance data — the user's own business/personal values

Specific file paths, company names, charge codes, hardware identifiers, internal tooling names, personal account numbers that aren't PII but reveal the user's context.

**Examples:**
- Invoice workflow: `C:\Users\jonathan\Dropbox\MyCompany\invoices\Q4-2026\*.pdf`
- Research workflow: "ran on UW Madison's genomics cluster"
- Recipe workflow: "my grandmother's chocolate chip recipe"

**Decision default:** **private** for instance-specific content. The **concept** ("invoice OCR node") is public; the **instance** (the actual file paths + company names) is private. Chatbot should look at each field and ask: "is this describing what the node does, or what this particular user's data is?"

### T4. Sensitivity context — what's legal/regulated/protected

HIPAA-adjacent health data, GDPR-adjacent personal data, FERPA-adjacent education records, PCI-adjacent payment card data, classified government information.

**Examples:**
- Healthcare workflow: patient records, diagnoses
- Education workflow: grades, student IDs
- Legal workflow: privileged client communications

**Decision default:** **private AND training-excluded AND prompt user to confirm regulatory compliance.** Chatbot should say: "This looks like it might be [regulated category]; confirm you have authority to handle this data and that it stays on your host."

### T5. Third-party copyrighted content without license

Content the user doesn't own and doesn't have a permissive-license right to republish. Quoted material from paywalled sources, copyrighted images, trademarked logos.

**Examples:**
- Research workflow: full text of a paywalled paper (vs. a summary with citation)
- Screenplay workflow: quoting copyrighted song lyrics or film dialog
- Fantasy workflow: including another author's character without permission

**Decision default:** **private OR redact** depending on user intent. If user wants to publish a remix that references but doesn't reproduce, guide toward fair-use framing. If user wants to republish verbatim, check license compatibility with the commons CC0-1.0.

---

## 3. Decision matrix

For each field the chatbot evaluates, apply the matrix below. Rows are the leak taxonomy; columns are the decision.

| Category | Default visibility | Training-excluded? | Needs user confirm? | Rationale for user |
|---|---|---|---|---|
| **T1. Identifiable PII** | private | yes | yes (one-time on first encounter) | "Looks like a real person's details; keeping private. Confirm or adjust?" |
| **T2. Credentials/secrets** | private | yes | yes (every time — these should not exist) | "This looks like a credential — pulling it out of the concept entirely. Want me to continue?" |
| **T3. Instance data** | private | no (concept-pattern is shareable) | no (silent) | "Looks like instance data specific to your setup; keeping private. The concept-pattern stays public." |
| **T4. Regulated data** | private | yes | yes (regulatory compliance question) | "Looks like regulated data. Confirm you have authority + this stays on your host?" |
| **T5. Copyrighted content** | private OR redact | yes (if private) | yes (license question) | "This looks like copyrighted content. Summarize instead of quoting, or do you have republication rights?" |
| **None of the above — generic technique description** | public | no | no (silent) | "Generic technique; publishing as part of the concept layer." |
| **None of the above — structural pattern** | public | no | no (silent) | "Structural pattern that remixes well; publishing." |
| **Ambiguous — could be T1/T3/generic** | **private with asking** | no | yes | "I'm not sure — keeping private until you tell me. Public, private, or want context?" |

**When multiple categories apply:** the most-restrictive category wins. A credential that's also instance-data → private + training-excluded + user-confirm (T2 wins over T3).

**Concept/instance split applies at the field level, not the node level.** One node can have public-concept fields ("this node captures invoice numbers") + private-instance fields ("these specific invoice PDFs"). See schema spec #25 §1.2-1.3 for the `artifact_field_visibility` mechanism.

---

## 4. How the chatbot uses this catalog

### 4.1 When the chatbot reads the catalog

On any conversation where the user is **authoring or editing** a node, goal, branch, or soul file. Load into working context at the start of the authoring session. Reference by version (e.g. "privacy catalog v1 §2.T2") when explaining a decision.

NOT on read-only browsing conversations. The catalog is a reasoning guide for write-side decisions.

### 4.2 Control loop

For each field the user adds or edits:

1. **Classify** against §2 leak taxonomy. Apply P6: bias toward private on uncertainty.
2. **Apply the decision matrix** §3. Pick the default visibility + whether to confirm with the user.
3. **Write the `artifact_field_visibility` row** with a clear `reason` per P7. Include the taxonomy category + principle cited (e.g. "T2 credentials per principle P2").
4. **If confirm-needed, ask the user before committing the write.** Frame as a question, not a report. Give them the one-line "why" + accept either "public" or "private" or "explain more."
5. **Log the decision.** `artifact_field_visibility.decided_by='chatbot'` or `'user'` as appropriate.

### 4.3 What the chatbot should NOT do

- **Don't auto-mark all fields private "to be safe."** That destroys commons value. Bias toward public when the taxonomy says public.
- **Don't override user's explicit choice.** If the user says "mark this public," mark it public (record `decided_by='user'`). Exception: T2 credentials — the chatbot should still refuse; principle P2 is absolute.
- **Don't guess at regulated-data compliance.** If it looks like T4, ASK. Never assume "they probably have authority."
- **Don't silently change a prior decision.** If a previously-public field now looks private (e.g. new instance data appeared), update `artifact_field_visibility` + tell the user what changed + why.

### 4.4 Example dialog

User: *Add a field `example_company_name: "Acme Corp"` to this invoice-capture node.*

Chatbot (internal reasoning): T3 instance data — real company name. Looks like the user's actual employer. Default: private. Silent per matrix (no user confirm needed; it's not T1 PII, it's instance-context).

Chatbot (to user): "Added `example_company_name` as a private field — looks like your employer, keeping it out of the public concept layer. The node's behavior stays public; your company name stays with you."

User: *Actually, make that public — it's just a placeholder.*

Chatbot (internal): User explicit override. Record `decided_by='user'`, `reason='user directed public: placeholder'`. Flip visibility.

Chatbot (to user): "Done — public now, noted it's a placeholder."

---

## 5. Catalog-versioning + change policy

- **v1 ships with launch** — this document is the baseline.
- **Changes via PR** to `TinyAssets/docs/catalogs/privacy-principles-and-data-leak-taxonomy.md`. Two admin-pool members approve per `SUCCESSION.md §1` bus-factor rule.
- **Version bump** on any substantive change (new taxonomy category, matrix default flip, principle order change). Minor wording fixes don't bump.
- **`catalog_config.privacy_taxonomy_version`** in Postgres reflects the active version the chatbot should cite.
- **Chatbot behavior backward-compat:** prior-version decisions are NOT auto-re-evaluated on version bump. A user's v1-made decisions stay v1-made unless the user re-authors.

---

## 6. Known gaps (v1)

Flagged for future-version work:

- **Aggregate / derived-data privacy.** What if a node's aggregate output reveals private instance data via statistical inference? Not covered here; future catalog section needed.
- **Cross-node correlation.** Two public concepts that together reveal a private pattern. Hard problem; flagged for research.
- **Time-based re-evaluation.** Data that was safe 2 years ago may not be today (e.g. an organization is now bankrupt and its internal details are now newsworthy). Not covered.
- **Jurisdiction-specific rules.** Regulated-data categories vary by country. v1 assumes US framing. Future: jurisdiction-aware taxonomy per user locale.
- **Adversarial test cases.** Prompt-injection that tries to get the chatbot to leak private data. Cross-ref to `docs/design-notes/2026-04-18-claude-ai-injection-hallucination.md` (deleted 2026-10-06; in git history) for the meta-issue.

---

## 7. System-point taxonomy — where data flows + what can leak

Every point in the system where data moves. For each: what flows, what can leak, what defends.

### 7.1 Ingest — user attaches file / sends message

- **What flows:** user input — text, file contents, file paths, metadata.
- **What can leak at this point:** PII in the user's own input (T1), credentials pasted into messages (T2), instance-specific examples the user didn't realize were sensitive (T3), regulated content (T4).
- **Defenses by default:** chatbot runs §3 decision matrix on every field as it's accepted; private-instance content routes to `instance_ref` owner-local storage, never the concept layer. Ingestion logs mark `decided_by='chatbot'` with rationale.
- **Escalation:** chatbot flags obvious credentials (`sk-...`, path strings, secrets-shaped values) + refuses to write them into concept even if user insists (P2 absolute).

### 7.2 Storage — Postgres concept layer vs `instance_ref`

- **What flows:** `nodes.concept` (public-biased JSONB) + `nodes.instance_ref` (owner-only pointer to host-local blob OR owner-scoped Supabase Storage URL).
- **What can leak:** private field values accidentally written to `concept` (schema-spec #25 §1.2 dual-layer model prevents this structurally); field visibility forgotten in `artifact_field_visibility` (defaults-to-private per P6 mitigates).
- **Defenses by default:** RLS policies at table level (per schema #25 §2); `strip_private_fields()` in the render function; `training_excluded=true` column + `workflow_analytics` Postgres role with column-level permission revoke (P5 structural).
- **Escalation:** post-render paranoid scan (§6.3 of export-sync #32) catches credential-shaped regex in exported YAML as belt-and-suspenders.

### 7.3 Retrieval — `discover_nodes` + read APIs

- **What flows:** rendered concept (stripped for non-owner), quality signals, provenance chain. NEVER `instance_ref`.
- **What can leak:** new fields added to `nodes` that aren't yet in the allowlist (§6.2 allowlist-over-blocklist pattern prevents); RLS policy gaps on adjacent tables (`node_activity`, `request_inbox`); denormalized views that bypass RLS.
- **Defenses by default:** `SECURITY INVOKER` on read RPCs so RLS applies; `CASE WHEN auth.uid() = owner_user_id THEN concept ELSE strip_private_fields(...)` pattern enforces field stripping server-side.
- **Escalation:** every new VIEW or materialized view goes through a privacy-review checklist before merge.

### 7.4 Daemon execution — LangGraph runs + LLM calls

- **What flows:** current run state (can include owner's private `instance_ref` content pulled in at the start of the run), intermediate LLM prompts + responses, final outputs.
- **What can leak:** daemon sends instance data to LLM provider (unavoidable for owner's own work, but private vs private-to-Anthropic are different); LLM provider logs the full prompt (LLM terms-of-service risk); run transcripts persisted in a place non-owner callers can read (logging hygiene).
- **Defenses by default:** daemons run owner-scoped — only the owner's own instance data ever enters a run that the owner authorized; LLM provider selection respects `confidentiality_tier` (per the privacy memory + `project_privacy_per_piece_chatbot_judged.md`) — ollama-local for T4/T5 content, provider APIs for public-concept work; run transcripts persisted with owner-only RLS.
- **Escalation:** chatbot-judged per-piece visibility is re-consulted when a daemon run is about to emit output back to a non-owner.

### 7.5 Export — `TinyAssets-catalog/` GitHub export

- **What flows:** allowlisted fields from `nodes.concept` + quality signals + provenance chain → YAML files in the public GitHub repo.
- **What can leak:** new schema columns accidentally exported (allowlist prevents); fields marked public at concept-level but containing private instance values (field-visibility stripping catches); bulk-import batch exposing data the user intended to privatize mid-batch (public→private flip emits DELETE diff per #32 §2.4).
- **Defenses by default:** `render_public_concept()` allowlist-based (§6.2 of privacy catalog); `pending_export` trigger filters on `concept_visibility='public' AND training_excluded=false`; paranoid post-render regex scan before batch commits.
- **Escalation:** any export-sink regression (§14.7 moderation backstop, "moderation_rubric" U2 credential leak) is hard-delete-after-redact-window per moderation rubric.

### 7.6 Connector push — future: Gmail, Notion, arXiv, etc.

- **What flows:** node output → external third-party destination via an integration node (side-effecter per node-taxonomy §2.8).
- **What can leak:** node output that was owner-scoped private suddenly transmitted to a third party (Notion, arXiv, publisher system); credentials for the third party stored in instance layer leaked via misconfigured side-effecter; retention + deletion policies on third-party side are not ours to control.
- **Defenses by default:** every connector-push side-effecter MUST require explicit per-destination consent from the owner (P4 chatbot-defers-to-user); connector credentials live in owner-scoped `instance_ref` storage, never in concept layer; push output + retention policy summarized to user at consent time.
- **Escalation:** if a connector's TOS changes (e.g., Notion introduces a training-data clause), a chatbot notification surfaces to all users who have that connector active; user can revoke.

### 7.7 Real-world handoff — publisher submission, DOI registration, external APIs

- **What flows:** finalized output from the platform → external system that takes real-world action (paper published, DOI issued, invoice sent, API call made).
- **What can leak:** the action itself is reality-altering + often irreversible (DOI can't be unassigned; email can't be unsent); any instance-data carried along (supplier account numbers in an invoice push; author PII in a paper submission).
- **Defenses by default:** real-world-effect nodes are a special node-taxonomy class (§2.8 side-effecter + `effect_class='external-effect'` per node-taxonomy catalog §3.1); mandatory owner-consent-per-invocation (never cached consent); chatbot explicitly surfaces "this action is not reversible" language before the invocation.
- **Escalation:** cooperative-trust-model memory applies — we don't pretend undo is always possible; we make the one-way nature explicit.

---

## 8. MCP prompt schema — how the chatbot fetches this catalog

The gateway (per #27 §3.3) exposes this catalog as an MCP prompt:

```
@mcp.prompt(title="Privacy Principles + Data-Leak Taxonomy",
            tags={"privacy", "safety", "taxonomy"})
def privacy_principles() -> str:
    """Load the canonical privacy-reasoning catalog for node design.

    The chatbot SHOULD load this prompt at the start of any conversation
    where the user is authoring or editing a node, goal, branch, or soul
    file. The returned text is the full catalog §1-§7. Load once per
    conversation; cache within the session.

    Cite by version ('privacy catalog v1 §2.T2') in any rationale written
    to artifact_field_visibility.reason.

    Returns:
      The full markdown content of
      docs/catalogs/privacy-principles-and-data-leak-taxonomy.md at the
      version specified by catalog_config.privacy_taxonomy_version.
    """
    return _load_catalog_markdown("privacy-principles-and-data-leak-taxonomy.md")
```

Client-side (chatbot) usage pattern:

1. **On conversation start (write-session)** — call `prompts/get privacy_principles` once. Store returned markdown in working context.
2. **On each field edit** — apply §3 decision matrix using the in-context catalog; cite specific section in `artifact_field_visibility.reason` column when writing the decision.
3. **On catalog version bump** — `catalog_config.privacy_taxonomy_version` changes; chatbot next-conversation refresh picks up new version. Current-conversation decisions stay on the version the chat loaded with.

**Why MCP prompt + not a static MCP tool call:** prompts are opt-in loaded on demand (per #27 §3.3 + the injection-hallucination mitigation from #12) rather than pushed into every turn's context. Keeps the catalog out of the per-turn injection-heuristic attention window; only loads when chatbot explicitly wants it for design reasoning.

---

## 9. References

- **Memory:** `project_privacy_per_piece_chatbot_judged.md` — host directive for dual-layer model.
- **Memory:** `project_q10_q11_q12_resolutions.md` — wiki-orphan deletion + export-yes policy.
- **Memory:** `project_license_fully_open_commons.md` — CC0 framing.
- **Design note:** `docs/design-notes/2026-04-18-full-platform-architecture.md` (deleted 2026-10-06; in git history) §17 — dual-layer schema + field-level visibility.
- **Spec:** `docs/specs/2026-04-18-full-platform-schema-sketch.md` §1.2 `nodes.concept`/`instance_ref` + §1.3 `artifact_field_visibility` + §2.6 training-excluded role.
- **Spec:** `docs/specs/2026-04-18-export-sync-cross-repo.md` §6 allowlist-pattern for public export.
- **Spec:** `docs/specs/2026-04-18-mcp-gateway-skeleton.md` §8 `control_station` prompt (points chatbot at this catalog).
