/**
 * A metered call's body as the gateway holds it once: the clamp made in place, and the call's
 * weight. That the one parse's keys are spelled only one way (`aliased`) is the contract's
 * guard (`contract/guards/api-rules.test.ts`).
 */
import { describe, expect, it } from "vitest";

import { apiRules, supportedApis } from "../../contract/src/api-rules";
import { valueAtPath } from "../../contract/src/checks";
import type { Meter } from "../../contract/src/egress-scope";
import {
  GATEWAY_POOL_SLOTS, heldDeadlineMs, parseWeight, Tokens, weightSlots,
} from "../src/held";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { clamped } from "../src/meter";
import { UNIT_BYTES } from "../../contract/src/slots";

describe("the clamp in place", () => {
  it("clamps the parse itself, never a copy, on every API", () => {
    for (const api of supportedApis()) {
      const rules = apiRules(api)!;
      const meter: Meter = { method: "POST", path: "/x",
                             model_field: rules.model === null ? null : [rules.model],
                             output_field: rules.outputs[0]! };
      const body = { model: "m", messages: [{ role: "user", content: "hi" }] };
      const after = clamped(meter, body, 10, { provider: "custom", api });
      expect(after, api).toBe(body);
      expect(valueAtPath(after, meter.output_field), api).toBe(10);
    }
  });
});

describe("the gateway's weight of a held call", () => {
  it("counts the structural tokens as the bytes stream in, strings and escapes kept apart",
     () => {
    const counted = (...pieces: string[]) => {
      const tokens = new Tokens();
      for (const piece of pieces) {
        tokens.feed(new TextEncoder().encode(piece));
      }
      return [tokens.count, tokens.ascii];
    };
    expect(counted('{"a":[1,2,{}],"b":"x"}')).toEqual([11, true]);
    // A string's own brackets, commas, colons and escaped quotes are not structure.
    expect(counted('{"a":"{[,:\\"]}"}')).toEqual([4, true]);
    // A chunk boundary inside an escape or a string changes nothing; non-ASCII is seen.
    expect(counted('{"a":"x\\', '",[]', '"}', ',"\u2014":1}')).toEqual(counted(
      '{"a":"x\\",[]"}', ',"\u2014":1}'));
    expect(counted('{"k":"\u2014"}')).toEqual([4, false]);
    expect(counted("[]".repeat(3))).toEqual([3, true]);
  });

  it("weighs a call by its bytes and its tokens, refusing one past the pool", () => {
    expect(weightSlots(0)).toBe(1);
    expect(weightSlots(UNIT_BYTES)).toBe(1);
    expect(weightSlots(UNIT_BYTES + 1)).toBe(2);
    // The ceilings the costs derive: an ASCII body of the door's 16 MiB fits; a 16 MiB body
    // with a non-ASCII character, or a 4 MiB one of empty objects, does not.
    const pool = GATEWAY_POOL_SLOTS * UNIT_BYTES;
    expect(parseWeight(EGRESS_BODY_MAX, 0, true)).toBeLessThanOrEqual(pool);
    expect(parseWeight(15 * 1024 * 1024, 0, false)).toBeLessThanOrEqual(pool);
    expect(parseWeight(EGRESS_BODY_MAX, 0, false)).toBeGreaterThan(pool);
    expect(parseWeight(4 * 1024 * 1024, 4 * 1024 * 1024 * 2 / 3, true)).toBeGreaterThan(pool);
  });

  it("bounds the upload of the door's largest body near the limits audit's 100 s", () => {
    expect(heldDeadlineMs(EGRESS_BODY_MAX)).toBeLessThan(120_000);
  });
});
