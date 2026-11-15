/** The platform's one content digest: SHA-256. */
import { createHash, type Hash } from "node:crypto";

/** The SHA-256 of `data` (a string as its UTF-8 bytes), as 64 lowercase hex. */
export function sha256Hex(data: string | Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

/** The SHA-256 of `data` (a string as its UTF-8 bytes): its 32 bytes. */
export function sha256(data: string | Uint8Array): Buffer {
  return createHash("sha256").update(data).digest();
}

/** A SHA-256 over data that arrives in parts: each `update`d in order, then one `digest`. */
export function sha256Stream(): Hash {
  return createHash("sha256");
}
