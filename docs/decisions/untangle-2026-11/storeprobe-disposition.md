# Store-probe deployment readings and retirement

Main `f934871ad` and the combined rehearsal `896bf64f3` carry the same store-probe workflow, scripts and platform lock. The October 30 readings remain historical evidence for the per-thread store. ADR-034 §13 and #5389 removed that store and pi from the owner. Later, #5426 explicitly retained this probe because it owed a run. Its 14 configured cases cover P1–P4 and P7. None exercises either outstanding question in the record, P5 (store reset mid-run) or P8 (billed bytes after populated-store deletion).

## What the deploy step can distinguish

Five offline runs on each tree used the workflow's original deploy step, fake config and deletion commands, and a fake Wrangler. Each experiment isolated the output for one copy. All ten steps exited zero.

| Simulated CLI result | Copy | Written status | Step output |
| --- | --- | --- | --- |
| Exit 7, empty diagnostic | main | refused | deployed=false |
| Exit 1, authentication error | main | refused | deployed=false |
| Exit 143 | main | refused | deployed=false |
| Exit 7, empty diagnostic | raised | refused | deployed=true |
| Exit 0, no deployment performed | main | accepted | deployed=true |

The step turns any nonzero result into a production refusal. For a raised copy, the driver skips its experiment and publishes that alleged refusal as the P1c reading. In the other direction, any exit zero adds the copy to the door bindings. The fixture supplied exit codes deliberately. It did not contact Cloudflare or measure how often those outcomes occur. The driver's run-ID filter rejects samples from another run. That filter identifies readings, but cannot identify which Worker version handled them. A missing raised disposition still permits a raised reading attempt.

## Which question the current tree can measure

The locked Wrangler 4.154.0 failed the original local entry point at its first owner dry-run. The lock lacks the two installed packages for its three unresolved imports (`@earendil-works/chord/context`, `@earendil-works/pi-durable`, and `@earendil-works/pi-durable/storage/sqlite/cloudflare`). A second, independent obstacle is visible in the original local config check: it requires the probe's subrequest limit of 10,000 to equal today's owner's 10,000,000. The date, flags and workers_dev setting do match. The first is the original P1c control, and changing it would change the experiment. Local verification also bundles the door, then passes its source file to Miniflare. Only the owner copies use emitted bundle bytes.

Leave the October result intact, including the million-subrequest acceptance on the raised copy. Hold further dispatch and retirement pending a disposition of P5 and P8 against the current owner erase door and externally run sessions. The current stage still owes acceptance of the ten-million setting. For any surviving experiment, distinguish an explicit provider refusal of the attempted configuration from an interruption, authentication error or unknown outcome. Correlate an affirmative provider acknowledgement with the intended script version and configuration, and exercise the emitted bundles during the local check. These are gaps in the evidence available today.

## Legacy route deployer

The separate legacy workflow installs `wrangler@latest` in both jobs. The dry-run lists its output, the deploy job invokes Wrangler, then an MCP edge preflight tests the serving route. A healthy older deployment could answer the preflight. No resolved legacy CLI version or live deployment was measured here. R1 already calls for deleting this workflow at route handover to retire its ability to rewrite routes. The proxy service's rollback lifetime belongs to the separate cutover hold.
