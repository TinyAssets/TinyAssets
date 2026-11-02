/**
 * The runs' rows' ids and bounds (`runs.ts`, migration 12), in a module of their own so the
 * schema reads them without the runs' code.
 *
 * Each bound is a technical ceiling of the owner's database (its 10 GB, of which the
 * owner-bound guard holds the worst case under half), recorded in `limits-audit.md`:
 * - `RUNS_KEPT`: settled runs kept as receipts, the newest first; an older run's status is
 *   gone, never its effects (its transcript is the engine's own file in the owner's files);
 * - `RESULT_BYTES`: a run's result text as its receipt keeps it; a longer one is kept to
 *   this many bytes and marked cut (`result_cut`), the whole in the engine's transcript;
 * - `ERROR_BYTES`: a run's first error, as its receipt keeps it, cut the same way;
 * - `CONVERSATIONS_KEPT`: conversations kept, the least recently run one with no live run
 *   forgotten first (its transcript stays in the owner's files; only its resume id goes);
 * - `SCHEDULES_KEPT`: the `runs` lane's schedules, past which a new one is refused with
 *   its way out (cancel one);
 * - `INPUT_BYTES`: a schedule's stored input. A door's input is never stored: it goes to
 *   the sandbox with the run's start, bounded by the door's own body bound.
 */

/** `run_` and 32 hex. */
export const RUN_ID_CHARS = 36;
/** `cv_` and 32 hex. */
export const CONVERSATION_ID_CHARS = 35;
/** `sch_` and 32 hex. */
export const SCHEDULE_ID_CHARS = 36;
export const RUN_ID = /^run_[0-9a-f]{32}$/;
export const CONVERSATION_ID = /^cv_[0-9a-f]{32}$/;
export const SCHEDULE_ID = /^sch_[0-9a-f]{32}$/;

export const RUNS_KEPT = 2000;
export const RESULT_BYTES = 16384;
export const ERROR_BYTES = 1024;
export const CONVERSATIONS_KEPT = 10000;
export const SCHEDULES_KEPT = 1000;
export const INPUT_BYTES = 16384;
