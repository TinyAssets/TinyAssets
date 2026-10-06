## Decisions

An explicit node pin is a constraint. A caller without a policy entrypoint must
refuse it with the requested model and source; an automatic order must not append
unrequested models after an exact model pin. A provider-only pin retains all
same-source eligible models in the captured order. Explicit fallback chains
remain explicit.

Persist advisory native catalogue snapshots in platform SQLite, keyed by owner,
command center and provider. Store model metadata, observation times and a custody
digest, never credentials. Hydrate only against the current custody reference;
execution still performs fresh discovery. A late old refresh cannot overwrite a
new custody's snapshot. Cold display reads stay nonblocking and queue discovery.

An owner-confirmed connect request with a model use already authorizes its exact
model list and free-only limits. Add that membership through the existing binding
authority, retaining every prior member and the root source. Registration alone
and agent-authored configuration still grant nothing. Preserve the already-powered
response only after successful acceptance, without changing the root model.
Legacy powered setups without a manifest digest, or setups without exactly one
owned serving agent, fail with `model_source_acceptance_failed` and leave the
request pending. Tell the owner to confirm explicit model access for the existing
source and ensure exactly one owned agent is serving before retrying. This is
intentional fail-loudly behavior: a saved credential is not accepted model access.

If activation fails after membership publication, restore the previous setup
through the same binding APIs with the published digest, binding revision and
last-update timestamp as fences. Serving transitions do not increment revision,
so the timestamp is also checked inside exclusive admission on both bind and
enable. Never overwrite an intervening owner edit. If recovery cannot complete,
report the disabled state explicitly. Acceptance failures remain pending requests
with a structured error; deposited credentials alone are not reported as accepted.

## Verification

Regressions must fail before implementation: policy-incapable caller refusal,
pinned candidate exhaustion without substitution, catalogue survival across cache
instances, and a second confirmed HTTP source accepted alongside the first.
Exercise actual owner-bound cross-family dispatch, custody replacement and owner
isolation. Run affected Windows tests, Linux 3.11 oracle, ruff and hygiene; obtain
one cross-family floor review before pushing. No PR or deployment in this lane.
