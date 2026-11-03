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
// version, rebuilds `starter.json`, and writes the pull request's body. Merging that pull
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
 * the byte-order mark, the soft hyphen); private-use and unpaired surrogates; tag characters;
 * and variation selectors, which can carry hidden data.
 */
export function hiddenUnicode(text: string): Hidden[] {
  const found: Hidden[] = [];
  const hidden = /[\p{Cc}\p{Cf}\p{Co}\p{Cs}\u{FE00}-\u{FE0F}\u{E0100}-\u{E01EF}]/u;
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
  const hidden = hiddenUnicode(upstream.spec);
  const moved = readmeChanged ? relocation(upstream.readme) : [];
  return {
    changed: specChanged || readmeChanged || newTags.length > 0,
    spec_changed: specChanged, readme_changed: readmeChanged, relocation: moved,
    new_tags: newTags, version: versionOf(upstream.spec), pinned_version: versionOf(vendored),
    hidden, widens: hidden.length > 0,
  };
}

/** The pull request's body: what changed, the checks, and what happens next. */
export function prBody(pin: Pin, upstream: Upstream, found: Report): string {
  const lines = [
    `The OKF watcher found a change in \`${pin.repository}\` (pinned at \`${pin.commit}\`, now ` +
      `\`${upstream.commit}\`).`, "",
    `- SPEC.md: ${found.spec_changed ? "changed" : "unchanged"}; its version line says ` +
      `${found.version ?? "nothing"} (pinned: ${found.pinned_version ?? "nothing"}). A version ` +
      "number alone never decides anything here: read the diff.",
    `- README.md: ${found.readme_changed ? "changed" : "unchanged"}` +
      (found.relocation.length > 0 ? `; it may say the spec moved: ${
        found.relocation.map((line) => `"${line}"`).join("; ")}` : ""),
    `- Tags not seen before: ${found.new_tags.length > 0 ? found.new_tags.join(", ") : "none"}`,
    `- Hidden or control characters in the new spec: ${found.hidden.length === 0 ? "none"
      : found.hidden.slice(0, 50).map((at) => `${at.code} at ${at.line}:${at.column}`).join(", ")}`,
    "", "## What happens next",
    "This pull request is a draft for about 7 days (a cooldown, as Renovate's " +
      "minimumReleaseAge), so a bad upstream commit can be caught and reverted first. Then " +
      "the watcher marks it ready and asks for the automated review; it is never merged on a " +
      "version number, and never by this workflow.",
    found.widens ? "It carries text no reviewer can see, which could widen what every " +
      "owner's agent does: the founder reviews it (`needs-founder`)."
      : "Nothing in it widens what the agent may do: the automated review decides.",
    "Merging it raises the starter's version: each owner's unedited copy of the reference is " +
      "replaced at its next wake; an edited one gets a `.ta-new` beside it and a notice.",
    "", "Registry row(s): S17", "Net core lines: 0",
  ];
  return `${lines.join("\n")}\n`;
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
  const provenance = vendored.slice(0, vendored.indexOf("\n"))
    .replaceAll(pin.commit, upstream.commit).replaceAll(pin.spec_blob, blobId(upstream.spec));
  writeFileSync(vendoredPath, `${provenance}\n${upstream.spec}`);
  manifest.okf = { ...pin, commit: upstream.commit, spec_blob: blobId(upstream.spec),
                   readme_blob: blobId(upstream.readme), tags: upstream.tags };
  manifest.version += 1;
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  execFileSync(process.execPath, [join(HERE, "bundle.mjs")], { stdio: "inherit" });
  writeFileSync(body, prBody(pin, upstream, found));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
