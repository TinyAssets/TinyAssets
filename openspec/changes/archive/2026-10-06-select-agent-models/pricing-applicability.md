# Price evidence versus dispatch bounds

September9,2026. Proposed adaptation for task1.3/2.2; not execution authority
or a claim that current account discovery/selection is complete.

## Reverified mismatch

Anonymous public all-modality catalogue read at23:11:57 UTC:583 entries,
zero with all four currently required price fields.239 entries have only
prompt/completion,61 of those with both explicitly0. The app's normal connected
catalogue question at16:26 PDT returned430 entries at16:28 and clearly separated
base token prices from possible cache/search/media/long-context charges. Neither
observation proves every model is account-eligible or usable for full agent tools.

Official SDK schema:
[PublicPricing](https://github.com/OpenRouterTeam/typescript-sdk/blob/main/src/models/publicpricing.ts)
requires prompt/completion; request/image and cache/reasoning/search/media fields
are optional. Conditional overrides carry real prices: retain per-component
maxima over the base and every override, without guessing which condition wins.
Discounts can only lower the conservative bound. SDK
[MaxPrice](https://github.com/OpenRouterTeam/typescript-sdk/blob/main/src/models/providerpreferences.ts)
exposes prompt/completion/request/image/audio bounds as decimal strings. The
[routing guide](https://openrouter.ai/docs/guides/routing/provider-selection)
documents enforced price filtering; it does not establish a per-cache/search
limit or applicability rule for every extra fee.

## Adaptation to review before execution changes

1. Keep advertised price evidence separate from owner-accepted dispatch bounds.
   Missing optional request/image metadata must remain absent/unknown, not gain
   fabricated zero charges. Mandatory token prices must still be present,
   finite, exact and nonnegative. A failed/partial catalogue is not usable.
2. The protocol, not the engine or model-release list, declares required base
   prices and enforceable dispatch components for the exact request shape.
   A missing optional fee may be attempted only when a fresh dispatch constraint
   enforces that accepted ceiling. Advertised fees above that ceiling are rejected
   before launch; absence cannot widen the wire bound or budget reservation.
3. Preserve additional advertised charges rather than erasing known token prices.
   Unknown or unbounded applicable extras remain a reason not to launch. Do not
   silently exclude them from free-only cost checks. A component is irrelevant
   only when the actual encoded request provably cannot invoke it, not because
   the request was casually called text-only.
4. Exact decimal-string maxima remove binary-float conversion and match the
   current protocol schema. All owner caps and signed membership remain unchanged.
   No new table, key access, provider-definition rewrite, or request allowance.
5. Constrain the actual request, including default output modalities, aliases,
   presets and plugins. A model advertising text plus image must not generate
   chargeable images outside the reservation. No server add-on may escape the
   accounting assumptions merely because the client body omits a tools field.
6. Reuse one cost eligibility/reservation contract in discovery and final
   execution, with realistic optional-field fixtures. Do not bolt an additional
   authority engine onto the pure advisory ranking function. Display unavailable
   candidates and their actual known prices/reason instead of dropping them.

## Required review decision

Determine the smallest safe implementation of points1–5 using the existing
policy/protocol/SelectedModel seams. In particular, is a missing request price
safe under request max_price0 without representing it as an advertised0, and
which non-token components can genuinely be excluded by the current wire shape?
If broader extra-price coverage requires endpoint-level pricing or additional
provider capability evidence, identify that as a concrete integration requirement,
not as permission to activate a permanently narrower selector. The user still
wants all eligible connected sources/models, best-first automatic defaults,
explicit controls and safe fallback. This adaptation does not redefine completion.

Tests must discriminate optional omission from malformed/paid/unknown fees,
prove wire maxima and reservation are never widened, preserve exact accounting,
and exercise realistic catalogue rows through the real selected router. Final
proof remains account-filtered live discovery and ordinary app interaction after
deployment, including the full HTTP tool loop rather than text-only success.

## Independent shape review disposition

Claude review40794 completed in271s, VERDICT ADAPT (September9 UTC). Accept
the required-evidence/wire-ceiling split; no manifest/storage change. Preserve
known prices alongside unknown components and fold conditional maximums. Do
not send modalities=text: require_parameters would unnecessarily exclude
providers. Instead require explicit zero output-image/audio cost for any such
advertised output modality. Plain-string messages prohibit input media and
cache_control; built-in search still needs zero-price evidence when advertised.
Implementation review14602 corrected the cache-write premise: automatic writes
can incur a surcharge without cache_control (current OpenAI behavior). Both
cache reads and writes must fit input cap; reasoning must fit output cap. Only
explicit 1h cache writes remain excluded. Decimal-string
wire maxima and preset/online guards belong to this text-only protocol shape.
Full HTTP tools must re-evaluate these exclusions before activation. Live
account-filtered execution remains a gate, particularly provider endpoint
filtering and paid accounting; this review is not a deployment claim.
