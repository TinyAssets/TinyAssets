import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { primaryContractAddress, readTokenInfo } from "../lib/token-contract.mjs";

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hex = /0x[a-fA-F0-9]{40}/;

function source(path) {
  return readFileSync(resolve(siteRoot, path), "utf8");
}

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if ([".next", "node_modules", "out"].includes(entry.name)) return [];
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    if (!/\.(?:[cm]?[jt]sx?|mjs|css|md|txt|xml)$/.test(entry.name)) return [];
    return [path];
  });
}

test("the published token address is the primary address in token-info.json", () => {
  const info = readTokenInfo();
  const primary = info.deploys.find((deploy) => deploy.primary === true);
  assert.ok(primary, "token-info.json names one primary deploy");
  assert.equal(primaryContractAddress(), primary.address_main);
  assert.equal(primaryContractAddress(info), primary.address_main);

  const contracts = source("components/TokenContracts.tsx");
  assert.match(contracts, /token-info\.json/);
  assert.match(contracts, /deploy\.address_main/);
  assert.match(contracts, /deploy\.address_others/);
  assert.doesNotMatch(contracts, hex);

  for (const file of ["app/legal/page.tsx", "app/token/page.tsx", "components/CopyAddress.tsx"]) {
    const body = source(file);
    assert.doesNotMatch(body, hex, `${file} must not retype a contract address`);
  }
  assert.match(source("app/token/page.tsx"), /token-info\.json/);
  assert.match(source("app/legal/page.tsx"), /TokenContracts/);
  assert.match(source("components/CopyAddress.tsx"), /writeText\(address\)/);
});

test("site source does not repeat a contract address outside token-info.json", () => {
  const info = JSON.parse(source("lib/token-info.json"));
  const allowed = info.deploys.flatMap((deploy) => [deploy.address_main, deploy.address_others].filter(Boolean));
  assert.ok(allowed.includes(primaryContractAddress(info)));

  for (const path of sourceFiles(siteRoot)) {
    const rel = relative(siteRoot, path);
    if (rel === "lib/token-info.json") continue;
    const found = readFileSync(path, "utf8").match(new RegExp(hex.source, "g")) ?? [];
    assert.deepEqual(found, [], `${rel} retypes a contract address; the only source is lib/token-info.json`);
  }
});
