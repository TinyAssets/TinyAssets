# Releasing an observed sandbox wait

An owner can be idle when it asks to leave and have a new waiting run by the time Slots
receives the request. At `287dd097` an ordinary Workerd regression lost the newer
turn. When a peer refreshed instead, the same idle leave correctly advanced the line.

## What identifies a wait

Two local Slots objects on `1b98974a` each registered A, then B. One registered A
again and the other registered B again. After eviction the five application tables
matched byte for byte when serialized in table and row order. Both queues still had
A at position 1 and B at position 2, with the next position counter at 4.
Neither the FIFO position nor the queue-wide counter identified who had refreshed.

At `b2ef470c` a repeat wait takes a fresh value from the existing allocator while
keeping the owner's position and backoff. An apparently idle Owner reads the value,
rechecks its runs and releases that registration conditionally at Slots. A normal
wake of a marked wait still goes straight to readmission. Erasure can remove the
owner irrespective of its registration.

## Verified behavior

- In six three-Worker cases, the same owner kept a refreshed turn, a peer's refresh
  allowed the idle head to leave, both start-reply orders launched the waiting run
  once, and cancellation advanced the next owner both warm and after eviction.
- Standalone, 56 Owner tests passed. The sandbox suite had 167 passes and the prior
  successful-placement notification failure. The combined candidate had eight focused
  Owner passes and 214 sandbox passes. The 15 failing sandbox test names matched
  the previous combined run exactly. Both full platform typechecks passed.
- A first-schema fixture with two queued owners, an outstanding reservation and
  prior backoff survived migration and eviction. Old registrations could neither
  remove a refreshed wait nor remove the owner after it left and rejoined.

## Mixed versions and remaining scope

The ring still carries just the owner. A real Doors consumer acknowledged and dropped
an experimental ring carrying an extra registration field. Its ordinary message
reached Owner. Changing the shared erasure message parser would reach a second queue.

A new Owner with the old Sandbox woke a marked wait. When later idle, its registration
read failed and the ring consumer requested retry. The head retained its place.
In the opposite combination an older Owner still issued its original unconditional
leave. It lost the same owner's refreshed wait, while a peer refresh advanced correctly.
Old Slots code refuses the newer on-disk schema. These are coordinator integration
constraints. The idle-leave change does not recover registrations already lost.

A duplicate ring before readmission and a successful placement while another start
reply is in flight require separate proofs. Both can occur without any newer
registration arriving between the idle decision and deletion.
