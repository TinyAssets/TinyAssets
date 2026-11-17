/**
 * The owner export's header line (`owner/src/export.ts`, format `tinyassets-export`): the first
 * line of every generation's part 0, which says whose the generation is. The export writes it,
 * a restore holds a generation to it before it begins (`owner/src/restore.ts`), and the files
 * Worker reads a namespace's owner from it (`files/src/index.ts` `locate`), so its shape is
 * the contract's.
 */
import { keyed, whole } from "./checks.ts";
import { HEX16 } from "./ids.ts";

export const EXPORT_FORMAT = "tinyassets-export";
export const EXPORT_VERSION = 3;

/**
 * What the header says: the owner schema's migration count, the owner, when the generation
 * began (ms), the generation, and its log's first seq.
 */
export interface ExportHeader {
  readonly schema: number;
  readonly owner: string;
  readonly at: number;
  readonly gen: string;
  readonly s: number;
}

/** The header line's object for `header`, as the export writes it. */
export function headerLine(header: ExportHeader): Record<string, unknown> {
  return { t: "header", format: EXPORT_FORMAT, version: EXPORT_VERSION, ...header };
}

/** `line`, parsed, as a header of this format and version in its exact shape, or null. */
export function exportHeader(line: unknown): ExportHeader | null {
  if (!keyed(line, ["t", "format", "version", "schema", "owner", "at", "gen", "s"])
      || line["t"] !== "header" || line["format"] !== EXPORT_FORMAT
      || line["version"] !== EXPORT_VERSION || !whole(line["schema"], 1)
      || typeof line["owner"] !== "string" || line["owner"] === "" || !whole(line["at"], 0)
      || typeof line["gen"] !== "string" || !HEX16.test(line["gen"]) || !whole(line["s"], 1)) {
    return null;
  }
  return Object.freeze({ schema: line["schema"], owner: line["owner"], at: line["at"],
                         gen: line["gen"], s: line["s"] });
}
