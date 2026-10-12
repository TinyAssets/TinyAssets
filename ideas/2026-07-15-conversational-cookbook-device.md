# Conversational Cookbook: A Two-Screen Kitchen Book

**Captured:** 2026-07-15  
**Source:** host design conversation  
**Status:** host-approved hard `$0` constraint; zero-purchase experience and
resource-gated scale design under review; physical device preserved as a later
gate  
**Implementation authority:** none until the host approves a design  
**Research review gate:** a Claude-family reviewer must independently re-check
the local-model sources and TinyAssets composition before implementation  
**Parent idea:** `ideas/2026-07-15-democratized-compute-stack.md`  
**Prior candidate:** `2026-07-15-riscv-fpga-vertical-proof.md (archived in git)`

## Problem Statement

How might a user build a dedicated, page-free cookbook through an ordinary
chatbot conversation with the TinyAssets connector, then talk directly to the
finished book while it finds recipes, tracks cooking state, coaches each step,
and proposes safe, explainable substitutions—without forcing messy hands back
onto a phone?

The product has two connected conversations:

1. **Build conversation:** the user's existing chatbot and TinyAssets connector
   design, source, test, manufacture, and physically accept the appliance.
2. **Cooking conversation:** the finished appliance listens and speaks while
   its two screens preserve the recipe's context and current state.

## Target User Decision

**Host approval, 2026-07-15:** Design first for a novice-to-intermediate home
cook who cooks several times per week, follows recipes but often substitutes
what is available, and regularly touches or unlocks a phone with wet or messy
hands.

The core job is:

> When I am cooking an unfamiliar meal, help me keep my place, understand what
> to do next, manage timing, and adapt when an ingredient or tool is missing so
> I can finish confidently without repeatedly touching or scrolling a device.

This target now governs vocabulary, coaching depth, screen hierarchy, recovery
behavior, and acceptance tests. Expert improvisation and accessibility-specific
editions remain downstream remixes rather than co-equal v0 audiences.

## Variation Sweep

Six useful variants were considered before convergence:

1. **Dual interactive LCD book:** two responsive matte touch displays; simplest
   hardware path for active coaching, timers, images, and accessibility.
2. **Paper + action hybrid:** left e-paper screen holds ingredients/overview;
   right LCD handles the active step, timers, speech state, and substitutions.
3. **Dual e-paper heirloom:** extremely book-like and low power, but slower and
   less suitable for dynamic coaching.
4. **Docked washable folio:** displays/hinge detach from a heavier compute and
   audio base, simplifying repair and kitchen placement.
5. **Offline sovereign cookbook:** all speech and cooking intelligence runs
   locally, maximizing privacy but substantially increasing compute, heat,
   model, and cost risk.
6. **Accessibility-first coach:** high contrast, large type, multilingual voice,
   repeat/slow-down controls, and hands-free operation are the central product,
   not secondary settings.

## Converged Directions

### A. Dual-LCD local-first kitchen computer — revised recommendation

**Host approval, 2026-07-15:** Proof v0 uses two matching matte LCD
touchscreens. The e-paper/LCD and dual-e-paper forms remain descendant remix
directions after the cooking interaction is physically validated.

Two approximately 7–8 inch matte displays in a book-like clamshell. The left
screen preserves ingredients, overall progress, and active timers. The right
screen shows the current step, doneness cues, substitutions, and the visible
speech transcript. The finished appliance runs wake word, speech recognition,
recipe retrieval, the cooking language model, substitution reasoning, speech
output, recipe state, and timers locally. Network access may download recipes,
model releases, and user-authorized updates, but it is not an inference
dependency during cooking.

Why first: highest interaction quality with ordinary components, easiest to
test, and the clearest path to a physically useful artifact. Training or
fine-tuning may rent market compute; the resulting model package is installed
and evaluated on the cookbook before it can be released.

### B. Hybrid e-paper/LCD book

Use e-paper for the stable left “page” and LCD for the active right “page.” It
is more distinctive and book-like, but adds display-driver, refresh, color,
power, and layout asymmetry before the conversational job is validated.

### C. From-scratch sovereign cooking model

Pretrain the language-model base weights rather than adapting an inherited open
base model. This is the strongest authorship claim, but it adds dataset scale,
training, tokenizer, model-architecture, and general-language-quality risk.
Preserve it as a later Branch after local inference and user-directed model
adaptation pass physically.

## What “the User Builds the LLM” Means

Proof v0 should make a precise **adapted-model** claim:

1. The chatbot helps the user choose a redistributable open-weight base model
   and records that upstream's authorship, license, and immutable hash.
2. The user defines cooking behavior, recipe/data policy, response style,
   safety boundaries, test conversations, and acceptance thresholds.
3. Daemon Branches assemble licensed training/evaluation data, run
   parameter-efficient fine-tuning on user-owned or rented market compute,
   compare candidates, and quantize the accepted result.
4. The released model unit contains the base-model reference, user/daemon
   adaptation, tokenizer/configuration, recipe retrieval index, evaluator set,
   quantization receipt, attribution, and measured device results.
5. The physical cookbook runs that accepted unit locally with networking
   disabled.

The user therefore builds and owns the cooking-model **derivative and its
behavioral/evaluation system**, not the inherited base weights. The product
must never call this “trained from scratch.” A later Branch can pretrain a
smaller cooking model from scratch on market compute and earn the stronger
claim if it meets the same conversational and safety gates.

**Host expansion, 2026-07-15:** the platform proof may not stop at this adapted
model. A sibling model-foundry Track must demonstrate that the same chatbot can
define arbitrary architecture/training code and train a small model from random
initialization on rented compute. Keep that research proof independent from the
cookbook's product gate so an honest negative result does not make the useful
appliance depend on a weak experimental base model. See
`ideas/2026-07-15-user-built-model-foundry.md`.

Current feasibility supports this direction: `llama.cpp` provides quantized
local LLM inference across broad hardware including RISC-V backends;
`whisper.cpp` provides offline speech recognition; and Hugging Face PEFT/LoRA
supports lightweight portable adaptations that train far fewer parameters than
full-model fine-tuning. Exact models and hardware remain planning decisions,
not assumptions embedded in this idea.

## Recommended Experience

The closed appliance resembles a hardback cookbook. Opening it wakes the two
screens. The user can say:

- “What can I make with chicken, mushrooms, and 35 minutes?”
- “Show me three choices without dairy.”
- “Start cooking the second one.”
- “I do not have cream. What can replace it?”
- “How should the onions look right now?”
- “Repeat that more slowly.”
- “Start an eight-minute timer and move to the next step.”

The left screen is persistent context: title, ingredients, substitutions,
overall step map, and timers. The right screen is working context: one current
step, quantity, technique, doneness cue, next action, speech transcript, and
confidence/warning state. Voice is primary; touch remains a visible fallback
for noisy kitchens and speech errors. A physical microphone-mute control must
have an unambiguous indicator.

Before coaching starts, the system freezes a versioned recipe snapshot. During
the cook it may create proposed substitutions or step adaptations, but it must
show what changed and preserve the original. Afterward the user can record what
worked and optionally publish a descendant recipe Branch.

## Structured Recipe, Not a Page

A recipe design unit is a typed graph rather than prose alone:

- ingredients with quantities, units, preparation, allergens, and optionality;
- equipment and capacity assumptions;
- ordered/dependent steps, active time, passive time, and parallel work;
- timers, temperatures, doneness cues, and food-safety boundaries;
- substitution candidates with purpose, ratio, expected effect, confidence,
  dietary implications, and source/provenance;
- scaling rules and serving/yield assumptions;
- photos or illustrations with licenses;
- outcome reports and descendant lineage.

This structure lets daemons evaluate scaling, missing dependencies, timer
conflicts, allergens, and substitutions instead of improvising from a wall of
text.

## Full-Stack and Remix Boundary

| Design unit | Proof-v0 output | What another user can remix |
|---|---|---|
| Build Branch | connector-driven decisions, budgets, gates, receipts | the appliance-building workflow |
| Product behavior | voice commands, screen roles, privacy and safety policy | the cooking experience |
| Recipe schema | structured ingredient/step/substitution graph | new cuisines and cooking methods |
| Coach policy | prompt/context assembly, confidence and refusal behavior | tone, expertise, accessibility |
| Voice stack | wake word, ASR/TTS adapters, noise tests | language, accent, local/cloud balance |
| Application | dual-screen UI, timers, state machine, offline recovery | layout and interaction |
| Computer | purchased Linux-capable RISC-V/other open compute module | compute-module adapter or later local AI |
| Card/PCB | custom power, dual-display, audio, controls, debug carrier | connectors, audio, sensors, form factor |
| Mechanical | hinge, shells, stand, cable routing, service access | materials, sizes, mounting, repairability |
| Evaluators | recipe, safety, speech, UI, electrical and cook-session tests | stronger gates and domain-specific rubrics |
| Evidence | build receipts, physical measurements, actual cook sessions | trusted upstream proof for descendants |

Proof v0 purchases the displays, compute module, microphones, speaker,
components, and fabrication. It custom-designs the product architecture,
carrier PCB, enclosure/hinge, software, recipe format, cooking coach, tests, and
evidence through the user's Branch. It does not claim the purchased processor
or display panels were user-designed. Later descendants can replace the
compute module with the FPGA accelerator, ASIC, or locally fabricated chip
Branches from the broader democratized-stack Goal.

## Compounding Loop

1. A user builds and physically accepts the cookbook through the connector.
2. The passing appliance units and evidence become discoverable Branches.
3. Another user remixes only one unit—for example a Spanish voice stack, a
   larger-print interface, a new hinge, or a baking-specific evaluator.
4. Compatibility manifests identify which dependent units must be rebuilt.
5. The descendant must pass relevant automated and physical cooking gates.
6. Passing improvements return with lineage, licenses, attribution, outcome
   evidence, and compatibility; ancestors are never silently overwritten.

Recipes compound the same way. A substitution that worked for one cook is an
outcome report, not immediate canon. Repeated diverse successful outcomes plus
evaluator review can promote it into a higher-confidence reusable substitution.

## Zero-Purchase MVP-0

**Host budget reframe, 2026-07-15:** no new displays, compute module, PCB,
fabrication, enclosure, rented GPU, or paid service is an MVP requirement.

Using only hardware and accounts already available:

- a real chatbot conversation through the live connector creates and steers
  the cookbook Goal and Branch;
- one browser application renders the left and right cookbook screens side by
  side, or as two windows on existing displays;
- the existing computer's microphone, speakers, keyboard, and pointing device
  stand in for appliance I/O;
- structured recipes, step state, local timers, visible speech transcript,
  substitutions, offline recovery, and attribution/lineage are exercised;
- target users complete real cook sessions without returning to a phone after
  coaching starts;
- another user remixes a recipe, coaching policy, voice behavior, or screen
  layout through their chatbot and completes the descendant flow; and
- the sibling model-foundry proof trains a micro-model from random weights on
  already-owned compute, proving arbitrary training composition separately;
  and
- a larger cookbook/model descendant is compiled and market-quoted, then shown
  as **scale-ready / resource-blocked** under the `$0` spending cap; it may
  publish a below-market or zero-reward compute request without pretending the
  target run executed.

MVP-0 proves the connector experience, software architecture, recipe commons,
micro-scale model foundry, cap enforcement, quote path, and contribution-request
path. It explicitly does **not** prove custom hardware, vendor fabrication,
target-cluster execution, or the full physical stack.

## Later Funded Physical Scope

- One tether-powered clamshell prototype; no battery in v0.
- Two purchased matte 7–8 inch touch displays.
- Purchased Linux-capable compute module with sufficient memory/acceleration
  for the selected quantized local models, on a custom serviceable carrier PCB.
- Far-field microphone, speaker, physical mute, USB-C power, and wired debug.
- One language, one unit system at a time, and a bounded licensed/user-owned
  recipe library.
- Recipe discovery from stated ingredients/time/diet constraints.
- Step-by-step voice coaching, visible transcript, repeat/back/next, and
  multiple local timers.
- Explainable substitutions from structured candidates; uncertain or risky
  substitutions stop for user confirmation rather than being presented as fact.
- Wake word, speech recognition, recipe retrieval, cooking-model inference,
  speech output, active recipe, timers, and navigation operate with networking
  disabled. Connectivity is limited to explicit downloads, synchronization,
  and training/update workflows outside the active cook.
- A real connector-first build conversation, physical cook-session acceptance,
  and one downstream physical remix.

## Later Physical and User Acceptance

Proof v0 should eventually require:

- cold boot into the cookbook without developer intervention;
- two displays, audio, microphone mute, timers, and local state recovery;
- successful voice operation in representative kitchen noise, with visible
  correction when recognition is uncertain;
- completion of at least three unfamiliar recipes by target users without
  touching a phone or laptop after coaching begins;
- at least one missing-ingredient substitution per session, with the change and
  reasoning preserved;
- full voice coaching of the frozen recipe after network loss, with local
  retrieval and inference rather than a hidden remote fallback;
- cleanable enclosure/controls and a documented splash/grease test without
  claiming an ingress-protection certification that was not performed;
- actual user outcome feedback and an independent descendant remix.

## Key Assumptions to Validate

### Must be true

- Two persistent screens plus voice materially reduce kitchen friction compared
  with a tablet, smart display, or paper cookbook.
- Users trust coaching and substitutions only when changes, uncertainty, food
  safety, and provenance are visible.
- Speech can recover gracefully in fan, sink, utensil, and conversation noise.
- A dedicated object earns permanent counter/storage space.

### Should be true

- The book form is stable, cleanable, repairable, and readable under kitchen
  lighting.
- A quantized local cooking model provides acceptable latency, thermal load,
  memory use, speech quality, and substitution quality on serviceable hardware.
- Users want family recipes and successful adaptations preserved as lineage,
  not flattened into generic generated text.

### Might be true

- Users will share high-quality recipes, substitution outcomes, voice packs,
  enclosures, and hardware improvements through the commons.
- A later camera, scale, thermometer, or appliance integration will improve the
  coach enough to justify its privacy and complexity costs.

## Failure Premortem

- It is merely a more expensive tablet with a hinge.
- Voice fails exactly when hands are messy and the kitchen is noisy.
- Generated substitutions damage the dish or violate an allergy constraint.
- Recipe sources cannot be legally redistributed or lack provenance.
- The hinge, seams, speakers, or ports trap grease and cannot be cleaned.
- Local compute is too slow, hot, expensive, or weak at nuanced substitutions
  to beat a tablet or remote assistant.
- The build conversation is ceremonial while developers secretly prepare the
  appliance; that fails the democratized-stack proof.

The product must win through persistent two-screen context, hands-free state,
recipe lineage, and connector-driven user ownership—not through novelty alone.

## Not Doing in v0

- No hardware or compute purchase in zero-purchase MVP-0.
- No claim that the split-screen simulator is a manufactured cookbook.
- No camera, computer vision, pantry scanner, or automatic doneness detection.
- No direct control of ovens, burners, knives, or other hazardous appliances.
- No grocery purchasing, meal-plan marketplace, calorie treatment, or medical
  nutrition claims.
- No battery, wireless charging, waterproof certification, production tooling,
  or mass-market industrial design.
- No frontier-model training and no claim that inherited base weights were
  user-authored. Proof v0 adapts on rented/user compute and runs inference
  locally; from-scratch pretraining is a later Branch.
- No unlicensed recipe scraping or publication of private family recipes by
  default.
- No custom processor silicon or lithography in the first cookbook revision.

## Open Questions

1. **Resolved by host, 2026-07-15:** MVP cash spending is strictly `$0`; only
   already-available hardware/accounts and voluntary capacity may execute work.
2. Should the later physical proof lock the revised local-first boundary: rented/user compute
   may train or adapt models, downloads are explicit, but all cooking-session
   speech, retrieval, reasoning, and coaching inference runs on the appliance?
3. Does the user import recipes, discover licensed/public recipes, generate new
   recipes, or combine all three—with which source shown as primary?
4. Which food-safety and allergy claims must be independently sourced and
   evaluator-gated before the coach may say them aloud?

## Local-Model Feasibility Sources

- `llama.cpp` local inference and quantization:
  <https://github.com/ggml-org/llama.cpp>
- `whisper.cpp` offline speech recognition:
  <https://github.com/ggml-org/whisper.cpp>
- Hugging Face parameter-efficient fine-tuning:
  <https://huggingface.co/docs/transformers/peft>
