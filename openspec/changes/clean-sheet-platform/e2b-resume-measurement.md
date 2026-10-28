# E2B pause/resume latency, 1 GiB sandbox — 2026-10-28

For `openspec/changes/clean-sheet-platform/tasks.md` §1 (ADR-032). Script:
`scripts/e2b_resume_probe.py`.

## Verdict

**Pass: E2B stays.** Resume to the first byte of `echo ok` came in at **p50 247 ms,
p95 706 ms** (p99 849 ms, max 895 ms) over 40 cycles. The threshold for switching
to Cloudflare Containers is a p95 above 1.5 s. Every cycle restored the full state:
the process was alive, the tick counter was frozen while paused, and the file
checksum matched.

Caveat: resumes after a 5-minute wait were about 3× slower than after 10 s or 60 s
(p50 740 ms against about 250 ms; only n=4). Idle periods of hours or days were
not measured, so that tail is the open question. Owner sandboxes will spend most
of their time in exactly that state.

## Setup

| | |
|---|---|
| SDK | Python `e2b==2.61.0` (PyPI), Python 3.13.17 |
| Template | throwaway, built with `Template.build(Template().from_base_image(), "tinyassets-resume-probe-1g", cpu_count=2, memory_mb=1024)` (about 10–50 s build); deleted after the run (HTTP 204). The default template is 2 vCPU / **512 MiB**, so a custom template is required for 1 GiB. |
| Sandbox | 2 vCPU, 1024 MiB (`get_info`); `MemTotal` 999,748 kB inside |
| State | 206 MB of files (20 × 10 MB from `/dev/urandom`, plus 5 MB written via `files.write`), and a background Python process holding 150 MiB of touched memory that writes a tick counter every second |
| Region | default managed cloud (`e2b.app`); the sandbox's egress IP geolocates to Oregon (ipinfo.io, queried from inside a sandbox). No region was selected. |
| Client | Anthropic-managed cloud container (Linux), egress through an HTTPS proxy. Geo-IP lookups from the client are blocked by that proxy, so the location is unknown. Full TLS request to `api.e2b.app/health`: 220–480 ms. Every latency below includes this client→E2B path. |
| Run | 2026-10-28 03:28–04:12 UTC; waits 16 × 10 s, 20 × 60 s, 4 × 300 s, shuffled (seed 28) |

How each value was measured (`time.perf_counter`, client side):
- **pause**: duration of `sbx.pause()`. `get_info` then reported `paused` every time (an untimed check). No `ServiceBusyException` retries were needed.
- **resume call**: `Sandbox.connect(id, timeout=900)` until it returns (the SDK's resume).
- **resume → first byte**: from the start of `connect` until the `on_stdout` callback of `commands.run("echo ok")` fires on the new handle. This is the number the plan cares about.
- **cold create**: `Sandbox.create(template)`, and from the start of `create` to the first byte of the same `echo ok`.
- Percentiles use linear interpolation (numpy's default method).

## Results (ms)

| metric | n | p50 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|
| resume_to_first_output | 40 | 247 | 706 | 849 | 895 | 335 |
| resume | 40 | 158 | 434 | 520 | 555 | 216 |
| pause | 40 | 185 | 574 | 699 | 705 | 237 |
| cold_create | 10 | 456 | 635 | 685 | 698 | 478 |
| cold_create_to_first_output | 10 | 569 | 766 | 823 | 837 | 601 |
| resume_to_first_output @ wait 10 | 16 | 251 | 472 | 495 | 501 | 306 |
| resume_to_first_output @ wait 60 | 20 | 246 | 434 | 501 | 518 | 273 |
| resume_to_first_output @ wait 300 | 4 | 740 | 877 | 891 | 895 | 762 |

Pause measured 185 ms p50 / 705 ms max, much faster than the "about 4 s per 1 GiB
of RAM" the docs quote. Either the call returns before the snapshot is fully
written, or a 1 GiB sandbox whose memory is mostly untouched (the holder touches
150 MiB; total memory in use was not recorded) snapshots quickly. Resume itself
matched the docs ("about 1 second") with room to spare. A resumed sandbox reaches
its first byte about 2.3× faster than a cold create (p50 247 ms against 569 ms).

## E2B facts (docs and pricing, read 2026-10-28)

| Fact | Value | Source |
|---|---|---|
| Paused-storage price | No charge. "You only pay while a sandbox is actively running"; "Paused and killed sandboxes are not billed." No storage line item on any page. | https://docs.e2b.dev/billing, https://docs.e2b.dev/faq/calculate-sandbox-price.md, https://e2b.dev/pricing (its Storage table lists disk as "10 Hobby Free / 20 Pro Free") |
| Paused retention | Kept indefinitely, with no TTL and no auto-delete; removed only by an explicit kill | https://docs.e2b.dev/sandbox/persistence |
| Paused vs. concurrency | Paused sandboxes do not count toward the concurrency limit | https://docs.e2b.dev/faq/paused-sandboxes-concurrency.md |
| Concurrency | Hobby 20; Pro 100, plus add-ons of +500 for +$500/mo or +1,000 for +$1,000/mo (so 600 at $650/mo or 1,100 at $1,150/mo including the $150 base); Enterprise 1,100+ | https://docs.e2b.dev/billing, https://e2b.dev/pricing |
| Plans | Hobby $0 (one-time $100 credit); Pro $150/mo + usage; Enterprise custom ($3,000/mo minimum) | https://e2b.dev/pricing |
| Usage price | $0.000014 per vCPU-second, $0.0000045 per GiB-second, so 2 vCPU + 1 GiB = $0.117 per running hour | https://e2b.dev/pricing |
| Max session length | Continuous runtime: 1 h Hobby, 24 h Pro, custom Enterprise. **Resets on every pause/resume.** | https://docs.e2b.dev/sandbox, https://docs.e2b.dev/faq/sandbox-lifetime.md |
| Pause/resume plan requirement | No doc restricts it to a paid plan. The persistence and billing pages describe it with no plan qualifier, and the lifetime FAQ gives its runtime reset for Hobby (1 h) and Pro alike. Which plan this account is on was not checked. | https://docs.e2b.dev/sandbox/persistence, https://docs.e2b.dev/billing |
| Regions | Several regions, each a separate deployment, chosen per project via support with `E2B_DOMAIN`; the docs don't list them. A shared EU cluster is available on every plan once a payment method is added (ask support). The default lands in the US (Oregon, measured above). | https://docs.e2b.dev/faq/migrate-between-regions.md, https://docs.e2b.dev/faq/eu-region.md |
| Sizes | 1–8 vCPU and up to 8 GiB RAM (default template 512 MiB; more on Pro via support), set per template (`cpuCount`/`memoryMB`); disk 10 GiB Hobby / 20 GiB Pro | https://e2b.dev/pricing, https://docs.e2b.dev/billing |
| Resume semantics | `connect` resumes a paused sandbox and only *extends* the timeout. Pause can return 503 (`ServiceBusyException`) while a previous snapshot finishes; auto-pause on timeout via `on_timeout="pause"`. | https://docs.e2b.dev/sandbox/persistence |

## Cost and cleanup

Running time across all runs (smoke tests plus the full run) was a few minutes; the
sandbox was paused for most of the 44-minute run. An upper bound that bills the
whole 44 minutes at 2 vCPU + 1 GiB is about $0.09. The total spend is under
$0.15 (estimated from the published rates; the E2B console bill was not visible
from this environment). After the run, the list API (running plus paused, whole
account) returned **0 sandboxes**, and `GET /templates` returned no custom templates.

## Raw timings

Pause/resume cycles (`tick` is the background process's counter after resume; it
advances only while the sandbox runs):

| # | wait (s) | pause (ms) | resume call (ms) | resume → first byte (ms) | state after pause | process | sha | tick |
|---|---|---|---|---|---|---|---|---|
| 1 | 60 | 206 | 141 | 225 | paused | alive | sha-ok | 4 |
| 2 | 60 | 705 | 158 | 244 | paused | alive | sha-ok | 4 |
| 3 | 60 | 187 | 249 | 346 | paused | alive | sha-ok | 5 |
| 4 | 10 | 340 | 144 | 232 | paused | alive | sha-ok | 5 |
| 5 | 10 | 168 | 148 | 383 | paused | alive | sha-ok | 6 |
| 6 | 60 | 181 | 165 | 253 | paused | alive | sha-ok | 6 |
| 7 | 60 | 189 | 144 | 234 | paused | alive | sha-ok | 7 |
| 8 | 60 | 181 | 149 | 237 | paused | alive | sha-ok | 7 |
| 9 | 60 | 187 | 157 | 245 | paused | alive | sha-ok | 8 |
| 10 | 10 | 170 | 142 | 231 | paused | alive | sha-ok | 8 |
| 11 | 300 | 176 | 465 | 778 | paused | alive | sha-ok | 9 |
| 12 | 60 | 178 | 343 | 430 | paused | alive | sha-ok | 9 |
| 13 | 60 | 261 | 160 | 251 | paused | alive | sha-ok | 10 |
| 14 | 60 | 303 | 200 | 288 | paused | alive | sha-ok | 10 |
| 15 | 300 | 171 | 376 | 703 | paused | alive | sha-ok | 11 |
| 16 | 10 | 191 | 151 | 382 | paused | alive | sha-ok | 11 |
| 17 | 60 | 182 | 150 | 248 | paused | alive | sha-ok | 12 |
| 18 | 10 | 173 | 144 | 231 | paused | alive | sha-ok | 12 |
| 19 | 10 | 175 | 161 | 247 | paused | alive | sha-ok | 12 |
| 20 | 300 | 181 | 368 | 673 | paused | alive | sha-ok | 13 |
| 21 | 60 | 178 | 158 | 246 | paused | alive | sha-ok | 13 |
| 22 | 10 | 188 | 158 | 245 | paused | alive | sha-ok | 14 |
| 23 | 60 | 177 | 154 | 241 | paused | alive | sha-ok | 14 |
| 24 | 60 | 403 | 159 | 247 | paused | alive | sha-ok | 15 |
| 25 | 60 | 186 | 153 | 239 | paused | alive | sha-ok | 15 |
| 26 | 60 | 173 | 150 | 237 | paused | alive | sha-ok | 15 |
| 27 | 60 | 200 | 159 | 246 | paused | alive | sha-ok | 16 |
| 28 | 10 | 191 | 153 | 241 | paused | alive | sha-ok | 16 |
| 29 | 10 | 568 | 410 | 501 | paused | alive | sha-ok | 17 |
| 30 | 10 | 183 | 168 | 255 | paused | alive | sha-ok | 17 |
| 31 | 10 | 191 | 151 | 238 | paused | alive | sha-ok | 18 |
| 32 | 10 | 172 | 154 | 241 | paused | alive | sha-ok | 18 |
| 33 | 60 | 181 | 165 | 252 | paused | alive | sha-ok | 18 |
| 34 | 60 | 171 | 154 | 241 | paused | alive | sha-ok | 19 |
| 35 | 300 | 691 | 555 | 895 | paused | alive | sha-ok | 20 |
| 36 | 10 | 169 | 176 | 274 | paused | alive | sha-ok | 20 |
| 37 | 10 | 219 | 374 | 462 | paused | alive | sha-ok | 20 |
| 38 | 60 | 210 | 433 | 518 | paused | alive | sha-ok | 21 |
| 39 | 10 | 346 | 354 | 440 | paused | alive | sha-ok | 22 |
| 40 | 10 | 181 | 204 | 292 | paused | alive | sha-ok | 22 |

Cold creates:

| # | create call (ms) | create → first byte (ms) |
|---|---|---|
| 1 | 698 | 837 |
| 2 | 392 | 492 |
| 3 | 475 | 668 |
| 4 | 455 | 564 |
| 5 | 436 | 550 |
| 6 | 434 | 548 |
| 7 | 410 | 522 |
| 8 | 557 | 679 |
| 9 | 462 | 578 |
| 10 | 456 | 574 |

