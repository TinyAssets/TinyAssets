/**
 * Cron recurrence for scheduled launches (tasks 5, F1): our own small, pure next-occurrence
 * function, since agents exports none (its `schedules/parser` holds only a prompt and a zod
 * schema).
 *
 * An expression is the five classic fields, in UTC: minute (0-59), hour (0-23), day of the
 * month (1-31), month (1-12) and day of the week (0-6, Sunday 0). A field is `*`, a value,
 * a range `a-b`, either with a step (`*\/n`, `a-b/n`, `a/n` from `a` to the field's end), or
 * a list of those, comma-separated. As in classic cron, when both day fields are
 * restricted a day matches if either does. What we accept is bounded, so a recurrence can
 * never be a flood or a loop:
 * - at most `CRON_MAX_CHARS` characters and `CRON_MAX_ITEMS` list items a field, numbers
 *   only (no names, `?`, `L`, `W` or `#`), every value in its field's range;
 * - consecutive occurrences at least `CRON_MIN_GAP_MIN` minutes apart, whatever the days;
 * - at least one occurrence within `CRON_HORIZON_DAYS` (an expression that never matches,
 *   `0 0 30 2 *`, is refused when it is parsed).
 *
 * Times are seconds since the epoch; `nextOccurrence` answers the first whole minute
 * strictly after `after` that matches, found a day at a time, so it is bounded by the
 * horizon, never by a minute-by-minute scan.
 */
import { Invalid } from "./errors";

/** The longest expression accepted, in characters. */
export const CRON_MAX_CHARS = 64;
/** The most list items one field takes. */
export const CRON_MAX_ITEMS = 16;
/** The least time between two occurrences, in minutes. */
export const CRON_MIN_GAP_MIN = 15;
/** How far ahead an occurrence is looked for, in days: eight years, past any Feb 29 gap. */
export const CRON_HORIZON_DAYS = 8 * 366;

const DAY_S = 86_400;
const FIELDS = Object.freeze([
  Object.freeze({ name: "minute", low: 0, high: 59 }),
  Object.freeze({ name: "hour", low: 0, high: 23 }),
  Object.freeze({ name: "day of the month", low: 1, high: 31 }),
  Object.freeze({ name: "month", low: 1, high: 12 }),
  Object.freeze({ name: "day of the week", low: 0, high: 6 }),
]);
const ITEM = /^(\*|[0-9]{1,2}(?:-[0-9]{1,2})?)(?:\/([0-9]{1,2}))?$/;

/** A parsed expression: each field's matching values, and whether a day field is `*`. */
export interface Cron {
  readonly source: string;
  readonly minutes: readonly number[];
  readonly hours: readonly number[];
  readonly days: ReadonlySet<number>;
  readonly months: ReadonlySet<number>;
  readonly weekdays: ReadonlySet<number>;
  readonly anyDay: boolean;
  readonly anyWeekday: boolean;
}

function refused(why: string): Invalid {
  return new Invalid(`a cron expression is five fields of numbers in UTC: ${why}`);
}

/** One field's values, sorted, from its text. */
function field(text: string, index: number): number[] {
  const { name, low, high } = FIELDS[index]!;
  const items = text.split(",");
  if (items.length > CRON_MAX_ITEMS) {
    throw refused(`the ${name} lists at most ${CRON_MAX_ITEMS} items`);
  }
  const values = new Set<number>();
  for (const item of items) {
    const match = ITEM.exec(item);
    if (match === null) {
      throw refused(`the ${name} field "${item}" is not a value, a range or a step`);
    }
    const [, base, stepText] = match;
    const step = stepText === undefined ? 1 : Number(stepText);
    let from: number;
    let to: number;
    if (base === "*") {
      [from, to] = [low, high];
    } else {
      const [a, b] = base!.split("-").map(Number) as [number, number | undefined];
      from = a;
      to = b ?? (stepText === undefined ? a : high);
    }
    if (from < low || to > high || from > to || step < 1 || step > high - low + 1) {
      throw refused(`the ${name} takes ${low} to ${high}`);
    }
    for (let value = from; value <= to; value += step) {
      values.add(value);
    }
  }
  return [...values].sort((a, b) => a - b);
}

/**
 * The least gap, in minutes, between two occurrences of a day's `times` (minutes of the
 * day, sorted): between neighbours, and from the day's last to the next day's first.
 */
function leastGap(times: readonly number[]): number {
  let least = 1440 - times.at(-1)! + times[0]!;
  for (let at = 1; at < times.length; at++) {
    least = Math.min(least, times[at]! - times[at - 1]!);
  }
  return least;
}

/** `expression` parsed and bounded; refused loudly otherwise. */
export function parseCron(expression: unknown): Cron {
  if (typeof expression !== "string" || expression.length === 0
      || expression.length > CRON_MAX_CHARS) {
    throw refused(`at most ${CRON_MAX_CHARS} characters`);
  }
  const parts = expression.trim().split(/ +/);
  if (parts.length !== 5) {
    throw refused("exactly five fields");
  }
  const [minutes, hours, days, months, weekdays] = parts.map(field) as
    [number[], number[], number[], number[], number[]];
  const times = hours.flatMap((hour) => minutes.map((minute) => hour * 60 + minute));
  if (leastGap(times) < CRON_MIN_GAP_MIN) {
    throw refused(`occurrences at least ${CRON_MIN_GAP_MIN} minutes apart`);
  }
  const cron: Cron = Object.freeze({
    source: parts.join(" "), minutes, hours, days: new Set(days), months: new Set(months),
    weekdays: new Set(weekdays), anyDay: parts[2] === "*", anyWeekday: parts[4] === "*",
  });
  if (firstDay(cron, 0) === null) {
    throw refused(`an occurrence within ${CRON_HORIZON_DAYS} days`);
  }
  return cron;
}

/** Whether the UTC day starting at `dayStart` (seconds) matches the day fields. */
function dayMatches(cron: Cron, dayStart: number): boolean {
  const date = new Date(dayStart * 1000);
  if (!cron.months.has(date.getUTCMonth() + 1)) {
    return false;
  }
  const day = cron.days.has(date.getUTCDate());
  const weekday = cron.weekdays.has(date.getUTCDay());
  if (cron.anyDay && cron.anyWeekday) {
    return true;
  }
  if (cron.anyDay) {
    return weekday;
  }
  if (cron.anyWeekday) {
    return day;
  }
  return day || weekday;
}

/** The first matching day at or after the day holding `from`, its start; null past the horizon. */
function firstDay(cron: Cron, from: number): number | null {
  const start = Math.floor(from / DAY_S) * DAY_S;
  for (let day = 0; day <= CRON_HORIZON_DAYS; day++) {
    const at = start + day * DAY_S;
    if (dayMatches(cron, at)) {
      return at;
    }
  }
  return null;
}

/**
 * The first whole minute strictly after `after` (seconds) at which `cron` occurs, in
 * seconds; null when none falls within `CRON_HORIZON_DAYS`. Pure.
 */
export function nextOccurrence(cron: Cron, after: number): number | null {
  if (!Number.isFinite(after)) {
    throw new Invalid("a time is a number of seconds");
  }
  const from = Math.floor(after / 60) * 60 + 60;  // the next whole minute past `after`
  const start = Math.floor(from / DAY_S) * DAY_S;
  for (let day = 0; day <= CRON_HORIZON_DAYS; day++) {
    const at = start + day * DAY_S;
    if (!dayMatches(cron, at)) {
      continue;
    }
    for (const hour of cron.hours) {
      for (const minute of cron.minutes) {
        const time = at + hour * 3600 + minute * 60;
        if (time >= from) {
          return time;
        }
      }
    }
  }
  return null;
}
