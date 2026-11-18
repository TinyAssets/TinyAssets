// BLOBS is the real binding. COPIED_BLOBS is a separate bucket in the local test pool.
declare namespace Cloudflare {
  interface Env extends Pick<import("../src/env").FilesEnv, "BLOBS"> {
    COPIED_BLOBS: R2Bucket;
  }
}
