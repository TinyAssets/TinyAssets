/**
 * The owner's agent trees, as the owner-object spec draws them: the owner's own authority
 * alone changes the agent definitions, the abilities' trees, the goals, the profile and the
 * model ranking (`OWNER_TREES`), while an outside client's sandbox holds only the definitions
 * and the abilities' trees read-only (`CLIENT_READ_ONLY`), its always-read set: a client under
 * a full grant may write the goals, the profile and the ranking.
 */
import { describe, expect, it } from "vitest";

import {
  ABILITY_TREES, AGENTS_DIR, CLIENT_READ_ONLY, GOALS_DIR, OWNER_TREES, PROFILE, RANKING,
} from "../src/paths";

describe("the owner's agent trees", () => {
  it("hold a client's sandbox read-only to the definitions and the abilities' trees alone",
     () => {
    expect(CLIENT_READ_ONLY).toEqual([AGENTS_DIR, ...ABILITY_TREES]);
    expect(CLIENT_READ_ONLY).not.toContain(GOALS_DIR);
    expect(CLIENT_READ_ONLY).not.toContain(PROFILE);
    expect(CLIENT_READ_ONLY).not.toContain(RANKING);
  });

  it("leave the goals, the profile and the ranking, beside those, to the owner's own " +
     "authority", () => {
    expect(OWNER_TREES).toEqual([...CLIENT_READ_ONLY, GOALS_DIR, PROFILE, RANKING]);
    expect(OWNER_TREES).toEqual(["/workspace/agents", "/workspace/.agents", "/workspace/skills",
                                 "/workspace/prompts", "/workspace/goals", "/workspace/USER.md",
                                 "/workspace/model-ranking.json"]);
  });
});
