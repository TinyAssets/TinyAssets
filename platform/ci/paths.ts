/** Where the repository and the platform are, for every test, guard, config and tool. */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** The repository root. */
export const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
/** The platform workspace root (`platform/`). */
export const PLATFORM = join(REPO, "platform");
