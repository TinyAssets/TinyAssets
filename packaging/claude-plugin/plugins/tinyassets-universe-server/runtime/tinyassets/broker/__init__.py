"""The credential broker: one long-lived process, many streams (S6, I14).

Contract: ``openspec/changes/broker-streaming-contract``. This package holds
the pieces the broker process is built from, each testable on its own:

* :mod:`.scan` -- the incremental scan that keeps every held sensitive value
  out of a streamed response, raw and decoded;
* :mod:`.ops` -- the durable, namespaced ``op_id`` record that keeps a request
  from ever being sent twice;
* :mod:`.fence` -- the owner-generation fence, authorized by a lease proof.

The wire framing it shares with ``boxhostd`` is :mod:`tinyassets.rpc_frames`.
"""
