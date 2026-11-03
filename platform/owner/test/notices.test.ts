/**
 * The owner's notices (`notices.ts`): what the owner's work reports that no call fails with,
 * kept bounded, newest first, and read by the owner alone (slice D review M3).
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { WriteGate } from "../src/gate";
import { NOTICE_DETAIL, Notices, NOTICES_KEPT } from "../src/notices";
import type { OwnerDO } from "../src/owner-do";
import { MUSE, rig } from "./ledger-support";
import { failure } from "./support";

describe("the owner's notices", () => {
  it("keep only the newest NOTICES_KEPT, newest first, each detail cut", async () => {
    const r = await rig();
    const kept = await runInDurableObject(r.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const notices = new Notices(gate.storage);
      for (let i = 0; i < NOTICES_KEPT + 5; i++) {
        await gate.write(() => notices.add("probe", `${i} ${"x".repeat(NOTICE_DETAIL)}`, i));
      }
      return notices.list();
    });
    expect(NOTICES_KEPT).toBe(1000);
    expect(kept).toHaveLength(NOTICES_KEPT);
    expect(kept[0]!.detail.startsWith(`${NOTICES_KEPT + 4} `)).toBe(true);
    expect(kept.at(-1)!.detail.startsWith("5 ")).toBe(true);
    expect([...kept[0]!.detail].length).toBe(NOTICE_DETAIL);
  }, 30_000);

  it("are read by the owner alone", async () => {
    const r = await rig();
    expect((await r.stub.noticeList(r.bind(null))).notices).toEqual([]);
    expect((await failure(() => r.stub.noticeList(r.bind(MUSE)))).message)
      .toBe("only the owner may read its notices");
  }, 30_000);
});
