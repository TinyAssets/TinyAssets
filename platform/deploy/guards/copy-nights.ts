/**
 * A fake of what GitHub holds of the off-account copy's nights (`offsite.ts` `nights`), for
 * every test that reads them: each night's run made at `at`, its settings job done a minute
 * later and its copy job twenty minutes later.
 */
import { expect } from "vitest";

import { DISPATCH } from "../../contract/src/canary";
import { NIGHTLY } from "../offsite";

/** One completed night of the copy: when its run was made, and its two jobs' conclusions. */
export interface Night {
  readonly at: string;
  readonly settings: "success" | "failure";
  readonly copy: "success" | "failure" | "skipped";
}

export const night = (at: string, copy: Night["copy"],
                      settings: Night["settings"] = "success"): Night => ({ at, settings, copy });

/** `minutes` after `at`. */
const later = (at: string, minutes: number) =>
  new Date(Date.parse(at) + minutes * 60_000).toISOString().replace(".000Z", "Z");

/** GitHub's answer to `url` over `nights`, newest first, or null for a URL not about them. */
export function nightsAnswer(url: string, nights: readonly Night[]): Response | null {
  const repository = `https://api.github.com/repos/${DISPATCH.repository}`;
  const at = new URL(url);
  if (url.startsWith(`${repository}/actions/workflows/${NIGHTLY.workflow}/runs?`)) {
    expect([at.searchParams.get("branch"), at.searchParams.get("status")])
      .toEqual([NIGHTLY.ref, "completed"]);
    return Response.json({ workflow_runs: at.searchParams.get("page") !== "1" ? []
      : nights.map((each, id) => ({ id, created_at: each.at, updated_at: later(each.at, 20) })) });
  }
  const jobs = /^\/actions\/runs\/(\d+)\/jobs$/.exec(url.slice(repository.length));
  if (!url.startsWith(repository) || jobs === null) {
    return null;
  }
  const each = nights[Number(jobs[1])]!;
  return Response.json({ jobs: [
    { name: NIGHTLY.settings, conclusion: each.settings, completed_at: later(each.at, 1) },
    { name: NIGHTLY.copy, conclusion: each.copy, completed_at: later(each.at, 20) }] });
}
