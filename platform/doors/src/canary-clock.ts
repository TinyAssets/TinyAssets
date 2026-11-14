/**
 * The platform canary's clock (d3 §J2): the doors' Cron Trigger (`triggers.crons`, every ten
 * minutes) starts the canary's workflow, which checks the platform from outside on GitHub's
 * runners (`deploy/canary.ts`): a Worker's fetch of its own zone's routes never reaches them.
 * Its three secrets are optional (`contract/canary.json`), and each tick does one of:
 *
 * - **No dispatch token** (`PLATFORM_CANARY_TOKEN`): nothing is sent, and one line says so.
 *   This is the clock until the founder mints the token and a deploy sets it, so a schedule
 *   the doors hold before then reaches nothing.
 * - **The dispatch answered `204`:** done.
 * - **Any other answer, or none** (GitHub down, the token expired or revoked): the failure is
 *   logged and the tick fails, so the Worker's own logs hold it. On the ticks of each hour's
 *   first ten minutes and of its second half's (`:00` and `:30` at this schedule) the founder
 *   is paged directly, when the pager's keys are set: GitHub can open no issue then, and a
 *   page every tick would wake the founder six times an hour for one dead token.
 */
import { DISPATCH, PAGER, dispatchRequest, pageForm } from "../../contract/src/canary";

import { namedSecret } from "./config";
import type { DoorsEnv } from "./env";

/** Whether the tick at `scheduledTime` pages a failed dispatch: twice an hour. */
function pages(scheduledTime: number): boolean {
  return new Date(scheduledTime).getUTCMinutes() % 30 < 10;
}

export async function canaryClock(env: DoorsEnv, scheduledTime: number): Promise<void> {
  const token = namedSecret(env, DISPATCH.token);
  if (token === null) {
    console.log(`the canary's clock has no ${DISPATCH.token}: nothing is dispatched`);
    return;
  }
  let failed: string;
  try {
    const answer = await fetch(dispatchRequest(token));
    if (answer.status === 204) {
      return;
    }
    failed = `GitHub answered the dispatch of ${DISPATCH.workflow} ${answer.status}`;
  } catch (error) {
    failed = `the dispatch of ${DISPATCH.workflow} reached no answer: ${(error as Error).message}`;
  }
  const user = namedSecret(env, PAGER.user);
  const key = namedSecret(env, PAGER.token);
  if (pages(scheduledTime) && user !== null && key !== null) {
    const page = pageForm({ user, token: key }, {
      title: "TinyAssets: the platform canary is not running", urgent: false,
      message: `${failed}. No canary run checks the platform until it is fixed: a 401 or 403 ` +
               `is the token (renew ${DISPATCH.token}), anything else GitHub.` });
    const paged = await fetch(PAGER.url, page).then((answer) => String(answer.status),
                                                    (error: Error) => error.message);
    if (paged !== "200") {
      failed += `; the page failed too (${paged})`;
    }
  }
  throw new Error(failed);
}
