// The OKF watcher (okf_update_research.md §5; `.github/workflows/okf-watch.yml`): the starter's
// `knowledge` skill vendors the Open Knowledge Format spec pinned to one commit of its
// canonical repository, and this compares that pin with the repository as it stands. It
// keys on content, never on a version number: OKF has changed its rules under an unchanged
// version and broken them under a "minor" one (§1.4), so no upstream version moves anything
// here by itself.
//
//   node platform/starter/okf-watch.ts check --upstream <clone> [--out report.json]
//   node platform/starter/okf-watch.ts apply --upstream <clone> --body <pr-body.md>
//
// `check` reports: whether `SPEC.md` changed (by its git blob id), whether the README moved
// or carries a relocation banner (the spec moved once already, §1.1), tags the pin has not
// seen, the spec's own version line, and every hidden or control character in the new text
// (the "rules file backdoor": text a reviewer cannot see, §3). `apply` writes the new spec
// into the vendored file under its provenance line, moves the pin, raises the starter's
// version, runs the starter's one build (`bundle.mjs`: `starter.json` and the plugins that
// carry the spec), and writes the pull request's body. Merging that pull
// request, after its cooldown and review, ships the new spec to every owner whose copy is
// unedited (`owner/src/starter.ts`); nothing here merges anything.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export interface Pin {
  readonly repository: string;
  readonly commit: string;
  readonly spec_blob: string;
  readonly readme_blob: string;
  readonly vendored: string;
  readonly tags?: readonly string[];
}

export interface Upstream {
  readonly commit: string;
  readonly spec: string;
  readonly readme: string;
  readonly tags: readonly string[];
}

/** One character a reader cannot see, where it is. */
export interface Hidden {
  readonly line: number;
  readonly column: number;
  readonly code: string;
}

export interface Report {
  readonly changed: boolean;
  readonly spec_changed: boolean;
  readonly readme_changed: boolean;
  readonly relocation: readonly string[];
  readonly new_tags: readonly string[];
  readonly version: string | null;
  readonly pinned_version: string | null;
  readonly hidden: readonly Hidden[];
  /** Whether a person (the founder) must see it: text no reviewer can see could widen the agent. */
  readonly widens: boolean;
}

/** Git's blob id of `text`, as `git hash-object` gives it. */
export function blobId(text: string): string {
  const bytes = Buffer.from(text, "utf8");
  return createHash("sha1").update(`blob ${bytes.byteLength}\0`).update(bytes).digest("hex");
}

/**
 * Every character that renders as nothing or as something else: controls but tab, line feed
 * and carriage return; format characters (zero-width, bidirectional overrides and isolates,
 * the byte-order mark, the soft hyphen); the line and paragraph separators (U+2028, U+2029);
 * private-use and unpaired surrogates; tag characters; the blank letters that render as
 * nothing (the Hangul fillers U+115F, U+1160, U+3164, U+FFA0 and the braille blank U+2800);
 * and variation selectors, which can carry hidden data.
 */
export function hiddenUnicode(text: string): Hidden[] {
  const found: Hidden[] = [];
  const hidden = new RegExp("[\\p{Cc}\\p{Cf}\\p{Co}\\p{Cs}\\p{Zl}\\p{Zp}\\u{115F}\\u{1160}" +
                            "\\u{2800}\\u{3164}\\u{FFA0}\\u{FE00}-\\u{FE0F}\\u{E0100}-\\u{E01EF}]", "u");
  text.split("\n").forEach((line, index) => {
    let column = 0;
    for (const char of line) {
      column += 1;
      if (char !== "\t" && char !== "\r" && hidden.test(char)) {
        found.push({ line: index + 1, column,
                     code: `U+${char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}` });
      }
    }
  });
  return found;
}

/** The README's lines that say the spec lives elsewhere now. */
export function relocation(readme: string): string[] {
  const moved = /(now lives in|has moved|moved to|stop using|frozen snapshot|no longer maintained|deprecated in favou?r of|canonical (home|location) is)/i;
  return readme.split("\n").map((line) => line.trim()).filter((line) => moved.test(line));
}

/** The spec's own version line (`This document specifies OKF version **0.2**`), or null. */
export function versionOf(spec: string): string | null {
  return /specifies OKF version \*\*([0-9][0-9.]*)\*\*/.exec(spec)?.[1] ?? null;
}

/** The vendored file's upstream text: everything after its first (provenance) line. */
export function vendoredSpec(file: string): string {
  return file.slice(file.indexOf("\n") + 1);
}

/** What changed between the pin (and the vendored text) and the upstream as it stands. */
export function report(pin: Pin, upstream: Upstream, vendored: string): Report {
  const specChanged = blobId(upstream.spec) !== pin.spec_blob;
  const readmeChanged = blobId(upstream.readme) !== pin.readme_blob;
  const known = new Set(pin.tags ?? []);
  const newTags = upstream.tags.filter((tag) => !known.has(tag));
  const moved = readmeChanged ? relocation(upstream.readme) : [];
  // Everything upstream that reaches the pull request is checked, not the spec alone.
  const hidden = [...hiddenUnicode(upstream.spec),
                  ...[...moved, ...newTags].flatMap((text) => hiddenUnicode(text)
                    .map((at) => ({ ...at, line: 0 })))];
  return {
    changed: specChanged || readmeChanged || newTags.length > 0,
    spec_changed: specChanged, readme_changed: readmeChanged, relocation: moved,
    new_tags: newTags, version: versionOf(upstream.spec), pinned_version: versionOf(vendored),
    hidden, widens: hidden.length > 0,
  };
}

/**
 * Upstream text, quoted where nothing in it can act on the page: each string as JSON, every
 * character but printable ASCII escaped (backticks and tildes too), inside one fenced block,
 * so no `<!--`, fence, markup or invisible character of upstream's hides or changes what the
 * reviewer reads.
 */
export function quoted(texts: readonly string[]): string {
  if (texts.length === 0) {
    return "none";
  }
  // Every character but printable ASCII as its escape, so nothing hidden reaches the page.
  const safe = texts.map((text) => JSON.stringify(text).replace(/[^ -}]|[`~]/gu, (char) =>
    char.split("").map((unit) => `\\u${unit.charCodeAt(0).toString(16).padStart(4, "0")}`).join("")));
  return `\n\n\`\`\`text\n${safe.join("\n")}\n\`\`\`\n`;
}

/** The pull request's body: what changed, the checks, and what happens next. */
export function prBody(pin: Pin, upstream: Upstream, found: Report): string {
  const lines = [
    `The OKF watcher found a change in \`${pin.repository}\` (pinned at \`${pin.commit}\`, now ` +
      `\`${upstream.commit}\`).`, "",
    `- SPEC.md: ${found.spec_changed ? "changed" : "unchanged"}; its version line says ` +
      `${found.version ?? "nothing"} (pinned: ${found.pinned_version ?? "nothing"}). A version ` +
      "number alone never decides anything here: read the diff.",
    `- Hidden or control characters (in the spec, by line:column; in the README lines and ` +
      `tags below, line 0): ${found.hidden.length === 0 ? "none"
      : found.hidden.slice(0, 50).map((at) => `${at.code} at ${at.line}:${at.column}`).join(", ")}`,
    `- README.md: ${found.readme_changed ? "changed" : "unchanged"}; its lines that may say ` +
      `the spec moved, quoted: ${quoted(found.relocation)}`,
    `- Tags not seen before, quoted: ${quoted(found.new_tags)}`,
    "", "## What happens next",
    "This pull request is a draft for about 7 days (a cooldown, as Renovate's " +
      "minimumReleaseAge), so a bad upstream commit can be caught and reverted first. Then " +
      "the watcher marks it ready, which starts its checks, and asks for the review every pull " +
      "request needs; it is never merged on a version number, and never by this workflow.",
    found.widens ? "It carries text no reviewer can see, which could widen what every " +
      "owner's agent does: the founder reviews it (`needs-founder`)."
      : "Nothing in it widens what the agent may do: the review decides.",
    found.spec_changed ? "Merging it raises the starter's version: each owner's unedited copy " +
      "of the reference is replaced at its next wake; an edited one gets a `.ta-new` beside it " +
      "and a notice." : "The spec's bytes are unchanged: merging it moves the pin's README and " +
      "tags only, and raises no starter version, so no owner's files change.",
    "", "Registry row(s): S17", "Net core lines: 0",
  ];
  return `${lines.join("\n")}\n`;
}

/**
 * What a proposal changes: the pin always (the README's blob, the tags); the vendored spec,
 * its provenance line and the starter's version only when the spec's bytes changed, so a
 * README or a tag alone ships nothing to any owner.
 */
export function applied<M extends { version: number; okf: Pin }>(
    manifest: M, vendored: string, upstream: Upstream, found: Report):
    { manifest: M; vendored: string } {
  const pin = manifest.okf;
  const okf = { ...pin, commit: found.spec_changed ? upstream.commit : pin.commit,
                spec_blob: blobId(upstream.spec), readme_blob: blobId(upstream.readme),
                tags: [...upstream.tags] };
  if (!found.spec_changed) {
    return { manifest: { ...manifest, okf }, vendored };
  }
  const provenance = vendored.slice(0, vendored.indexOf("\n"))
    .replaceAll(pin.commit, upstream.commit).replaceAll(pin.spec_blob, blobId(upstream.spec));
  return { manifest: { ...manifest, okf, version: manifest.version + 1 },
           vendored: `${provenance}\n${upstream.spec}` };
}

/** The upstream as a clone at `dir` holds it: its head commit, spec, README and tags. */
export function readUpstream(dir: string): Upstream {
  const git = (...args: string[]) => execFileSync("git", ["-C", dir, ...args],
                                                  { encoding: "utf8" });
  return { commit: git("rev-parse", "HEAD").trim(), spec: readFileSync(join(dir, "SPEC.md"), "utf8"),
           readme: readFileSync(join(dir, "README.md"), "utf8"),
           tags: git("tag", "--list").split("\n").map((tag) => tag.trim()).filter(Boolean) };
}

const HERE = dirname(fileURLToPath(import.meta.url));

function main(argv: readonly string[]): void {
  const [command] = argv;
  const option = (name: string) => {
    const at = argv.indexOf(name);
    return at < 0 ? null : argv[at + 1] ?? null;
  };
  const upstreamDir = option("--upstream");
  if ((command !== "check" && command !== "apply") || upstreamDir === null) {
    throw new Error("usage: okf-watch.ts check|apply --upstream <clone> [--out file] " +
                    "[--body file]");
  }
  const manifestPath = join(HERE, "manifest.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as
    { version: number; okf: Pin; [key: string]: unknown };
  const pin = manifest.okf;
  const vendoredPath = join(HERE, pin.vendored.slice(1));
  const vendored = readFileSync(vendoredPath, "utf8");
  if (blobId(vendoredSpec(vendored)) !== pin.spec_blob) {
    throw new Error("the vendored spec is not the pinned blob: fix the pin first");
  }
  const upstream = readUpstream(upstreamDir);
  const found = report(pin, upstream, vendoredSpec(vendored));
  if (command === "check") {
    const out = option("--out");
    const json = `${JSON.stringify({ ...found, commit: upstream.commit }, null, 2)}\n`;
    if (out === null) {
      process.stdout.write(json);
    } else {
      writeFileSync(out, json);
    }
    return;
  }
  const body = option("--body");
  if (body === null || !found.changed) {
    throw new Error("apply needs --body, and a change to apply");
  }
  const next = applied(manifest, vendored, upstream, found);
  if (next.vendored !== vendored) {
    writeFileSync(vendoredPath, next.vendored);
  }
  writeFileSync(manifestPath, `${JSON.stringify(next.manifest, null, 2)}\n`);
  execFileSync(process.execPath, [join(HERE, "bundle.mjs")], { stdio: "inherit" });
  writeFileSync(body, prBody(pin, upstream, found));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
