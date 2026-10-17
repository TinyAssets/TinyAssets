# Capability-URL connections

**Live, 2026-09-30.** A free-account universe
(`u-01ky3zh1arr8qth8jee7zx63pq`, turn `d75a6cb6447e4434b8b0d6aecf706475`)
was given a friend's webhook link — `https://tinyassets.io/hooks/inbound/<secret>`
— and asked to send bug reports to it. What happened, in order:

1. It raised a `connect_http` ask for an **"API Token or Bearer Token"** that
   does not exist. The user said so.
2. It tried an approve-only ask and hit
   `auth_scheme must be one of basic, bearer, header, oauth1a`.
3. It settled on `auth_scheme=bearer` with **the secret hardcoded in
   `endpoints[].path_template`**, plus a secret field captioned "paste the code
   at the end of the link".

So the pasted value went out as a useless `Authorization: Bearer` header, and
the real secret sat in the clear in the grant's `allowed_endpoints_json` — a
column that is projected to `read_graph target="connections"`, rendered in the
grant sentence on the owner's tab, and copied into a remixed connector
artifact. The universe did nothing wrong: there was no shape for what it had.

## The problem

A **capability URL** carries its secret as a path segment. It is the most
common way an agent posts a message anywhere:

| Channel | URL |
|---|---|
| Slack incoming webhook | `https://hooks.slack.com/services/T…/B…/<24-char token>` |
| Discord webhook | `https://discord.com/api/webhooks/<id>/<68-char token>` |
| Zapier / Make catch hook | `https://hooks.zapier.com/hooks/catch/<id>/<key>/` |
| TinyAssets inbound hook | `https://tinyassets.io/hooks/inbound/<43-char token>` |

None of the five depositable schemes describe it. `bearer`, `header`, `basic`
and `oauth1a` all put the credential in a **header**; `oauth2` needs a sign-in.
The secret's home — the path — is the one place the platform stores in the
clear, because `path_template` is grant policy the owner is meant to read.

The failure is therefore structural, not a prompting gap. Any universe that
meets a webhook link has exactly two options today, and both are wrong:

- name a header scheme and hardcode the secret in `path_template` (what
  happened live: the secret is stored in the clear and the header is noise), or
- give up and tell the user it cannot do it.

## What changes

1. **A new depositable `auth_scheme`: `url_secret`.** Its credential is not a
   header — it is a path segment. The ask declares the endpoint with a reserved
   placeholder, `{secret}` (one segment) or `{secret+}` (the final tail, for
   Slack's three-segment secret), and the vault holds the segment.

2. **The owner pastes the whole link.** One secret field, fixed name
   `capability_url`. The deposit parses the pasted URL, requires it to match
   the host and template the ask declared, and **extracts the secret segment
   itself**. The owner never has to find "the code at the end of the link" — and
   a link for the wrong host, or for a path the ask never declared, is refused
   before anything is written. Pasting just the segment still works.

3. **The secret is substituted at call time, inside the broker child.** The
   node's packet, the run state, the receipt, the evidence and every failure
   record carry the placeholder form `…/hooks/inbound/{secret}` — never the real
   URL. The allowlist matcher admits the `{secret}` segment **only as the
   literal token**, so the egress boundary is evaluated with no secret material
   present, and a node that hardcodes a real secret in its own packet is
   refused by the allowlist rather than quietly sent.

4. **A literal secret in `path_template` is refused at ask time**, on every
   scheme, with an error that names both fixes: `url_secret` + `{secret}` if
   it is a credential, or a `{param}` with a `param_patterns` regex if it is a
   public identifier (a Google Sheets id). Both are better than a hardcoded
   segment; the second is the shape the handbook already teaches.

5. **The `write_graph.connections` chapter gets one short webhook example** —
   the one shape the agent needs to recognise a pasted link.

## What does not change

- No per-service code. `url_secret` is as channel-agnostic as `bearer`: Slack,
  Discord, Zapier, Make and a hook nobody has heard of all deposit the same
  way, because the *shape* is what is named, not the service.
- The egress boundary is untouched: the same canonical-HTTPS parse, the same
  endpoint allowlist, the same DNS pin and globally-routable-address check, in
  the same order. Substitution happens after the allowlist has passed.
- The credential still never exists outside the broker child, and the response
  is still declassified against it.
- `access: "full"` is **refused** with `url_secret`: a full channel admits any
  path on the host, so the placeholder would never be enforced and the secret
  would have no home.
