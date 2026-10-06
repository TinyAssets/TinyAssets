# Outside-client bearer admission inventory

Round-2 source audit at `a1fcedd27c`; line numbers refer to that unchanged implementation. This is the future admission contract, not a claim of shipped checks. There are 32 app route patterns: three `/app/api/*` owner-door routes and 29 other `/app/*` routes. Each listed route refuses outside OAuth clients in this slice, even with wildcard/control/costly grants. Outside clients use the existing granted MCP operations; an equivalent HTTP operation may be opened later only with the same operation/resource admission. First-party exemption does not bypass ordinary owner authorization or protected approval proof.

| Bearer route (all declared methods) | Registration file:line | Required outside disposition |
|---|---|---|
| `/app/api/read` | `tinyassets/owner_door/routes.py:221` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/api/status` | `tinyassets/owner_door/routes.py:222` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/api/ui-asset` | `tinyassets/owner_door/routes.py:223` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/approvals/{operation}` | `tinyassets/onboarding/__init__.py:2553` | Refuse outside clients; retain protected interactive session requirement |
| `/app/model-connect/{operation}` | `tinyassets/onboarding/__init__.py:2555` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/openai/device/start` | `tinyassets/onboarding/__init__.py:2562` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/openai/device/poll` | `tinyassets/onboarding/__init__.py:2563` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/openai/begin` | `tinyassets/onboarding/__init__.py:2564` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/openai/exchange` | `tinyassets/onboarding/__init__.py:2565` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/voice/status` | `tinyassets/onboarding/__init__.py:2566` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/voice/session` | `tinyassets/onboarding/__init__.py:2567` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/me` | `tinyassets/onboarding/__init__.py:2568` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/trace` | `tinyassets/onboarding/__init__.py:2569` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/serving/bind` | `tinyassets/onboarding/__init__.py:2570` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/models/preferences` | `tinyassets/onboarding/__init__.py:2571` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/billing/status` | `tinyassets/onboarding/__init__.py:2572` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/billing/checkout` | `tinyassets/onboarding/__init__.py:2573` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/billing/cancel` | `tinyassets/onboarding/__init__.py:2574` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/account/delete` | `tinyassets/onboarding/__init__.py:2576` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/account/timezone` | `tinyassets/onboarding/__init__.py:2577` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/ui-prefs` | `tinyassets/onboarding/__init__.py:2578` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/rules` | `tinyassets/onboarding/__init__.py:2579` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/memory` | `tinyassets/onboarding/__init__.py:2580` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/profile` | `tinyassets/onboarding/__init__.py:2581` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/turn/interrupt` | `tinyassets/onboarding/__init__.py:2582` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/live` | `tinyassets/onboarding/__init__.py:2583` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/turn/steer` | `tinyassets/onboarding/__init__.py:2584` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/turn/pending` | `tinyassets/onboarding/__init__.py:2585` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/connections` | `tinyassets/onboarding/__init__.py:2586` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/files` | `tinyassets/onboarding/__init__.py:2587` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/devices` | `tinyassets/onboarding/__init__.py:2593` | Refuse outside clients; first-party owner/ACL checks remain |
| `/app/notify` | `tinyassets/onboarding/__init__.py:2594` | Refuse outside clients; first-party owner/ACL checks remain |

`_app_identity_required` (`tinyassets/onboarding/__init__.py:583`) is presently identity-only. Admission must run before handler reads/effects, including `file_upload.py:160`, `inline_requests.py:18`, `connections.py:37`, `model_connect.py:31`, `model_preferences.py:29`, and `notifications.py:147` / `:213` (all under `tinyassets/onboarding/`). `/app/api/read` cannot expose its complete owner document to an outside bearer. Enumerate actual mounted routes/methods in an integration test; every bearer route must have an explicit disposition, and newly added/unclassified routes refuse outside clients by default.

| Other surface | File:line | Admission contract |
|---|---|---|
| Public `/mcp`, all transport methods, initialize/list/call, resources/prompts and sessions | `tinyassets/universe_server.py:4626`; `tinyassets/auth/middleware.py:1018` | Verified outside identity, live generation/fence/switch; all seven handles use agent/operation grants; discovery grants no data authority; static prompts remain static |
| `GET /mcp/pulse` | `tinyassets/universe_server.py:4822` | Refuse outside OAuth clients; retain verified first-party and existing narrow canary principal rules |
| Other `/mcp/*` and `/app/*` paths, including retired `/mcp/app*` | `tinyassets/auth/middleware.py:645` | Unknown routes never bypass admission; authenticated unknown paths remain not found |
| Loopback engine `/mcp` bearer listener | `tinyassets/engine_mcp_server.py:4692`; `tinyassets/engine_mcp_http.py:180` | Refuse outside OAuth tokens; private engine bearer remains bound to owner/launch. Outside-origin work forwarded here carries trusted provenance and rechecks grants/mode/fence before data/effects; owner/universe work continues |
| Private OAuth service `POST /` | `tinyassets/connection_oauth/service.py:85` | Refuse outside OAuth tokens; retain launch-bound private bearer. Any outside-origin delegated call preserves origin and passes downstream admission |

Non-OAuth ingress is not an outside-grant loophole: `/mcp/hooks/{token}` (`tinyassets/universe_server.py:4756`) uses its separate revocable webhook capability, not an AuthKit bearer; a client-created hook/schedule retains outside origin. Public shells, static modules, discovery, `/app/model-callback/{flow}`, `/app/token` and `/app/owner-sign-in` are bootstrap/PKCE/session flows, not bearer-authorized owner-data routes (`tinyassets/auth/middleware.py:577`). They cannot convert an outside bearer into first-party identity or interactive owner proof. Billing webhook keeps signed provenance (`tinyassets/onboarding/__init__.py:2575`). Canary bearer remains restricted to its existing health/tool-catalog allowance (`tinyassets/auth/middleware.py:1026`), never an owner grant exemption.
