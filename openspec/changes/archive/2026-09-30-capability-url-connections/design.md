# Design: capability-URL connections

## D1. The scheme name, and why a scheme at all

`url_secret`. It names where the credential goes, which is the only thing that
distinguishes it from `bearer` — the vault string is a single opaque token in
both cases. The alternatives were worse:

- **`path`**: collides with `path_template`, `request.path`, and the `{path+}`
  placeholder the handbook already teaches.
- **`webhook`**: names a use, not a mechanism. Capability URLs are not only
  webhooks (Zapier catch hooks, signed download links, some print/IoT APIs).
- **No new scheme, just allow a `{param}` whose value comes from the vault**:
  this is the shape that has to be refused. A `{param}` is *node-supplied*;
  making some params vault-supplied depending on a per-endpoint flag puts the
  distinction on 200 endpoint rows instead of on one connection field, and the
  dispatch-time fail-closed check then has 200 places to get wrong.

One connection field, read by the same three checkpoints the existing
`connection_type` ↔ `credential_ref` binding uses (create, deposit door,
dispatch), is the cheaper shape.

## D2. Reserved placeholder, and the match-then-substitute order

Reserved placeholder names: **`{secret}`** (exactly one segment) and
**`{secret+}`** (the final tail, one or more segments — Slack's secret is
`T…/B…/token`, three segments).

**It must be the template's ONLY placeholder** (added by self-review after
review round 2). A capability URL is a fixed path plus a secret, which is
exactly what all four real shapes are — `/services/{secret+}`,
`/api/webhooks/{secret+}`, `/hooks/catch/{secret+}`, `/hooks/inbound/{secret}` — so
the restriction costs nothing real and buys two things. It removes the shape
review round 1's FINDING 3 attacked, making the positioned substituter defence
in depth rather than the only line. And it makes the pasted-link parser
*correct* rather than approximately correct: `extract_url_secret` matches the
template's prefix as a literal string, so `/hooks/{room}/{secret}` accepted the
ask, took the owner's **right** link, and refused it. Telling an owner their
correct answer is wrong is the failure this change exists to stop.

`_validate_param_patterns` today demands that `param_patterns` declare
**exactly** the template's placeholders. For the reserved name it instead:

- **refuses** a caller-declared `param_patterns["secret"]` — the pattern is the
  platform's, not the caller's; and
- **injects** the pattern `re.escape("{secret}")` (or `re.escape("{secret+}")`).

That injected pattern is the whole security argument. The stored allowlist
entry for `/hooks/inbound/{secret}` matches **only** the concrete path
`/hooks/inbound/{secret}` — the literal token. So:

```
node packet path   /hooks/inbound/{secret}            -> matches, substituted
node packet path   /hooks/inbound/<a-real-secret>      -> REFUSED by the allowlist
node packet path   /hooks/inbound/../../admin          -> REFUSED (upstream + allowlist)
```

**Order at call time** (`_SsrfHardenedHttpDriver.__call__`):

1. `_parse_canonical_https_url(url)` — the URL still holds `{secret}`. `{` and
   `}` are 0x7B/0x7D, outside `_SSRF_FORBIDDEN_URL_CHARS`, so they survive the
   canonical parse unchanged. Every existing safety check (scheme, userinfo,
   port, dot segments, encoded separators, double encoding) runs first.
2. `_enforce_endpoint_allowlist(canonical, verb, endpoints, mode)` — the real
   egress boundary, evaluated **with zero secret material in the URL**.
3. **Only then** substitute the vault segment into `canonical.path_qs`.
4. DNS pin, globally-routable-address check, socket — unchanged, on the
   substituted canonical (the host is not touched by substitution).

Substitute-*then*-match was rejected: it requires the `secret` placeholder to
carry a permissive value pattern, which re-admits a node-supplied segment in
the secret's position, and it feeds secret bytes into the matcher and into
every `SsrfValidationError` raised by it.

## D3. Substitution is byte-verbatim, and the segment is validated twice

The stored secret is the path segment **exactly as the user received it**, so
the wire bytes equal the link they were given. No percent-encoding is applied
(that would double-encode a `%`-bearing token and hit the existing
`must not be double-encoded` refusal).

That is only safe if the stored value cannot itself introduce structure, so the
segment grammar is enforced at the deposit door **and** re-enforced in the
child immediately before substitution (fail closed on a mutated or corrupted
vault value — the same belt-and-braces as
`_validate_connection_credential_scheme`):

```
_URL_SECRET_SEGMENT_RE = ^[A-Za-z0-9._~!$&'()*+,;=:@-]{8,512}$        # {secret}
_URL_SECRET_TAIL_RE    = ^<segment>(/<segment>){0,7}$                  # {secret+}
```

Derived from `_SSRF_ENDPOINT_LITERAL_RE` **minus `%`** (no percent-encoding in
a capability secret) and minus the empty match. `/`, `?`, `#`, `\`, control
bytes and dot-segments are therefore all unrepresentable. Minimum 8 characters
because a capability secret shorter than that is not one; maximum 512 is
`_SSRF_MAX_MATCH_SEGMENT`.

## D4. The pasted link is parsed by the platform

`connect_http` accepts either shape in `secret`:

- **`https://…` (preferred).** Parsed with `urlsplit`. It must be `https`, carry
  no userinfo/port/fragment, and its host + path must match **exactly one** of
  the connection's declared endpoints, whose template must carry the reserved
  placeholder. The captured segment(s) become the stored credential. A trailing
  `/` is tolerated (Zapier's own UI shows one) and a query string is refused —
  a capability URL's secret is in the path, and a query would be silently
  dropped.
- **a bare segment.** Validated against D3 and stored as-is.

A refusal **never echoes the pasted value** — it echoes the *template* the ask
declared (`expected POST https://tinyassets.io/hooks/inbound/{secret}`). Two
candidate endpoints that both match is also a refusal: which one holds the
secret would be a guess.

## D5. The hardcoded-secret refusal, and why it is actionable either way

`looks_like_embedded_secret(segment)` — a **literal** (non-placeholder) path
segment with:

- length ≥ 20, **and**
- at least one letter and at least one digit, **and**
- at least 10 distinct characters.

Calibration (`tests/test_capability_url_connections.py` pins the table):

| segment | verdict |
|---|---|
| `MRL8k3nZ-Ht9xWq2vB7pC4dF6gJ0sYaT1u` (hook token) | secret |
| `T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX` (Slack) | secret |
| `completions`, `messages`, `2`, `v1`, `pulls` | fine |
| `contents`, `git`, `refs`, `heads` | fine |
| `1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms` (Sheets id) | secret |

The last row is a deliberate false positive. A Google Sheets id is not a
credential, but hardcoding it is still the wrong shape — it belongs in a
`{param}` with a `param_patterns` regex, which is what the handbook already
teaches for `contents/{path+}`. So the error names **both** fixes and is
actionable whichever it is:

> `path_template` segment 1 looks like a secret or an id, not a fixed path. If
> it is a credential (a webhook link's code), use `"auth_scheme": "url_secret"`
> and put `{secret}` there — it goes to the vault, not into this grant. If it
> is a public identifier, make it a `{param}` with a `param_patterns` regex.

The check runs at the **authoring doors only** — `_validated_endpoint_list`
(ask), `connect_http`, `extend_http` — never in `_validate_path_template`,
which also re-parses **stored** templates. Putting it there would make an
already-deposited connection unreadable, which is a data-loss bug wearing a
security fix's name.

## D6. Where the binding is enforced, three times

`url_secret` ⇔ the templates carry a reserved placeholder. Both directions,
because both are leaks:

- `url_secret` with no `{secret}` anywhere → the vault segment has nowhere to
  go; on the wire it would be a secret-free request to the host.
- A `{secret}` template on a **header** scheme → the literal `{secret}` token
  is sent in the path *and* the real credential in an `Authorization` header
  that the receiver never asked for.

Enforced at:

1. `connect_http` / `extend_http` — a precise, user-facing refusal before any
   write.
2. `ConnectionLedger.create_connection` — the storage boundary, so no issuer
   can skip it (the same reason `validate_git_scopes` lives there).
3. `_TrustedNetworkDriver._dispatch_http` — the **current** row, re-read at
   dispatch, so a row mutated after the proxy started is refused before a
   credential is resolved. This is the TOCTOU closure
   `_validate_connection_credential_scheme` already documents.

Every endpoint on a `url_secret` connection must carry exactly one reserved
placeholder — not "at least one endpoint". A mixed connection would have
endpoints reachable without the secret, and the owner's grant sentence could
not say which.

`access: "full"` + `url_secret` is refused (D6 is unenforceable under `full`:
the host match alone admits any path).

## D7. Redaction is structural, not a scrub list

The secret exists in exactly one process — the spawned broker child — and for
the duration of one substitution. Every output channel is therefore clean by
construction rather than by filtering:

| Channel | Why it is clean |
|---|---|
| Node packet / graph / `read_graph` | The author writes `{secret}`; a real secret there is refused by the allowlist |
| Run state, `$ta.effect` evidence | Built from the packet |
| Effector result `url` (success **and** error) | The pre-substitution URL — the effector never sees the other one |
| `external_write_errors` rows | Derived from `error`/`error_kind`/`destination`; no URL field |
| Broker → adapter process boundary | Only `_adapter_safe_proxy_error`'s fixed strings cross; `_send`/`_record_error` are fixed text |
| Response headers and body | `bundle.secret_values()` includes the segment → `_declassify_response` scrubs it; `_contains_secret` refuses an echoing response outright |
| Grant policy JSON / connector artifact / grant sentence | Hold the template, which holds `{secret}` |

So the only additive work is: put the segment in the bundle (so declassify and
`_contains_secret` see it), and emit **no** auth header for the scheme.

## D8. Tests

`tests/test_capability_url_connections.py`, plus additions to the existing
outbound/deposit files where the seam already has a home.

1. **Ask validates.** `connect_http` with `auth_scheme: url_secret`,
   `path_template: /hooks/inbound/{secret}`, one `capability_url` secret field →
   pending row, grant sentence renders the template.
2. **Accept stores the segment.** Answer with the full pasted link → the vault
   holds **only** the segment; `allowed_endpoints_json` holds `{secret}`; the
   projected view and the grant sentence hold no part of the secret.
3. **Wire.** An injected HTTP-layer transport records the URL actually sent:
   `https://host/hooks/inbound/<segment>`. Mutation check: break the substitution
   and this test must fail.
4. **Redaction.** One table over every channel in D7 asserting the segment is
   absent — success path and failure path (a far-side 500 with the URL in its
   body).
5. **Hardcoded secret refused.** The full D5 calibration table, both verdicts.
6. **Mismatched paste refused.** Wrong host; a path the ask never declared; a
   query string; userinfo; two matching endpoints. Each refusal's text must not
   contain the pasted value.
7. **Binding.** `url_secret` with no placeholder; `bearer` with `{secret}`;
   `access: full` + `url_secret`; a **row mutated** after deposit → refused at
   dispatch.
8. **Grammar.** A segment with `/`, `%2f`, `..`, a control byte, 7 chars → each
   refused at the door **and** in the child.

## D9. Review

### Round 1 — `gpt-6-astra`, 2026-09-29, head `d8dc1986`: `OVERALL: HOLES (6)`

Dispatched via `peer-agents` from the PR worktree on the two axes above. Five
findings were real and this change's; one was pre-existing. Every one is
`DISAGREE_EVIDENCE` with a reproduction, which is the verdict class worth
having — each names the exact input.

| # | Finding | Disposition |
|---|---|---|
| 1 | Redeposit race sends the new secret to the old endpoint | **Filed**, not fixed — pre-existing and scheme-independent (`docs/concerns/2026-09-29-redeposit-race-sends-new-secret-to-old-endpoint.md`) |
| 2 | Individual `{secret+}` segments escape response scanning | **Fixed** — `url_secret_sensitive_values`; every segment is a bundle member and a broker `secrets_held` entry |
| 3 | An alternate reserved token receives substitution in the wrong path position | **Fixed** — substitution is positioned by the matched endpoint's template (D2 revised below) |
| 4 | Dispatch does not reject a capability connection mutated to `full` | **Fixed** — `access_mode` passes into `validate_url_secret_binding`, checked at dispatch and again in the substituter |
| 5 | Malformed pasted links produce secret-bearing parser exceptions | **Fixed** — `urlsplit` (and the lazy `.port`) wrapped; fixed text, `__context__` dropped |
| 6 | Deposit silently discards the pasted capability's port | **Fixed** — an explicit port is refused, not ignored |

**Finding 3 changed D2**, so the design statement above is the revised one.
The original searched the concrete path for the reserved token. astra's input:
declare `/hooks/{secret}/{tail+}` with `tail: ".*"`, which legitimately admits
`/hooks/{secret}/echo/{secret+}` — the reserved segment is the literal token and
the tail matches anything. A search finds `{secret+}` **in the tail** and puts
the credential at a path the owner granted for arbitrary content. So
`_enforce_endpoint_allowlist` now RETURNS the endpoint that admitted the
request, `_positioned_url_secret_path` replaces only the segment that
endpoint's template reserves, and a closing invariant refuses any reserved
token that survives substitution.

**Finding 2 was the subtlest.** Making the credential multi-segment (for
Slack) made the response scanners — which match substrings — miss an echo of
ONE segment. That is the class the repo already names: a guard whose test drives
the joined form never sees the segment form.

Note for the next dispatch: `codex exec -m gpt-6-astra -s read-only` **wrote
files** (six concern drafts). The `codex` on this host is a shim that injects a
bypass flag, so `-s read-only` did not hold. Inventory `git status` after a
dispatched review.

### Round 2 — `gpt-6-astra`, 2026-09-29, head `ec6a06b7`: `OVERALL: HOLES (2)`

Refuting its **own** round-1 repairs, on three questions: did a repair create a
new hole, is finding 1's disposition defensible, and is anything only narrowed.
Four `AGREE`, two `DISAGREE_EVIDENCE`.

The four `AGREE`s are the load-bearing part, because each is a claim I would
otherwise only be asserting:

- **The return-type change is sound.** `_enforce_endpoint_allowlist` now returns
  the matched endpoint; every caller (including `_redirect_chain`) branches on
  success-versus-exception, not truthiness. Fixed templates require equal
  segment counts, an ordinary rest placeholder is final so it cannot shift an
  earlier reserved slot, and `full` returning `None` cannot authorize a
  substitution.
- **The new bundle members collide with nothing.** No `secret_values()` consumer
  assumes one value, and no `get()` name collides.
- **Finding 1's disposition is right.** astra reproduced the redeposit race
  with `bearer` on the **pre-feature** commit `7743112e` — which is the evidence
  I wanted for filing it rather than fixing it here — and confirmed
  `url_secret` does not widen its blast radius (substitution preserves
  hostname and port, and the scheme forbids redirect-enabled endpoints).
- **Round-1 findings 2, 5 and 6 are closed in their original forms.**

The two open ones:

| # | Finding | Disposition |
|---|---|---|
| R2-1 | Per-segment response scanning rejects clean responses that contain a platform wrapper key (`body_text/<token>` fails a 200) | **Already fixed** in `ada98dfa`, written before this round returned — the per-segment scan floor is 16 characters, and astra's colliding keys are 8-9. Pinned by `test_no_platform_response_key_can_collide_with_a_scanned_segment`, which fails if a wrapper key ever grows to 16. |
| R2-2 | The closing invariant still permits reserved tokens outside the substituted slot | **Fixed** — `_reject_stray_reserved_tokens` |

**R2-2 is the honest one.** The design said "nothing reserved survives
substitution" and the check did not deliver it: it compared **whole, undecoded**
path segments, and the query was appended unchanged. Three spellings survived —
`?q={secret}`, `%7Bsecret%7D` inside a permissive `{tail+}`, and
`prefix{secret}` embedded in a larger segment. astra was explicit that none of
them substitutes a credential into the wrong slot; the positioned substituter
had settled that. But a reserved token is a platform marker, and sending one
tells a receiver what shape the connection is. The check is now a substring
test, on the raw text **and** its percent-decoded form, over the path with the
legitimate slot blanked and over the whole query — and it runs before the secret
is joined in, so no refusal can carry it.

**Stopping at two rounds.** Round 2's findings are fixed; a third round would be
reviewing repairs to repairs, which this repo has measured as non-convergent
(`AGENTS.md`: fixing round N's findings often creates round N+1's). What remains
open is one filed concern that both rounds agree is pre-existing.

### Live proof

Mint a hook on the **founder's own** universe (`write_graph target="webhook"
operation="mint"`), deposit its URL as a `url_secret` connection, and POST to it
through the real effector. Never the free account's friend link.
