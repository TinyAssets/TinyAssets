Both branches pushed; worktrees clean. Six separate commits, each with a failing-first check and required trailers.

- **conv/router-503-schema-drift** — `39d1a65f9b656917cb62bd4cc4356d32ecb378ca`
  Fixed R1–R4: preserve box responses, omit unsafe retry hints, document session signout, preserve original rollback errors.
  Linux oracle: router, unavailable-response, and schema tests — **76 passed**.

- **conv/turn-reply-budget** — `c34ec9179e757ccbb3708c154681233fd9191695`
  Fixed B1–B2: retain final text without executing tools; consider eligible sources, failure limits, and deadlines.
  Linux oracle: reply-reserve tests — **11 passed**.

Ruff passed on both branches. Nothing left undone within scope; no production access or deployment.