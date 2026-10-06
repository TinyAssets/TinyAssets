# App byte-intake shape disposition

Fable 21633 reviewed 6f5e6c7f read-only and returned ADAPT, exit 0, 364 seconds.
Full output is preserved in app-byte-intake-shape-review.md. Root accepted the
bounded shape and authorized implementation after these corrections.

- Reslice actual ASGI body messages to CHUNK_BYTES before the two-slot bridge.
- Retain the established whole-copy shared maintenance barrier for this MVP;
  add 10-second idle and 120-second total upload deadlines. Do not claim this
  eliminates starvation or shorten the barrier without fresh safety proof.
- Bound upload workers to four per process with immediate 503 before reading
  bytes or allocating a worker when full; reuse existing worker infrastructure.
- Allow the existing request-Host or configured-public-resource origin set,
  requiring exact scheme/authority and no path/query/fragment, octet content
  type and custom header. Leave the existing JSON helper unchanged.
- Add current-home validation INSIDE the final author write fence; existing
  admin/tombstone checks alone do not imply this.
- Root makes in-flight owner/home scope correction required: preserve prior
  records safely and never show/reoffer another account's filenames/message.
- Expiry is wrapper metadata beside files, not a seventh immutable reference
  field. Document verbatim reference copying in the normal agent guide only.
- Prior rollback wording verified separately (Q5); this does not turn the old
  runtime review into review of upload code. Final code review, CI, deployment
  and ordinary rendered app acceptance remain mandatory.

No new durable queue, object store, schema, provider or private workflow. The
one-hour unbound and nonexpiring-bound lifecycle is unchanged. Complete readiness,
partial failure, expiry, account-switch and stream-abort tests precede freeze.
