// The files Worker's one binding, as its wrangler.jsonc declares it.
declare namespace Cloudflare {
  interface Env {
    BLOBS: R2Bucket;
  }
}
