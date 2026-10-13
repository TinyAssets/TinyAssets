import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(siteRoot, "../..");
const SETTING = "WebSite/site-react/lib/legal-info.json";

const SKIP_DIRS = new Set([
  ".git",
  ".next",
  ".venv",
  "__pycache__",
  "coverage",
  "dist",
  "node_modules",
  "out",
  "out-shots",
  "venv",
]);

const TEXT = /\.(?:cjs|css|html|js|json|jsx|md|mjs|py|sh|svg|toml|ts|tsx|txt|xml|ya?ml)$/i;

function read(path) {
  return readFileSync(resolve(siteRoot, path), "utf8");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function fragments(template) {
  return template
    .split(/\{(?:brand|legal_name)\}/g)
    .map((part) => part.replace(/^[\s,.;:]+|[\s,.;:]+$/g, ""))
    .filter((part) => part.length >= 8);
}

function repoFiles() {
  const found = [];
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) walk(resolve(directory, entry.name));
        continue;
      }
      if (!entry.isFile() || !TEXT.test(entry.name)) continue;
      const path = resolve(directory, entry.name);
      if (statSync(path).size > 8_000_000) continue;
      found.push(path);
    }
  };
  walk(repoRoot);
  return found;
}

const info = JSON.parse(read("lib/legal-info.json"));
const phrases = [info.operator_template, info.terms_party_template].flatMap(fragments);
const needles = [
  { label: "legal_name", re: new RegExp(escapeRegExp(info.legal_name), "i") },
  { label: "governing_law", re: new RegExp(`\\b${escapeRegExp(info.governing_law)}\\b`, "i") },
  ...phrases.map((phrase) => ({ label: "trade-name phrase", re: new RegExp(escapeRegExp(phrase), "i") })),
];

function hits(text) {
  return needles.filter((needle) => needle.re.test(text)).map((needle) => needle.label);
}

test("the legal operator record is one editable setting", () => {
  assert.equal(typeof info.brand, "string");
  assert.ok(info.brand.length > 0);
  assert.equal(typeof info.legal_name, "string");
  assert.ok(info.legal_name.length > 0);
  assert.equal(info.contact, "info@tinyassets.io");
  assert.equal(typeof info.governing_law, "string");
  assert.ok(info.governing_law.length > 0);
  for (const key of ["operator_template", "terms_party_template"]) {
    assert.match(info[key], /\{brand\}/);
    assert.match(info[key], /\{legal_name\}/);
  }
  assert.ok(phrases.length >= 2, "both public sentences must carry phrasing beyond the name");

  const filler = read("lib/legal.ts");
  assert.match(filler, /replaceAll\("\{brand\}", info\.brand\)/);
  assert.match(filler, /replaceAll\("\{legal_name\}", info\.legal_name\)/);
  assert.match(filler, /fill\(info\.operator_template\)/);
  assert.match(filler, /fill\(info\.terms_party_template\)/);

  const legalPage = read("app/legal/page.tsx");
  assert.match(legalPage, /operatorLine/);
  assert.match(legalPage, /termsParty/);
  assert.match(legalPage, /legal\.governing_law/);
  assert.match(legalPage, /legal\.contact/);
  assert.match(read("components/Footer.tsx"), /legal\.brand/);
  assert.match(read("app/layout.tsx"), /legal\.brand/);
  assert.match(read("lib/site.ts"), /contact:\s*legal\.contact/);

  assert.deepEqual(hits(`Agreement with ${info.legal_name}.`), ["legal_name"]);
  assert.deepEqual(hits(`Courts in ${info.governing_law}.`), ["governing_law"]);
});

test("the owner string is not hard-coded outside the shared setting", () => {
  const offenders = [];
  for (const path of repoFiles()) {
    const rel = relative(repoRoot, path).split(sep).join("/");
    if (rel === SETTING) continue;
    let text;
    try {
      text = readFileSync(path, "utf8");
    } catch {
      continue;
    }
    if (text.includes("\u0000")) continue;
    const found = hits(text);
    if (found.length) offenders.push(`${rel} (${found.join(", ")})`);
  }
  assert.deepEqual(
    offenders,
    [],
    `hard-coded legal operator outside ${SETTING}:\n${offenders.join("\n")}`,
  );
});
