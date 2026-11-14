/**
 * The Workers' configs as wrangler reads them, and the wrangler the platform installs: what
 * the deploy kit, the generators and the guards need of wrangler without the test kit
 * (`workers.ts`, which bundles a Worker for miniflare and so loads TypeScript).
 *
 * Node runs this file as it is (type stripping), so its own imports name their `.ts` files.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

import { PLATFORM } from "./paths.ts";

/** A Worker's config, as untyped data: each reader reads it field by field. */
export type Config = Record<string, unknown>;

/**
 * `text` with its comments taken out and each comma before a closing bracket blanked, as
 * wrangler reads JSON with comments; strings are kept whole. Every character keeps its place,
 * so a parse error's position is the file's.
 */
function plainJson(text: string): string {
  const out = text.split("");
  const blank = (from: number, to: number) => {
    for (let at = from; at < to; at += 1) {
      out[at] = out[at] === "\n" ? "\n" : " ";
    }
  };
  let comma = -1;
  for (let i = 0; i < text.length;) {
    const at = text[i]!;
    if (at === '"') {
      let end = i + 1;
      while (end < text.length && text[end] !== '"') {
        end += text[end] === "\\" ? 2 : 1;
      }
      comma = -1;
      i = end + 1;
    } else if (text.startsWith("//", i) || text.startsWith("/*", i)) {
      const line = text.startsWith("//", i);
      const close = line ? text.indexOf("\n", i) : text.indexOf("*/", i + 2);
      if (close === -1 && !line) {
        throw new SyntaxError(`a comment at ${i} is never closed`);
      }
      const end = close === -1 ? text.length : line ? close : close + 2;
      blank(i, end);
      i = end;
    } else {
      if ((at === "}" || at === "]") && comma !== -1) {
        blank(comma, comma + 1);
      }
      comma = at === "," ? i : /\s/.test(at) ? comma : -1;
      i += 1;
    }
  }
  return out.join("");
}

/** `text`, a wrangler config in JSON with comments, parsed; loud when it does not parse. */
export function parseWrangler(text: string, fileName = "wrangler.jsonc"): Config {
  let parsed: unknown;
  try {
    parsed = JSON.parse(plainJson(text));
  } catch (error) {
    throw new Error(`${fileName}: ${(error as Error).message}`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${fileName}: a config is an object`);
  }
  return parsed as Config;
}

/** The config at `where`: a config file, or a Worker's folder (its `wrangler.jsonc`). */
export function readWrangler(where: string): Config {
  const path = /\.(?:jsonc?|toml)$/.test(where) ? where : join(where, "wrangler.jsonc");
  return parseWrangler(readFileSync(path, "utf8"), path);
}

/** The platform's Workers: each folder of `platform/` that holds a `wrangler.jsonc`, sorted. */
export function workerFolders(): string[] {
  return readdirSync(PLATFORM, { withFileTypes: true })
    .filter((entry) => entry.isDirectory()
                       && existsSync(join(PLATFORM, entry.name, "wrangler.jsonc")))
    .map((entry) => entry.name).sort();
}

/** The wrangler package the platform installs (hoisted to `platform/node_modules`). */
const WRANGLER_PACKAGE = dirname(createRequire(join(PLATFORM, "package.json"))
  .resolve("wrangler/package.json"));
/** Its command line, run with Node. */
export const WRANGLER = join(WRANGLER_PACKAGE, "bin", "wrangler.js");

/** The keys wrangler's own schema offers at the top level, or in an environment. */
export function wranglerKeys(scope: "RawConfig" | "RawEnvironment" = "RawConfig"): string[] {
  const schema = JSON.parse(readFileSync(join(WRANGLER_PACKAGE, "config-schema.json"), "utf8")) as
    { definitions: Record<string, { properties: Record<string, unknown> }> };
  return Object.keys(schema.definitions[scope]!.properties);
}
