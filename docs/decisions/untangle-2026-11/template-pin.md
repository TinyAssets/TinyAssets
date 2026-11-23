# template-pin (2026-11-21)

The sandbox image moved after the last pin (`s85528cdd2cd74efa`): #5745 dropped Kimi's install
and lock from the recipe, so `image.ts`'s tag no longer matched the pin and `apply` deploys were
refused. One `sandbox-template.yml` build on main at f1a539703 (run 44938392431, part of the
founder-approved dark redeploy) built `tinyassets-sandbox:s8661fc8113e20662` and passed every
standing check on throwaway sandboxes: the three sign-ins (claude-code, codex, gemini), the eleven
keyed routes (two turns each, the second resumed, no outside connects) and the matcher step
(4.46 ns, ceiling 6). Its pair is `s8661fc8113e20662-rcde54e5309ef50f5-p8cc7078f4f6dcaa5`.

This change is the run's own record: `passed.json` as the run uploaded it (artifact
`passed-json`) and the pin in `sandbox/wrangler.jsonc`. Nothing else changes.

- [ ] live: the dark redeploy runs its pair check on this pin.
