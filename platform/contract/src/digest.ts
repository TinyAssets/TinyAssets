/** The platform's one content digest: SHA-256, as lowercase hex. */
import { createHash } from "node:crypto";

/** The SHA-256 of `data` (a string as its UTF-8 bytes), as 64 lowercase hex. */
export function sha256Hex(data: string | Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}
