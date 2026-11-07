/**
 * The app's words for the runner's events (`app/view.js` `eventShown`): the runner sends
 * codes and facts, never the words a person reads, and the app shows each event as the
 * runner used to send it, its words in their place; any other event, or one whose facts are
 * not of their shape (the VM is untrusted), as it came.
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { AUTOMATON_STATES, LIST_BYTES } from "../../contract/src/sync-ignore";

type Shown = (event: unknown) => unknown;
const VIEW = join(import.meta.dirname, "..", "app", "view.js");
const { eventShown } = await import(VIEW) as { eventShown: Shown };

describe("the runner's events, in the app's words", () => {
  it("says the sync-ignore lines past their ceilings, which and why", () => {
    const lines = [{ line: "*a?", reason: "states" }, { line: "*b?", reason: "states" }];
    expect(eventShown({ type: "ta.sync_ignore_unapplied", lines, count: 2,
                        list_bytes_past: 1200, automaton_states: AUTOMATON_STATES,
                        list_bytes: LIST_BYTES }))
      .toEqual({ type: "ta.sync_ignore_unapplied", lines, count: 2, list_bytes_past: 1200,
                 message: "In /workspace/.ta/sync-ignore, 2 lines past the 8,192 automaton " +
                   "states the lines with *, ? or [ ] may take together, and the list past its " +
                   "6,291,456 bytes (1,200 more bytes) are not applied, in file order: the files " +
                   "they name go on syncing." });
  });

  it("says the files whose names are not UTF-8, and a slow list", () => {
    expect(eventShown({ type: "ta.sync_unnamed", paths: ["/workspace/b\\xfe"], count: 1 }))
      .toEqual({ type: "ta.sync_unnamed", paths: ["/workspace/b\\xfe"], count: 1,
                 message: "1 file or folder in your sandbox has a name that is not UTF-8, so " +
                   "it does not sync to your files: /workspace/b\\xfe. Rename them to sync " +
                   "them." });
    expect(eventShown({ type: "ta.sync_ignore_slow", match_ms: 6400 }))
      .toEqual({ type: "ta.sync_ignore_slow", match_ms: 6400,
                 message: "Matching /workspace/.ta/sync-ignore took 6 s of this sync; its lines " +
                   "with * or ? cost the most." });
  });

  it("names what scratch went and that the owner's storage is unaffected, never an upgrade",
     () => {
    const shown = eventShown({ type: "ta.scratch_cleared", evicted: ["builder"], expired: [],
                               freed_bytes: 3_100_000_000, free_bytes: 4_000_000_000,
                               total_bytes: 15_000_000_000 }) as { type: string; text: string };
    expect(shown).toEqual({ type: "ta.scratch_cleared", text: "The sandbox's working disk was " +
      "running low, so old scratch was cleared (builder; 3.1 GB freed; 4.0 GB of 15.0 GB free " +
      "now). Scratch is a temporary cache: a cleared repository is cloned again when needed. " +
      "Your storage is unaffected." });
    expect(shown.text.toLowerCase()).not.toContain("upgrade");
  });

  it("says why a run or a file was refused, and why a file was skipped", () => {
    expect(eventShown({ type: "ta.refused", code: "project_env_refused", path: "/workspace/.env",
                        sets: "HTTPS_PROXY" }))
      .toEqual({ type: "ta.refused", reason: "project_env_refused: /workspace/.env sets " +
        "HTTPS_PROXY, which a run's environment never takes from the agent's files" });
    expect(eventShown({ type: "ta.refused", code: "project_env_refused", path: "/a.json",
                        helper: "echo key" }))
      .toEqual({ type: "ta.refused",
                 reason: "project_env_refused: /a.json names a credential helper (echo key)" });
    expect(eventShown({ type: "ta.sync_refused", path: "{home}/a", code: "secret_bytes" }))
      .toEqual({ type: "ta.sync_refused", path: "{home}/a", reason: "holds a secret's bytes" });
    expect(eventShown({ type: "ta.sync_skipped", files: 2, skipped: [
      { path: "/a", code: "gone" }, { path: "/b", code: "held_back" }] }))
      .toEqual({ type: "ta.sync_skipped", files: 2, skipped: [
        { path: "/a", reason: "gone before it was sent" },
        { path: "/b", reason: "held back by a sync-ignore line: your files keep the version " +
                              "they have" }] });
    expect(eventShown({ type: "ta.cli_refused", text: "{}", code: "runner_type" }))
      .toEqual({ type: "ta.cli_refused", text: "{}",
                 reason: "a CLI's line may not carry the runner's own event type (ta.…)" });
  });

  it("shows any other event, and one whose facts are not of their shape, as it came", () => {
    for (const event of [{ type: "result", text: "hi" }, "text", null,
                         { type: "ta.sync_refused", path: "/a", reason: "storage_full" },
                         { type: "ta.sync_unnamed", count: "1", paths: [] },
                         { type: "ta.scratch_cleared", evicted: "builder" },
                         { type: "ta.sync_skipped",
                           skipped: [{ path: "/a", code: "toString" }] }]) {
      expect(eventShown(event)).toBe(event);
    }
  });
});
