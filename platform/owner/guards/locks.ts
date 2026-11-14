/** The owner's migration lock (`ci/kit/locks.ts`): its object's migrations, pinned in `migrations.json`. */
import { MIGRATIONS } from "../src/schema.ts";

export const LOCKS = Object.freeze({ "owner/migrations.json": MIGRATIONS });
