import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const tokenInfoPath = join(dirname(fileURLToPath(import.meta.url)), "token-info.json");

export function readTokenInfo() {
  return JSON.parse(readFileSync(tokenInfoPath, "utf8"));
}

/** The primary contract address, read from token-info.json. No second copy. */
export function primaryContractAddress(info = readTokenInfo()) {
  const primary = info.deploys?.find((deploy) => deploy.primary === true);
  if (!primary || typeof primary.address_main !== "string" || primary.address_main.length === 0) {
    throw new Error("token-info.json has no primary address_main");
  }
  return primary.address_main;
}
