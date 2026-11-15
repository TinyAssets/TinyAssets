/** The doors' recorders, on the doors' own workerd project (`npm run record`, `ci/record.ts`). */
import { recording } from "../../../ci/project";
import config from "../../vitest.config";

export default recording(config);
