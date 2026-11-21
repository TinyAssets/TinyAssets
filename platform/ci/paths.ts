/** Where the repository and the platform are, for every test, guard, config and tool. */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const PLATFORM = join(REPO, "platform");
