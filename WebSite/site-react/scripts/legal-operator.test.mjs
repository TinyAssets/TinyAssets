import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

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

// Pieces stay split so this file does not itself contain a forbidden phrase.
const FORBIDDEN = [
  "Sim" + "kal",
  "Holdings " + "LLC",
  "trade name " + "of",
  "doing business " + "as",
  "{" + "legal_name}",
];

function read(path) {
  return readFileSync(resolve(siteRoot, path), "utf8");
}

function siteFiles() {
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
  walk(siteRoot);
  return found;
}

function hits(text) {
  const lower = text.toLowerCase();
  return FORBIDDEN.filter((needle) => lower.includes(needle.toLowerCase()));
}

const info = JSON.parse(read("lib/legal-info.json"));

test("the public legal record names TinyAssets only", () => {
  assert.equal(info.brand, "TinyAssets");
  assert.equal(info.contact, "info@tinyassets.io");
  assert.equal(info.governing_law, "Washington");
  assert.equal("legal_name" in info, false);
  assert.equal("operator_template" in info, false);
  assert.equal("terms_party_template" in info, false);

  const legalPage = read("app/legal/page.tsx");
  assert.match(legalPage, /agreement between you and \{legal\.brand\}/);
  assert.match(legalPage, /Privacy policy and terms of service for \$\{legal\.brand\}/);
  assert.match(legalPage, /legal\.governing_law/);
  assert.match(legalPage, /legal\.contact/);
  assert.match(read("components/Footer.tsx"), /legal\.brand/);
  assert.match(read("app/layout.tsx"), /legal\.brand/);
  assert.match(read("lib/site.ts"), /contact:\s*legal\.contact/);

  assert.deepEqual(hits(`seen ${FORBIDDEN[0]} here`), [FORBIDDEN[0]]);
});

test("no legal-entity name appears under the site", () => {
  const offenders = [];
  for (const path of siteFiles()) {
    const rel = relative(siteRoot, path).split(sep).join("/");
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
  assert.deepEqual(offenders, [], `legal-entity wording under the site:\n${offenders.join("\n")}`);
});
