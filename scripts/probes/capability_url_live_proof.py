#!/usr/bin/env python3
"""LIVE PROOF: a capability-URL connection posts to a real ``/hooks/inbound`` receiver.

Runs INSIDE the production container against ``/data``, as
``/app/scripts/probes/capability_url_live_proof.py --universe <uid>``. It proves
the one thing no unit test can: that the vault segment reaches the wire, and
that nothing else does.

No invocation is spelled out here on purpose. ``drop-first-exec`` refuses a
repo-authored bare exec into the daemon container -- it asserts nothing about
the identity or capability set it lands with -- and the closed ta-op mode table
(``deploy/native/ta_op_modes.tsv``) has no mode for an arbitrary script, which
is the correct answer for a wrapper whose whole value is that its argv is a
compile-time constant. An operator reaches this the way that gate names as its
stated limit: ad-hoc admin SSH holding the deploy key, which is outside the repo
gate by construction.

**The oracle is the receiver's own contract**, not this script's view of the
secret. ``tinyassets/webhook_inbound.py`` answers a deliverable POST with
**202** and EVERY non-deliverable state -- unknown token, revoked, malformed --
with a uniform **404**. So a 202 can only mean the real token was on the wire,
which is exactly what the substitution has to do. Nothing is asserted from the
inside.

Safety:

* ``--universe`` is REQUIRED and must be one the named principal administers.
  There is no default, so this can never wander onto somebody else's universe
  (the 2026-09-30 incident that motivated the feature involved a free account's
  friend's webhook; a probe must not be able to reach one).
* It creates its OWN throwaway branch and mints the hook for that, so no real
  branch of the owner's ever executes.
* ``finally``-cleanup removes the connection, the consent, the hook and the
  branch. ``--cleanup`` re-runs just that.

The 202 means the receiver ENQUEUED a run, and cleanup then deletes the branch
under it -- so the owner sees one failed run named "Capability URL live proof".
That is deliberate: the 202 is returned at enqueue, so the proof does not need
the run to execute, and a failed run clearly named for the probe is cheaper and
tidier than leaving a live branch and a real inference behind.

Findings baked in from the first run (2026-09-30):

* The packet sets NO ``User-Agent``, on purpose. It used to: the driver sent
  none, and ``tinyassets.io`` is behind Cloudflare, which answers a UA-less POST
  with ``error code: 1010`` (403) before the receiver sees it. That was filed
  from this probe's first run and is now FIXED -- the driver sends the
  platform's own client string on every call, and a per-call one is refused. So
  this probe reaching the receiver with no special allowance is itself the
  standing proof that the 1010 stays gone.
* ``PROOF_BASE`` / ``PROOF_FOUNDER`` exist ONLY to rehearse against a throwaway
  data dir before pointing this at production. Production is the default.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DESTINATION = "capability-url-live-proof"
BRANCH_ID = "capurlproof01"
BRANCH_NAME = "Capability URL live proof"
HOOK_HOST_DEFAULT = "tinyassets.io"
TEMPLATE = "/hooks/inbound/{secret}"

BASE = os.environ.get("PROOF_BASE", "/data")
os.environ["TINYASSETS_DATA_DIR"] = BASE
os.environ.setdefault("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def resolve_admin(universe_id: str) -> str:
    """The principal with an explicit admin row on THIS universe, or raise."""
    override = os.environ.get("PROOF_FOUNDER")
    if override:
        return override
    from tinyassets.daemon_server import list_universe_acl

    admins = [
        row["actor_id"]
        for row in list_universe_acl(BASE, universe_id=universe_id)
        if row.get("permission") == "admin"
    ]
    if len(admins) != 1:
        raise SystemExit(
            f"{universe_id} has {len(admins)} admin rows; name the principal "
            "with PROOF_FOUNDER rather than guessing"
        )
    return admins[0]


def _login(actor: str) -> None:
    """Bind the authenticated principal the way the app's own session does."""
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity

    class _P(AuthProvider):
        def resolve_token(self, token):
            if token != "proof":
                return None
            return Identity(
                user_id=actor,
                username=actor,
                capabilities=["tinyassets.universe.write"],
            )

        def is_auth_required(self):
            return True

        def register_client(self, metadata):
            return {"client_id": "proof", **metadata}

        def create_authorization(self, *a, **k):
            return "code"

        def exchange_code(self, *a, **k):
            return None

    set_provider(_P())
    auth_middleware("proof")


def vault_records(universe_id: str) -> list[dict]:
    from tinyassets.credential_vault import load_credential_vault

    return [
        r
        for r in load_credential_vault(f"{BASE}/{universe_id}")
        if r.get("credential_type") == "http" and r.get("destination") == DESTINATION
    ]


def cleanup(universe_id: str, *, token: str = "") -> None:
    print("\n--- cleanup ---")
    from tinyassets.api.http_connection import remove_http
    from tinyassets.daemon_server import _connect
    from tinyassets.storage import webhook_hooks
    from tinyassets.storage.effector_consents import revoke_consent

    try:
        out = remove_http(
            universe_id=universe_id, payload=json.dumps({"destination": DESTINATION})
        )
        print("remove_http:", out.get("status") or out.get("error"))
    except Exception as exc:  # noqa: BLE001 - cleanup is best-effort and loud
        print("remove_http failed:", type(exc).__name__)
    try:
        revoke_consent(
            f"{BASE}/{universe_id}",
            sink="authenticated_external_call",
            destination=DESTINATION,
        )
        print("consent revoked")
    except Exception as exc:  # noqa: BLE001
        print("revoke_consent failed:", type(exc).__name__)
    if token:
        print("hook revoked:", webhook_hooks.revoke(BASE, token=token))
    with _connect(BASE) as conn:
        conn.execute("DELETE FROM branch_definitions WHERE branch_def_id = ?", (BRANCH_ID,))
    print("branch removed:", BRANCH_ID)
    live = [
        row
        for row in webhook_hooks.list_for_universe(BASE, universe_id=universe_id)
        if row.get("branch_def_id") == BRANCH_ID and row.get("revoked_at") is None
    ]
    check("cleanup: no active proof hook remains", not live, f"{len(live)} left")


def main(universe_id: str, hook_host: str) -> int:
    from tinyassets.api.http_connection import (
        _DEPOSITABLE_AUTH_SCHEMES,
        _ids,
        connect_http,
    )
    from tinyassets.daemon_server import create_branch_definition_once
    from tinyassets.effectors.authenticated_external_call import (
        run_authenticated_external_call_effector,
    )
    from tinyassets.storage import webhook_hooks
    from tinyassets.storage.effector_consents import grant_consent
    from tinyassets.storage.outbound_connections import ConnectionLedger

    actor = resolve_admin(universe_id)
    _login(actor)
    print(f"universe={universe_id} principal={actor} host={hook_host}")

    check(
        "0. the RUNNING code offers url_secret",
        "url_secret" in _DEPOSITABLE_AUTH_SCHEMES,
        f"schemes={sorted(_DEPOSITABLE_AUTH_SCHEMES)}",
    )
    if FAILURES:
        return 1

    # It must VALIDATE, or the receiver answers its uniform 404 for an
    # un-runnable branch and check 6 cannot tell that from a rejected token.
    # The first production run of this probe failed exactly there
    # (`branch capurlproof01 failed validation: ['Branch must have at least one
    # node.', "Entry point 'noop' is not a defined node."]`) because the insert
    # helper reads `graph_nodes` / `edges` / `node_defs`, NOT the `*_json`
    # column names. `display_name` is required on a node def.
    branch, created = create_branch_definition_once(
        BASE,
        branch_def={
            "branch_def_id": BRANCH_ID,
            "name": BRANCH_NAME,
            "description": "Receiver for the capability-URL live proof. Safe to delete.",
            "author": actor,
            "entry_point": "noop",
            "graph_nodes": [{"id": "noop", "node_def_id": "noop", "position": 0}],
            "edges": [{"from": "START", "to": "noop"}, {"from": "noop", "to": "END"}],
            # A CODE node, not a prompt node: the enqueued run costs the owner
            # no inference at all (`provider_used` and `token_count` both stay
            # NULL, confirmed live). The sandbox needs a CALLABLE, not a bare
            # assignment -- two production runs taught this the long way, first
            # `prompt` instead of `prompt_template` (node validation), then a
            # bare `result = ...` ("No callable function found in node source
            # code"). A failed run named for this probe reads like the FEATURE
            # failed, which is the opposite of what a proof should leave behind.
            "node_defs": [
                {
                    "node_id": "noop",
                    "display_name": "noop",
                    "source_code": "def run(state):\n    return {'noop': 'ok'}\n",
                    "output_keys": ["noop"],
                }
            ],
        },
    )
    print(f"branch {branch['branch_def_id']} ({'created' if created else 'reused'})")

    token = webhook_hooks.mint(
        BASE, universe_id=universe_id, branch_def_id=BRANCH_ID, owner_principal_id=actor
    )
    pasted = f"https://{hook_host}/hooks/inbound/{token}"
    print(f"minted: https://{hook_host}/hooks/inbound/<{len(token)}-char token>")

    try:
        deposited = connect_http(
            universe_id=universe_id,
            payload=json.dumps(
                {
                    "destination": DESTINATION,
                    # THE WHOLE LINK. The platform extracts the segment.
                    "secret": pasted,
                    "auth_scheme": "url_secret",
                    "allowed_endpoints": [
                        {"host": hook_host, "path_template": TEMPLATE, "methods": ["POST"]}
                    ],
                }
            ),
        )
        check(
            "1. the pasted link deposits",
            deposited.get("status") == "provisioned",
            json.dumps(deposited)[:180],
        )
        check(
            "2. the deposit response carries no part of the token",
            token not in json.dumps(deposited),
        )
        records = vault_records(universe_id)
        check(
            "3. the vault holds ONLY the segment, never the URL",
            len(records) == 1
            and records[0]["token"] == token
            and "https://" not in records[0]["token"],
            f"{len(records[0]['token']) if records else 0} chars stored",
        )
        conn_id, grant_id = _ids(universe_id=universe_id, destination=DESTINATION)
        ledger = ConnectionLedger(
            f"{BASE}/outbound.db", verify_authenticated_principal=lambda: actor
        )
        view = ledger.get_connection_view(conn_id)
        stored = view.allowed_endpoints[0].path_template
        check(
            "4. the GRANT holds the placeholder, not the token",
            stored == TEMPLATE and token not in stored,
            stored,
        )
        policy = ledger.policy_json(conn_id)
        check(
            "5. the stored policy JSON carries no part of the token",
            policy is not None and token not in policy[0],
        )

        grant_consent(
            f"{BASE}/{universe_id}",
            sink="authenticated_external_call",
            destination=DESTINATION,
            granted_by=actor,
        )

        packet = {
            "sink": "authenticated_external_call",
            "connection_id": conn_id,
            "grant_id": grant_id,
            "verb": "POST",
            "request": {
                "method": "POST",
                "host": hook_host,
                "path": TEMPLATE,  # the PLACEHOLDER, never the token
                # NO User-Agent. It used to carry one to get past Cloudflare's
                # `error code: 1010`, which is the concern this probe's first
                # run filed; the driver now sends the platform's own client
                # string on every call, and a per-call one is REFUSED. So the
                # probe reaching the receiver with no special allowance is
                # itself the proof that the 1010 is gone.
                "headers": {"Content-Type": "application/json"},
                "body": {"proof": "capability-url", "at": int(time.time())},
            },
        }
        result = run_authenticated_external_call_effector(
            node_id="proof",
            output_keys=["packet"],
            run_state={"packet": json.dumps(packet)},
            base_path=f"{BASE}/{universe_id}",
            run_id="capurl-live-proof",
        )
        blob = json.dumps(result)
        status = (result.get("response") or {}).get("status")
        print("\neffector result:", blob[:360])
        check(
            "6. THE WIRE: the real receiver answered 202 (deliverable)",
            result.get("delivered") is True and status == 202,
            f"delivered={result.get('delivered')} status={status} "
            f"error={result.get('error')} kind={result.get('error_kind')}",
        )
        check(
            "7. the effector's returned url holds the PLACEHOLDER",
            result.get("url") == f"https://{hook_host}{TEMPLATE}",
            str(result.get("url")),
        )
        check(
            "8. nothing the effector returns carries any part of the token",
            token not in blob,
        )

        # The negative: a node that hardcodes the real token is refused, so the
        # secret cannot reach the run record through a packet.
        bad = json.loads(json.dumps(packet))
        bad["request"]["path"] = f"/hooks/inbound/{token}"
        refused = run_authenticated_external_call_effector(
            node_id="proof-negative",
            output_keys=["packet"],
            run_state={"packet": json.dumps(bad)},
            base_path=f"{BASE}/{universe_id}",
            run_id="capurl-live-proof-negative",
        )
        check(
            "9. a packet hardcoding the token is refused before the wire",
            refused.get("delivered") is not True
            and refused.get("error_kind") == "capability_url_not_addressed_by_placeholder",
            f"kind={refused.get('error_kind')}",
        )
        check(
            "10. that refusal carries no part of the token either",
            token not in json.dumps(refused),
        )

        # What the 202 enqueued: that branch, as that universe, under the
        # receiver's own run name. The first production run read
        # `branch_tasks_v2` and found nothing -- an inbound trigger lands in the
        # SHARED runs db as a `runs` row keyed by `queue_universe_id`, which is
        # where `enqueue_universe_branch_run` writes it.
        time.sleep(3)
        runs = sqlite3.connect(f"{BASE}/.runs.db")
        runs.row_factory = sqlite3.Row
        try:
            rows = [
                dict(r)
                for r in runs.execute(
                    "SELECT run_id, run_name, actor, queue_universe_id FROM runs "
                    "WHERE branch_def_id = ? ORDER BY rowid DESC LIMIT 3",
                    (BRANCH_ID,),
                )
            ]
        finally:
            runs.close()
        check(
            "11. the receiver enqueued a run of THAT branch as THAT universe",
            bool(rows)
            and rows[0]["queue_universe_id"] == universe_id
            and rows[0]["actor"] == f"universe:{universe_id}"
            and rows[0]["run_name"] == "webhook",
            f"rows={rows}",
        )
    finally:
        cleanup(universe_id, token=token)

    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe",
        required=True,
        help="the universe to prove on -- REQUIRED, no default, and the named "
        "principal must administer it",
    )
    parser.add_argument("--hook-host", default=HOOK_HOST_DEFAULT)
    parser.add_argument(
        "--cleanup", action="store_true", help="remove this probe's artifacts and exit"
    )
    args = parser.parse_args()
    if args.cleanup:
        _login(resolve_admin(args.universe))
        cleanup(args.universe, token=os.environ.get("PROOF_TOKEN", ""))
        raise SystemExit(1 if FAILURES else 0)
    raise SystemExit(main(args.universe, args.hook_host))
