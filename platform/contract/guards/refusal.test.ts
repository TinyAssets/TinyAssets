/**
 * The one refusal (`src/refusal.ts`): a closed list of codes, each code's status, and the
 * one test of a refusal against a bug, here and as one crosses RPC.
 *
 * Across Workers RPC a thrown error is serialized by workerd: under
 * `enhanced_error_serialization` (`src/workerd/io/compatibility-date.capnp`, on from
 * 2026-04-21) `jsg::Serializer::WriteHostObject` writes its name (one no built-in error has),
 * its message and its own properties, and the caller gets a plain `Error` with them; without
 * it, only the message and a built-in error type cross. So a refusal keeps its fields as own
 * data properties, and every Worker that throws or reads one runs at a date with that flag
 * on and never turns it off. The real crossing is held in workerd by the doors' tests
 * (`doors/test/api-keys.test.ts`, the owner's object's RPC).
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { readWrangler, workerFolders } from "../../ci/wrangler";
import { MAINTENANCE } from "../src/control";
import {
  EGRESS_CODES, ERASING, FAULT_CODES, faultOf, FileFault, Refusal, REFUSALS,
  refusalOf,
} from "../src/refusal";

/** The first compatibility date at which workerd carries an error's own properties. */
const ENHANCED_SINCE = "2026-04-21";

/** An error as it reaches the caller across RPC: a plain `Error` with the refusal's fields. */
function crossed(name: string, message: string, fields: Record<string, unknown>): Error {
  const error = new Error(message);
  Object.defineProperty(error, "name", { value: name });
  return Object.assign(error, fields, { remote: true });
}

describe("the Workers' runtime", () => {
  const workers = workerFolders();

  it("carries a refusal's own fields across RPC in every Worker", () => {
    expect(workers).toEqual(["codemode", "doors", "files", "gateway", "owner", "sandbox"]);
    for (const worker of workers) {
      const config = readWrangler(join(PLATFORM, worker));
      expect(String(config["compatibility_date"]) >= ENHANCED_SINCE, worker).toBe(true);
      expect(config["compatibility_flags"] ?? [], worker)
        .not.toContain("legacy_error_serialization");
    }
  });
});

describe("a refusal", () => {
  it("is a value: its code, its code's status, its Retry-After and its facts", () => {
    const refusal = new Refusal("storage_busy", "storage_busy: catching up",
                                { retryAfterS: 1, facts: { held_bytes: 3 } });
    expect([refusal.name, refusal.code, refusal.message, refusal.status, refusal.retryAfter,
            refusal.facts]).toEqual(["Refusal", "storage_busy", "storage_busy: catching up",
                                     503, 1, { held_bytes: 3 }]);
    expect(new Refusal("storage_busy").retryAfter).toBe(30);  // its code's
    expect(new Refusal("refused").retryAfter).toBeUndefined();
    expect(new Refusal("unknown_model").message).toBe("unknown_model");  // the gateway's
  });

  it("keeps every field it carries as its own, so that each crosses RPC", () => {
    const refusal = new Refusal("storage_full", "storage_full: full",
                                { retryAfterS: 5, facts: { used_bytes: 1 } });
    for (const field of ["name", "code", "retryAfterS", "facts"]) {
      expect(Object.hasOwn(refusal, field), field).toBe(true);
    }
  });

  it("tells its family and whether its message names its code", () => {
    expect([new Refusal("invalid").invalid, new Refusal("message_body").invalid,
            new Refusal("refused").invalid, new Refusal("file_quota").invalid])
      .toEqual([true, true, false, false]);
    expect([new Refusal("file_quota").named, new Refusal("refused").named,
            new Refusal("erasing", ERASING).named]).toEqual([true, false, false]);
  });
});

describe("a transient refusal", () => {
  it("is one of the codes for the moment only, and each that says when is one", () => {
    const transient = Object.entries(REFUSALS)
      .filter(([, entry]) => (entry as { transient?: true }).transient === true)
      .map(([code]) => code).sort();
    expect(transient).toEqual(["busy", "events_busy", "events_raced", "root_changed",
                               "storage_busy", "unavailable"]);
    expect(Object.entries(REFUSALS)
      .filter(([, entry]) => (entry as { retryAfterS?: number }).retryAfterS !== undefined)
      .map(([code]) => code).sort())
      .toEqual(["busy", "events_busy", "events_raced", "storage_busy", "unavailable"]);
    expect([new Refusal("busy").transient, new Refusal("storage_busy").transient,
            new Refusal("unavailable", MAINTENANCE).transient,
            new Refusal("tier_runs").transient, new Refusal("refused").transient])
      .toEqual([true, true, true, false, false]);
  });
});

describe("the code list", () => {
  it("gives each code one status", () => {
    for (const [code, entry] of Object.entries(REFUSALS)) {
      expect(Number.isInteger(entry.status) && entry.status >= 400 && entry.status < 600, code)
        .toBe(true);
    }
  });

  // The one table of what a code is answered with (`d2:N87`): what every door has answered
  // with each, its status and its `Retry-After`.
  it.each([
    ["refused", 403, undefined], ["invalid", 400, undefined],
    ["message_body", 400, undefined], ["chunk_deleting", 403, undefined],
    ["file_quota", 403, undefined], ["owner_suspended", 403, undefined],
    ["storage_full", 507, undefined], ["file_index_full", 507, undefined],
    ["uploads_open", 409, undefined],
    ["file_busy", 409, undefined], ["storage_busy", 503, 30], ["busy", 503, 1],
    ["events_busy", 503, 1], ["events_raced", 503, 1],
    // The platform gates, closed: the doors' 503 and its minute.
    ["unavailable", 503, 60],
    // The doors' own, as the egress door has answered them.
    ["runner_token_missing", 401, undefined], ["run_secret_missing", 401, undefined],
    ["sync_token_missing", 401, undefined], ["sync_token_unknown", 401, undefined],
    ["sync_token_expired", 401, undefined], ["not_found", 404, undefined],
    ["method_not_allowed", 405, undefined], ["events_too_large", 413, undefined],
    ["egress_gateway_failed", 502, undefined], ["egress_deadline", 504, undefined],
    ["egress_aborted", 503, undefined], ["egress_ended", 503, undefined],
    // The files plane's.
    ["keyring_unavailable", 500, undefined], ["data_key_refused", 400, undefined],
    ["root_changed", 409, undefined],
  ] as const)("answers %s with %i", (code, status, retry) => {
    const refusal = new Refusal(code);
    expect([refusal.status, refusal.retryAfter]).toEqual([status, retry]);
  });

  it("names the egress door's every code, each shown by its code", () => {
    expect(EGRESS_CODES.filter((code) => !new Refusal(code).named)).toEqual([]);
  });
});

describe("the test of a refusal against a bug", () => {
  it("knows a refusal here, and one as it crossed RPC, by its fields", () => {
    const here = new Refusal("ticket_refused", "ticket_refused: no");
    expect(refusalOf(here)).toBe(here);
    const there = refusalOf(crossed("Refusal", "storage_full: full",
                                    { code: "storage_full", retryAfterS: 2, facts: { a: 1 } }))!;
    expect(there).toBeInstanceOf(Refusal);
    expect([there.code, there.message, there.status, there.retryAfterS, there.facts])
      .toEqual(["storage_full", "storage_full: full", 507, 2, { a: 1 }]);
  });

  it("calls anything else a bug, a refusal of an unknown code or a malformed field too", () => {
    for (const error of [new Error("storage_full: full"), "storage_full", null,
                         crossed("Refused", "storage_full: full", { code: "storage_full" }),
                         crossed("Refusal", "x", { code: "not_a_code" }),
                         crossed("Refusal", "x", { code: "toString" }),
                         crossed("Refusal", "x", { code: "busy", retryAfterS: -1 }),
                         crossed("Refusal", "x", { code: "busy", retryAfterS: "1" }),
                         crossed("Refusal", "x", { code: "busy", facts: [1] })]) {
      expect(refusalOf(error)).toBeNull();
    }
  });
});

describe("a file fault", () => {
  it("is known by its own code, here and as it crossed RPC, and is no refusal", () => {
    const fault = new FileFault("not_directory", "a parent is not a directory");
    expect([fault.message, faultOf(fault), refusalOf(fault)])
      .toEqual(["not_directory: a parent is not a directory", "not_directory", null]);
    expect(faultOf(crossed("FileFault", "exists: in the way", { code: "exists" }))).toBe("exists");
    expect(faultOf(crossed("FileFault", "x", { code: "busy" }))).toBeNull();
    expect(FAULT_CODES).toContain("not_found");
  });
});
