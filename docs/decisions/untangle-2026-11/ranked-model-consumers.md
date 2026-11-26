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
