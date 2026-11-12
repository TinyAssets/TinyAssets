// The files Worker's bucket and cipher, as its generated env types them (`src/env.ts`).
declare namespace Cloudflare {
  interface Env extends Pick<import("../src/env").FilesEnv, "BLOBS" | "FILES_CIPHER"> {}
}
