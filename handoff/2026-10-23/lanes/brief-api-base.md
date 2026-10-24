LANE: discovery finds a provider's API base generically. Worktree C:\Users\Jonathan\Projects\wf-api-base, branch
feat/discovery-api-base (from main eb582d82d08). Small.
Discovery (tinyassets/connection_oauth/model_discovery.py, runs in the owner's box) dropped the provider-named
"/openai/v1" probe (Direction 2: no provider-specific code), so typing a bare provider domain whose API lives under a
vendor-specific path no longer works. Make it generic: read what the provider publishes (an OpenAPI/servers document,
RFC 9728 protected-resource metadata, a well-known API catalog such as RFC 9727 /.well-known/api-catalog, or links on
the domain's root/docs page if machine-readable) to learn the model API base, then probe that. No vendor names or paths
in code. Keep the existing probe budget and egress path. Tests with offline fixtures for each discovery source. Open a
draft PR.
