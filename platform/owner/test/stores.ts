/**
 * What a harness's own stores of the object (`ownerStores`, over its own gate) tell of a file
 * change: nothing, loudly. Only the object's own stores tell the owner's other modules
 * (`fileChanged`, over its whole state), and only its doors change a file; a test or the
 * memory leg reaches the ledger, the placed bodies and the manifest's lane, which change none.
 */
import type { Changed } from "../src/files";

export const NO_FILE_CHANGE: Changed = (path) => {
  throw new Error(`a test's stores change no file: ${path} is the object's doors' to change`);
};
