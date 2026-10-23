## Context
Lane WX converges declared CLI sources and detected HTTP sources into SourceRecord, a small standard-library dataclass and JSON schema. The existing branch already includes both lanes.

## Decisions
One record contains identity, address aliases, data-only suggested/free labels, one sign-in mechanism (OAuth, CLI login, API key), one invocation mechanism (HTTP or CLI), exact egress hosts, and broker or owner-box state. Installed and owner-edited declarations use the same schema. Resolve exact address aliases before discovery; never dispatch by provider name or host. OAuth metadata is frozen before authorization. Existing PKCE key acquisition is an OAuth protocol declared in record data; its working exchange adapter remains import compatible for live pending flows.

The connect dialog has one address and Connect. The sole suggested free record supplies the initial address. No source lists or categories. Missing sign-in offers a key field and help link. Preserve browser same-tab authorization, native sheets and desktop external return. CLI login persists its opaque flow reference across navigation and resumes polling in the owner box. Credentials remain in their existing custody; owner-authored legacy CLI declarations must adopt the new record schema.

## Verification and delivery
Targeted detector, fake-provider exchange, real owner-box login and model selection, browser tests, ruff and heavy-file checks only. No production actions. No sub-agents. Floor changes require independent review by the integration owner before deployment. Report runtime-role copy manifests and import dependencies. Merge the subscription branch again before completion.

The former acquisition, free-source and discovery-preset data now live in source records. Compatibility functions project those records for existing live consumers and pending flows; they are not separate source catalogues. See `hotfix-files.md` for role copies, retired paths and validation evidence.
