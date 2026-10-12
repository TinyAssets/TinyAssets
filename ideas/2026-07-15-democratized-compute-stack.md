# Democratized Compute Stack: From Harness to Lithography

**Captured:** 2026-07-15  
**Source:** host design chat  
**Status:** host-approved direction and hard `$0` MVP constraint;
resource-gated scale design requires approval before build  
**Specification:** `2026-07-15-riscv-fpga-vertical-proof.md (archived in git)`
**Review gate:** Before implementation, a Claude-family reviewer must re-check
the external sources and compare the proposal against current TinyAssets
architecture.

## Problem Statement

How might a user's chatbot and daemon-controlled Branches take a computing
system from intent through harness, model, computer, board/card, chip, and
lithography, while making the connector conversation the actual user build
surface, making every layer independently remixable, and making "created" mean
physically verified rather than merely producing plausible design files?

## Recommended Direction

Treat full-stack creation as a hierarchy of ordinary, remixable Branches bound
to a Goal, not as new platform-specific hardware primitives. The chatbot is the
control station: it captures intent, approves budgets and irreversible actions,
and presents evidence. Daemons do the work: generate and test artifacts, invoke
EDA/CAD/training tools, procure market capacity, control authorized machines,
read instruments, and revise from measured failures.

Define four distinct claim levels:

1. **Designed:** source artifacts and deterministic checks pass.
2. **Vendor-built:** an outside fab/assembler produced the artifact and physical
   acceptance tests pass.
3. **Locally built:** user-owned machines produced it and metrology confirms it.
4. **Recursively self-hosted:** the resulting stack can reproduce a defined
   subset of the machines, components, and software needed for its next version.

The platform can support claim levels 1-3 across the whole stack today, with
capability falling sharply at semiconductor fabrication. Level 4 must remain a
measured long-horizon Goal, not a present-tense product claim.

Treat each result as a graph of versioned design units—not one monolithic
project archive. Behavior, model, accelerator, SoC/gateware, board, firmware,
mechanical, evaluator, and evidence units carry interfaces, compatibility,
licenses, attribution, upstream lineage, build commands, and physical proof.
This lets another user discover a passing design in their chatbot, remix only
one layer, rebuild its affected dependency cone, and contribute a tested
descendant without erasing the ancestor.

"User-designed" is participatory and must remain truthful: the user authors
intent and material decisions; daemons are credited for generated artifacts;
upstreams retain source credit/licenses; vendors and paid executors retain
production credit. Ownership and approval do not rewrite authorship.

**Host model-foundry principle, 2026-07-15:** Full-stack democratization must
include arbitrary user-defined model algorithms and from-scratch training, not
only prompting or fine-tuning known architectures. Through the chatbot, a user
may theoretically pursue a new LLM, world model, learning algorithm, or AGI
research program by acquiring lawful compute, storage, data, and specialist
work at market rates. TinyAssets must remain architecture- and scale-agnostic;
it orchestrates code, resources, budgets, checkpoints, evaluators, lineage, and
physical outcomes without promising scientific success or unrestricted access.
Detailed proof ladder and cost envelope:
`ideas/2026-07-15-user-built-model-foundry.md`.

**Budget and resource reframe, 2026-07-15:** MVP cash spending is strictly
`$0`. MVP-0 therefore uses only already-available compute and accounts: a
connector-built dual-screen cookbook simulator plus a micro-model trained from
random initialization on local CPU/GPU. The same Branch may compile a larger
immutable run, obtain timestamped market quotes, enforce the lowest applicable
Universe/Goal/run cap, expose the exact shortfall, and publish a `$0` or below-
market contribution request. A scaled-down distributed canary proves the real
shard/checkpoint/recovery/evaluation path. The unexecuted target run remains
**scale-ready / resource-blocked**, never scale-proven. Paid or voluntarily
contributed capacity can advance the unchanged manifest through later outcome
gates; displays, PCB, enclosure, chip, and lithography remain explicit funded
or contributed descendants, not hidden assumptions in “minimum.”

## Feasibility Boundary

| Layer | Realistic branch outcome | Hard external boundary |
|---|---|---|
| Harness/software | Source, tests, packaging, deploy, monitoring | Credentials and production effects |
| LLM | Data pipeline, architecture, distributed training/fine-tuning, eval, serving | Compute supply, lawful data, frontier-scale cost and expertise |
| Desktop/board/card | Schematics, PCB, BOM, enclosure, firmware, assembly, bring-up | Components, PCB fabrication/assembly, electrical lab |
| Chip | ISA/RTL, verification, physical layout, mature-node tapeout, package/test | PDK/foundry access, packaging, yield, proprietary IP |
| Lithography/process | Machine design/control, recipes, calibration, micron-scale device experiments | Precision optics, vacuum, chemicals, deposition/doping, clean environment, metrology |
| Leading-edge recursive stack | Research roadmap and evidence ledger | Industrial supply chain; advanced DUV/EUV and leading-edge fabs are not hobby-scale capabilities |

Open tooling already reaches meaningful seams: KiCad exposes automatable PCB
checks and fabrication outputs; OpenROAD targets autonomous RTL-to-GDSII;
public mature-node PDKs and multi-project wafer services enable real test chips;
low-cost open maskless steppers and garage fabs demonstrate micron-scale
fabrication. These demonstrations do not imply a hobbyist can reproduce a
modern GPU, leading-edge CPU, or EUV scanner.

## Physical Outcome Gates

- Harness: a clean machine installs, runs, recovers, and passes user-path tests.
- LLM: held-out evaluations, cost/latency limits, and reproducible training
  receipts pass.
- Board/card: ERC/DRC passes, manufactured board powers safely, interfaces
  operate, and instrument traces are attached.
- Chip: signoff checks pass, packaged silicon is returned, and bench tests match
  the declared function and operating envelope.
- Lithography: measured linewidth, overlay, defect rate, electrical device
  behavior, and repeatability pass.
- Complete computer: it boots an open software stack and runs a workload on the
  branch-produced hardware.
- Recursive claim: the completed system successfully produces a defined next
  artifact using its own prior outputs; purchased inputs remain explicitly
  enumerated.

No branch may promote itself from simulated to physical based on model judgment.
Metrology and independent evidence own that transition.

## Key Assumptions to Validate

- "All the DIY machines" includes calibrated process tools, metrology, safety
  systems, consumables, and operators/robots, not merely machine ownership.
- Market-rate compute is reliably purchasable, but compute money does not grant
  proprietary PDK/IP rights, foundry slots, controlled equipment, or material
  purity.
- Users value reproducible design/control sovereignty even when early
  fabrication is vendor-mediated.
- Existing TinyAssets external-tool, capability-registry, market, Brain,
  evaluator, and Goal/gate primitives can compose the workflow without a new
  hardware-specific MCP surface.
- Qualified human specialists can participate as paid work executors at safety,
  legal, tapeout, and process boundaries.

## MVP Scope

Prove one intentionally modest vertical slice: a daemon-designed RISC-V-based
computer or accelerator using an FPGA or mature-node shuttle chip, a custom PCB,
open firmware/software, and a small locally trained or fine-tuned model. Require
real boot, electrical, and workload evidence. Keep local wafer fabrication as a
parallel research Branch until it can beat the vendor-built path on a concrete
gate.

**Host approval, 2026-07-15:** Start with this vertical proof. For the first
specification, prefer the FPGA accelerator path; keep a mature-node custom ASIC
as the next physical proof after the FPGA system passes its boot and workload
gates.

**Specification drafted, 2026-07-15:** The v0 proposal uses a RISC-V soft CPU,
custom function unit, quantized keyword-spotting workload, replaceable
ECP5-class FPGA module, and custom carrier PCB. It awaits host review before a
technical plan or implementation tasks are created.

**Host clarification, 2026-07-15:** The proof must start from a real user's
rendered chatbot conversation with the live connector and remain steerable
there through physical acceptance. A developer-built repository followed by a
chatbot summary does not count. Proof v0 is now an offline voice-command module
whose behavior, model, accelerator, SoC, PCB, firmware, and tests are published
as separately remixable units. Completion includes a second user's
connector-driven descendant that runs on physical hardware.

**Candidate pivot, 2026-07-15:** The host proposed a page-free, book-like
conversational cookbook with two screens, recipe discovery, live cooking
coaching, and substitutions as the first useful physical artifact. The prior
voice-command/FPGA spec is paused as the default device while this product is
refined. The cookbook retains the connector-first build proof and independently
remixable hardware/software/recipe units; see
`ideas/2026-07-15-conversational-cookbook-device.md`.

## Not Doing

- No claim that chat alone manufactures matter.
- No attempt to start with a modern GPU, frontier LLM, or EUV scanner.
- No new platform primitive for each machine, EDA tool, or manufacturing step;
  compose them through capability adapters and ordinary Branches.
- No autonomous handling of hazardous chemistry, high energy, expensive
  tapeouts, or regulated procurement without explicit authority and interlocks.
- No "end-to-end" completion based only on simulations, renderings, GDS files,
  or purchase receipts.
- No "scale-proven" completion based only on quotes, scaling estimates,
  emulation, or an unfilled compute-contribution listing.
- No claim of supply-chain independence while purchased chips, optics,
  chemicals, wafers, or machine controllers remain required.

## Open Questions

1. May the durable build Goal span multiple resumable chatbot sessions, or must
   the proof occur in one literally uninterrupted chat window? Recommended:
   one durable Goal spanning resumable conversations.
2. Is the intended north star **design sovereignty**, **local manufacturing
   sovereignty**, or **recursive industrial sovereignty**? Each is a different
   Goal and gate ladder.
3. Does "cards" mean PCB add-in cards generally, or specifically GPU/AI
   accelerator cards?
4. What is the first computer worth building: an educational 8/32-bit machine,
   a useful RISC-V Linux system, or an FPGA accelerator attached to a commercial
   host?
5. Which irreversible actions always require founder/user confirmation, and
   which may be pre-authorized by bounded budget and machine policy?
6. Which purchased inputs are acceptable in the first recursive claim?

## Initial Primary Sources

- OpenROAD documentation: <https://openroad.readthedocs.io/en/latest/>
- SkyWater SKY130 PDK status: <https://skywater-pdk.readthedocs.io/en/main/>
- Tiny Tapeout chips and real shuttle runs: <https://tinytapeout.com/chips/>
- KiCad command-line automation: <https://docs.kicad.org/master/en/cli/cli.html>
- RISC-V ratified specifications: <https://docs.riscv.org/reference/home/index.html>
- Low-cost open maskless stepper paper: <https://arxiv.org/abs/2510.15082>
- Sam Zeloof's home chip fab record: <https://sam.zeloof.xyz/>
- ASML lithography principles: <https://www.asml.com/en/technology/lithography-principles>
- DeepSeek-V3 training report: <https://arxiv.org/abs/2412.19437>
