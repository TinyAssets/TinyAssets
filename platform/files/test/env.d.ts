// The files Worker's bucket, as its generated env types it (`src/env.ts`).
declare namespace Cloudflare {
  interface Env extends Pick<import("../src/env").FilesEnv, "BLOBS"> {}
}
