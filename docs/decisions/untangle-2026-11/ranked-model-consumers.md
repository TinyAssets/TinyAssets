# Model names shared by automatic selection and the route probe

The older Codex ranking change (134a79aa) predates tiered and owner-defined
rankings, sign-in plans, vendor scores and the route probe in #5852. Current
main d662e63e matches entries on either a full model ID or its last segment,
then compares full IDs. A provider prefix can outweigh a model version or a
connection's age. Two ordinary Owner tests exercise actual pinned catalog IDs
in both input orders. The newer routed Fable loses to the older native one,
and a newer routed GPT connection beats the older native connection.

- [ ] Reconcile the old change at the current shared ranking. Review the
  spelling that matched for both Owner selection and the sandbox route probe,
  including patterns that match only the full ID. Keep the selected model's
  original ID for dispatch. Preserve tier, plan, eligibility and unknown-model
  ordering. Check each shipped route against the pinned catalog before release.

On 77ac0b46 the complete ordinary Owner ranking file passed all eight existing
cases and failed only the two new ones. Full Node 24 platform TypeScript
passed. The first test invocation stopped before collection because nested
workspace dependencies were missing. Both outcomes also reproduced with
the installed catalog on Node 22 and 24 in both connection orders.

The shipped offline probe plan has 12 selections. The old matched-name rule
kept 11. For pi on OpenRouter's Messages API it would change the selected
ID from anthropic/claude-fable-5.2 to ~anthropic/claude-fable-latest. Both
IDs exist in pi-ai 1.6.0, the template's pinned version. The catalog does
not identify the revision served by the alias. For each of low and high
effort on Node 22 and 24, the actual adapter built the versioned request
with two extra beta flags and a per-message effort setting. The alias
request omitted those flags and that setting. The capture stopped before
transport. No vendor request or template acceptance was run.

- [ ] Decide whether the platform ranking should put versioned Fable 5.2
  ahead of the moving alias. Keep that preference in the shared ranking
  data. Do not guess the upstream revision or special-case the probe.
  Recheck all route choices, including custom full-ID-only patterns,
  together with Owner selection before integrating a runtime fix.
