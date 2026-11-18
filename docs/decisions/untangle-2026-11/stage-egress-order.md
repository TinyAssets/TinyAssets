# Stage egress verification order as built (2026-11-18)

Step 7 asked for a successful model call on the dark deployment. Decision d3 §H attaches
the real `egress.tinyassets.io` at the stage's `routes`. The runner and probes embed that
hostname so the same pair goes to production. The dark control record has no admitted
stage owner until the coordinator configures one.

On `f934871ad` and the combined tree `c78a8d858`, the existing synthetic deploy harness
exercised the actual dark and routes sequences. After a fresh dark apply the fixture showed
zero attached egress domains and the legacy MCP route. After routes it showed the egress
domain and an enabled lifecycle hook. A further dark apply with the clock token preserved
all nine established routes and the enabled hook. These were local fixture runs.

Step 7 keeps the live pair and dark resource checks. Step 8 exercises an admitted run's
network and lease, the E2 policy update and model call on its changed network, and R5's
real engine hooks once their implementation exists. P-import's read-only census uses its
own authorized data. Keep the pre-write empty-bucket observation where specified. Use the
same runner and egress host throughout. The paid template, stage admission and live calls
remain coordinator operations.
