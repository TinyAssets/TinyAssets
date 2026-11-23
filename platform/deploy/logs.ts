/**
 * Mode `logs`: the platform Workers' error and warn lines of the last minutes, as Workers Logs
 * (each Worker's `observability`) keeps them, read with the Cloudflare token and printed to the
 * job's log, each line redacted (`redact`) and the whole bounded. It changes nothing.
 *
 * The query is Cloudflare's Workers Observability telemetry query (`Cloudflare.telemetry`):
 * the `events` view over the `cloudflare-workers` dataset, filtered to the Workers by
 * `$metadata.service` and to a line logged at `error` or `warn`, or carrying an error
 * (`$metadata.error`: an uncaught exception). Its fields are those Cloudflare's OpenAPI
 * schema (`cloudflare/api-schemas` `openapi.yaml`, `telemetry.query`) names, read 2026-11-23.
 * `dry` runs it without keeping the run in the account's query history.
 *
 * The job's log of a public repository is public, so every printed line is redacted first, by
 * shape, never by value: the plan job holds no value but the Cloudflare token, which Actions
 * masks itself.
 */
import type { Cloudflare } from "./cloudflare.ts";

/**
 * The window's bound, in minutes: Workers Logs keep 7 days on Workers Paid, the account's
 * plan (developers.cloudflare.com/workers/observability/logs/workers-logs/, "Limits").
 */
export const MINUTES_MAX = 7 * 24 * 60;
/** The longest text a run filters by. */
export const FILTER_MAX = 200;
/** The window a run gives none of. */
export const MINUTES = 60;
/** The most events one query returns: Cloudflare's own bound on `limit` is 2000. */
export const EVENTS = 200;
/** The most characters one printed line holds, and the most the whole printout does. */
export const LINE_MAX = 2000;
export const PRINT_MAX = 100_000;

/** What a logs run asks: its window, and the Workers' scripts by the name each is printed as. */
export interface LogsAsk {
  readonly minutes: number;
  /** Each script read, to the Worker's name it is printed as (its folder). */
  readonly scripts: Readonly<Record<string, string>>;
  /** Text every line read holds somewhere, any case (Cloudflare's `needle`); null for none. */
  readonly filter: string | null;
}

const REDACTED = "[redacted]";

/**
 * The shapes a credential takes in a log line, each replaced by `[redacted]`, in order:
 * - a header's credential scheme and what follows it (`Bearer …`, `Basic …`);
 * - the value of a header, field, query or variable named as a credential (`authorization`,
 *   `cookie`, an API key, or a name ending in token, secret, password, keyring, credential),
 *   in a header line, JSON (escaped too) or a query;
 * - a JWT;
 * - an email address (an owner's, in a public log);
 * - a run of 24 or more base64, base64url or hex characters (a key, a keyring's keys, a sealed
 *   route, a token, a long id), unless every part of it between slashes is words: lowercase
 *   ones joined by `_` or `-` (a path, an error code), or a constant's uppercase name.
 */
const SCHEME = /\b(Bearer|Basic)(\s+)[A-Za-z0-9._~+/=-]{8,}/gi;
const NAMED = new RegExp(
  "([\\w-]*(?:authorization|cookie|api[-_]?key|token|secret|password|passwd|keyring|credential)s?)" +
  "(\\\\?[\"']?\\s*[:=]\\s*\\\\?[\"']?\\s*)([^\\s\"'\\\\,;&{}\\[\\]]+)", "gi");
const JWT = /\beyJ[\w-]{4,}\.[\w-]{4,}\.[\w-]*/g;
const EMAIL = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g;
const RUN = /[A-Za-z0-9+/_=-]{24,}/g;
const WORDS = /^(?:[a-z]*(?:[_-][a-z]+)*|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)$/;

/** `text` with every credential-shaped part of it replaced by `[redacted]`. */
export function redact(text: string): string {
  return text
    .replace(SCHEME, `$1$2${REDACTED}`)
    .replace(NAMED, `$1$2${REDACTED}`)
    .replace(JWT, REDACTED)
    .replace(EMAIL, REDACTED)
    .replace(RUN, (run) => run.split("/").every((part) => WORDS.test(part)) ? run : REDACTED);
}

/** `line` cut to `LINE_MAX` characters, after its redaction, so a cut never shows a part. */
function bounded(line: string): string {
  return line.length <= LINE_MAX ? line
    : `${line.slice(0, LINE_MAX)} [cut at ${LINE_MAX} characters]`;
}

/** The query's body (`telemetry.query`) for `ask`, its window ending at `now`. */
export function logsQuery(ask: LogsAsk, now: Date): Record<string, unknown> {
  const to = now.getTime();
  return {
    queryId: "tinyassets-deploy-logs", view: "events", dry: true, limit: EVENTS,
    timeframe: { from: to - ask.minutes * 60_000, to },
    parameters: {
      datasets: ["cloudflare-workers"],
      filterCombination: "and",
      filters: [
        { key: "$metadata.service", operation: "in", type: "string",
          value: Object.keys(ask.scripts).join(",") },
        { kind: "group", filterCombination: "or", filters: [
          { key: "$metadata.level", operation: "in", type: "string", value: "error,warn" },
          { key: "$metadata.error", operation: "exists", type: "string" }] }],
      ...ask.filter === null ? {} : { needle: { value: ask.filter, isRegex: false,
                                                matchCase: false } },
    },
  };
}

/** One event as the query answers it: the fields printed. */
interface Event {
  readonly timestamp: number;
  readonly service: string;
  readonly level: string | null;
  readonly message: string | null;
  readonly error: string | null;
  readonly source: unknown;
}

const text = (value: unknown): string | null => typeof value === "string" ? value : null;

/** The events and the count matched of a query's `result`; out of shape, a throw. */
function eventsOf(result: unknown): { events: Event[]; count: number } {
  const events = (result as { events?: { events?: unknown; count?: unknown } } | null)?.events;
  const listed = events?.events;
  if (!Array.isArray(listed)) {
    throw new Error("the Workers Logs query answered out of shape (no events)");
  }
  const read = listed.map((each): Event => {
    const event = (each ?? {}) as Record<string, unknown>;
    const meta = (event["$metadata"] ?? {}) as Record<string, unknown>;
    const service = text(meta["service"])
      ?? text((event["$workers"] as Record<string, unknown> | undefined)?.["scriptName"]);
    if (typeof event["timestamp"] !== "number" || service === null) {
      throw new Error("the Workers Logs query answered an event out of shape (no timestamp or " +
                      "Worker)");
    }
    return { timestamp: event["timestamp"], service, level: text(meta["level"]),
             message: text(meta["message"]), error: text(meta["error"]),
             source: event["source"] };
  });
  return { events: read, count: typeof events?.count === "number" ? events.count : read.length };
}

/** Each `stack` text in `value` (an error as logged), and `value` with them taken out. */
function stacks(value: unknown, found: string[] = [], depth = 0): unknown {
  if (Array.isArray(value)) {
    return value.map((each) => stacks(each, found, depth + 1));
  }
  if (typeof value === "object" && value !== null && depth < 8) {
    return Object.fromEntries(Object.entries(value).flatMap(([key, each]) => {
      if (key === "stack" && typeof each === "string") {
        found.push(each);
        return [];
      }
      return [[key, stacks(each, found, depth + 1)]];
    }));
  }
  return value;
}

/** One event's printed lines: its head, its error, its stacks, and its source as logged. */
function printed(event: Event, ask: LogsAsk): string[] {
  const lines = [`${new Date(event.timestamp).toISOString()} ${
    ask.scripts[event.service] ?? event.service} ${event.level ?? "-"} ${event.message ?? ""}`];
  if (event.error !== null && event.error !== event.message) {
    lines.push(`  error: ${event.error}`);
  }
  const found: string[] = [];
  const rest = stacks(event.source, found);
  for (const stack of found) {
    lines.push(...stack.split("\n").map((frame) => `  ${frame.trim()}`));
  }
  if (typeof rest === "object" && rest !== null) {
    lines.push(`  source: ${JSON.stringify(rest)}`);
  } else if (typeof rest === "string" && rest !== event.message) {
    lines.push(`  source: ${rest}`);
  }
  return lines;
}

/**
 * The Workers' error and warn lines `ask` names, oldest first, each redacted and bounded,
 * the whole within `PRINT_MAX`; and the plan's one line, what was read.
 */
export async function workerLogs(cloudflare: Cloudflare, account: string, ask: LogsAsk,
                                 now: Date): Promise<{ lines: string[]; said: string }> {
  const { events, count } = eventsOf(await cloudflare.telemetry(account, logsQuery(ask, now)));
  const sorted = [...events].sort((a, b) => a.timestamp - b.timestamp);
  const lines = [`The Workers' error and warn lines from ${
    new Date(now.getTime() - ask.minutes * 60_000).toISOString()} to ${now.toISOString()}, ` +
    `oldest first: ${sorted.length} of ${count} matched`];
  let size = lines[0]!.length;
  let shown = 0;
  for (const event of sorted) {
    const each = printed(event, ask).map((line) => bounded(redact(line)));
    const length = each.reduce((sum, line) => sum + line.length + 1, 0);
    if (size + length > PRINT_MAX) {
      lines.push(`(${sorted.length - shown} more not printed: the printout's bound is ${
        PRINT_MAX} characters)`);
      break;
    }
    lines.push(...each);
    size += length;
    shown += 1;
  }
  const workers = [...new Set(Object.values(ask.scripts))].join(", ");
  return { lines, said: redact(`nothing: it read the error and warn lines of ${workers} over ` +
    `the last ${ask.minutes} minutes${ask.filter === null ? "" : ` holding "${ask.filter}"`}, ` +
    `${count} matched and ${shown} printed in the job's log`) };
}
