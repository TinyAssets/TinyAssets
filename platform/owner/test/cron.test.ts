/**
 * Cron recurrence (`cron.ts`; tasks 5, F1), carried over from the deleted scheduled-launch
 * tests (slice D review H1): the parse and its bounds, and `nextOccurrence`, strictly after
 * the time, by property tests against a minute-by-minute scan. The runs slice (R) schedules
 * a run by it.
 */
import { describe, expect, it } from "vitest";

import {
  CRON_HORIZON_DAYS, CRON_MAX_CHARS, CRON_MAX_ITEMS, CRON_MIN_GAP_MIN, nextOccurrence, parseCron,
} from "../src/cron";

const DAY_S = 86_400;

describe("cron recurrence", () => {
  /** A deterministic generator (mulberry32), so a failure names its seed. */
  function random(seed: number): () => number {
    let state = seed >>> 0;
    return () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 2 ** 32;
    };
  }
  const pick = <T>(next: () => number, items: readonly T[]): T =>
    items[Math.floor(next() * items.length)]!;

  /** Whether `value` matches one field's text, by arithmetic, apart from `cron.ts`. */
  function fieldMatches(text: string, value: number, low: number, high: number): boolean {
    return text.split(",").some((item) => {
      const [base, step] = item.split("/") as [string, string | undefined];
      const by = step === undefined ? 1 : Number(step);
      const [from, to] = base === "*" ? [low, high]
        : base.includes("-") ? base.split("-").map(Number) as [number, number]
          : [Number(base), step === undefined ? Number(base) : high];
      return value >= from && value <= to && (value - from) % by === 0;
    });
  }

  /** Whether the minute starting at `t` (seconds) matches `expression`, in UTC. */
  function matches(expression: string, t: number): boolean {
    const [minute, hour, day, month, weekday] = expression.split(" ") as
      [string, string, string, string, string];
    const date = new Date(t * 1000);
    const dayOk = fieldMatches(day, date.getUTCDate(), 1, 31);
    const weekdayOk = fieldMatches(weekday, date.getUTCDay(), 0, 6);
    const days = day === "*" && weekday === "*" ? true : day === "*" ? weekdayOk
      : weekday === "*" ? dayOk : dayOk || weekdayOk;
    return t % 60 === 0 && fieldMatches(minute, date.getUTCMinutes(), 0, 59)
      && fieldMatches(hour, date.getUTCHours(), 0, 23)
      && fieldMatches(month, date.getUTCMonth() + 1, 1, 12) && days;
  }

  it("answers the first matching minute strictly after a time, for random expressions " +
     "(property tests against a minute-by-minute scan)", () => {
    const minutes = ["0", "*/15", "5,35", "0-30/15", "7", "*/20", "45", "5/20"];
    const hours = ["*", "*/2", "9-17", "3", "1,13", "0-23/6", "22"];
    const days = ["*", "*", "1", "15", "1-7", "*/10", "28-31"];
    const months = ["*", "*", "1", "*/3", "6-8", "2"];
    const weekdays = ["*", "*", "1-5", "0", "6", "2,4"];
    let checked = 0;
    for (let seed = 1; seed <= 400; seed++) {
      const next = random(seed);
      const expression = [pick(next, minutes), pick(next, hours), pick(next, days),
                          pick(next, months), pick(next, weekdays)].join(" ");
      const cron = parseCron(expression);
      const after = 1_700_000_000 + Math.floor(next() * 3 * 365 * DAY_S) + Math.floor(next() * 60);
      const found = nextOccurrence(cron, after);
      expect(found, `${expression} after ${after}`).not.toBeNull();
      expect(found! > after && matches(expression, found!), `${expression} after ${after}`)
        .toBe(true);
      // Nothing matches between: scanned minute by minute while the gap is short.
      if (found! - after <= 3 * DAY_S) {
        for (let t = Math.floor(after / 60) * 60 + 60; t < found!; t += 60) {
          expect(matches(expression, t), `${expression}: ${t} before ${found}`).toBe(false);
        }
        checked++;
      }
      // From the occurrence itself, the next is a later one.
      expect(nextOccurrence(cron, found!)! > found!).toBe(true);
    }
    expect(checked).toBeGreaterThan(100);
  });

  it("answers the first matching minute at or after the next whole minute, strictly after " +
     "the time", () => {
    const hour = Date.UTC(2026, 10, 2, 15) / 1000;
    const hourly = parseCron("0 * * * *");
    expect(nextOccurrence(hourly, hour - 30)).toBe(hour);
    expect(nextOccurrence(hourly, hour - 60)).toBe(hour);
    expect(nextOccurrence(hourly, hour)).toBe(hour + 3600);
    // `a/n` runs from `a` to the field's end.
    const stepped = parseCron("5/20 * * * *");
    expect(stepped.minutes).toEqual([5, 25, 45]);
    expect(nextOccurrence(stepped, hour + 6 * 60)).toBe(hour + 25 * 60);
  });

  it("matches a day by either day field when both are restricted, as classic cron", () => {
    // The 13th of a month, or a Friday: 2026-11-13 is a Friday; 2026-11-06 a Friday too.
    const cron = parseCron("0 9 13 * 5");
    const start = Date.UTC(2026, 10, 1) / 1000;
    expect(nextOccurrence(cron, start)).toBe(Date.UTC(2026, 10, 6, 9) / 1000);
    expect(nextOccurrence(cron, Date.UTC(2026, 10, 6, 9) / 1000))
      .toBe(Date.UTC(2026, 10, 13, 9) / 1000);
    // A leap day four years off is within the horizon.
    expect(nextOccurrence(parseCron("0 0 29 2 *"), Date.UTC(2025, 0, 1) / 1000))
      .toBe(Date.UTC(2028, 1, 29) / 1000);
    expect(CRON_HORIZON_DAYS).toBeGreaterThanOrEqual(8 * 365);
  });

  it.each([
    ["0 0-23 * * *", null],
    ["60 * * * *", "takes 0 to 59"], ["0 24 * * *", "takes 0 to 23"],
    ["0 0 0 * *", "takes 1 to 31"], ["0 0 * 13 *", "takes 1 to 12"],
    ["0 0 * * 7", "takes 0 to 6"], ["0 0 * * MON", "not a value"], ["0 0 * JAN *", "not a value"],
    ["0 0 L * *", "not a value"], ["0 0 ? * *", "not a value"], ["0 0 * * 1#2", "not a value"],
    ["0 0 30 2 *", "an occurrence within"], ["0 0 31 4 *", "an occurrence within"],
    ["0 0 * *", "exactly five fields"], ["0 0 * * * *", "exactly five fields"],
    [`${Array.from({ length: 61 }, (_, n) => n % 60).join(",")} * * * *`, "at most 60 items"],
    [`0 0 * * *${" ".repeat(CRON_MAX_CHARS)}`, "characters"], ["", "characters"],
    ["0 5-1 * * *", "takes 0 to 23"], ["*/0 * * * *", "takes 0 to 59"],
    ["*/61 * * * *", "takes 0 to 59"],
  ])("bounds what it accepts: %s", (expression, refusal) => {
    if (refusal === null) {
      expect(parseCron(expression).hours).toHaveLength(24);
      return;
    }
    expect(() => parseCron(expression)).toThrow(refusal);
  });

  it("holds the bounds as named", () => {
    expect([CRON_MAX_CHARS, CRON_MAX_ITEMS, CRON_MIN_GAP_MIN]).toEqual([256, 60, 1]);
    // Every minute is a schedule: the tier's simultaneous runs bound what it starts.
    expect(parseCron("* * * * *").minutes).toHaveLength(60);
    expect(parseCron("*/5 * * * *").minutes).toHaveLength(12);
    // Every value of a field, listed, is one field's most items.
    expect(parseCron(`${Array.from({ length: 60 }, (_, n) => n).join(",")} * * * *`).minutes)
      .toHaveLength(60);
    expect(parseCron("0 0,23 * * *").hours).toEqual([0, 23]);
  });
});
