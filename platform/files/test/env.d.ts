// The files Worker's bucket and cipher, as its wrangler.jsonc declares them.
declare namespace Cloudflare {
  interface Env {
    BLOBS: R2Bucket;
    FILES_CIPHER: string;
  }
}
