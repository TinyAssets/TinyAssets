/**
 * The most nanoseconds one sync-ignore automaton step (a byte of text, a 32-state word) may
 * take in the sandbox: the constant of `AUTOMATON_STATES`'s derivation
 * (`contract/src/sync-ignore.ts`, the matcher), measured there by the template build's
 * standing check (`stepNanos`), which fails the build past it. It is here, alone, because
 * `sandbox/template/build.py` reads it by its text at this path; fix A6 moves the build's
 * reads to the runner bundle's own facts, and it joins the matcher.
 */
export const STEP_NS = 6;
