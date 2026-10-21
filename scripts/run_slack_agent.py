"""Run one universe's Slack agent.

    python scripts/run_slack_agent.py --universe-id <id> [--connection slack-main]

Everything it needs beyond those two arguments is derived, not configured. The
universe's directory comes from its id through the canonical resolver, so a
config can never name one universe while reading another's credentials. The
workspace id and the bot's own user id both come from `auth.test` at startup,
using the credential already in the vault — so there is nothing to copy between
the deposit step and this one, and therefore nothing to copy wrongly. Getting
`bot_user_id` wrong in particular is not a visible error: the agent simply stops
recognising its own messages and answers itself in a loop.

Ctrl-C stops it. The socket is an outbound WebSocket, so this works behind a
firewall with no public endpoint and no inbound port.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinyassets.effectors.slack_agent_service import (  # noqa: E402
    SlackAgentConfig,
    SlackAgentConfigError,
    run_slack_agent,
)
from tinyassets.effectors.slack_errors import safe_error_code  # noqa: E402
from tinyassets.effectors.slack_socket_mode import (  # noqa: E402
    app_id_from_token,
)

from tinyassets.credential_vault import (  # noqa: E402
    resolve_slack_app_token,
    resolve_slack_token,
)

AUTH_TEST_URL = "https://slack.com/api/auth.test"

logger = logging.getLogger("slack-agent")


def identify(bot_token: str) -> tuple[str, str, str]:
    """Ask Slack who we are: (team_id, bot_user_id, team_name)."""
    request = urllib.request.Request(  # noqa: S310 - fixed https Slack endpoint
        AUTH_TEST_URL,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    # Raised after the handler exits: `from None` leaves the token-bearing
    # URLError on __context__. And Slack's in-band `error` is upstream text
    # that has been seen quoting the credential, so it is allow-listed.
    result = None
    failure = ""
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        failure = f"Slack returned HTTP {exc.code} for auth.test"
    except Exception:  # noqa: BLE001
        failure = "Could not reach Slack to identify this app"
    if failure:
        raise SystemExit(failure)
    if not result.get("ok"):
        code = safe_error_code(result.get("error"), default="unknown_error")
        raise SystemExit(f"Slack rejected the bot token: {code}")
    return (
        str(result.get("team_id") or ""),
        str(result.get("user_id") or ""),
        str(result.get("team") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one universe's Slack agent.")
    parser.add_argument(
        "--universe-id",
        required=True,
        help="the universe to run; its directory is derived from this",
    )
    parser.add_argument("--connection", default="slack-main")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    universe_id = args.universe_id.strip()
    try:
        # Built once with placeholder identity purely to resolve the directory
        # through the canonical resolver — which carries the path-traversal
        # guard — then rebuilt below with the identity Slack reports.
        universe_dir = SlackAgentConfig(
            universe_id=universe_id,
            connection_id=args.connection,
            team_id="pending",
            bot_user_id="pending",
        ).universe_dir
    except SlackAgentConfigError as exc:
        raise SystemExit(str(exc)) from None
    if not universe_dir.is_dir():
        raise SystemExit(
            f"Universe {universe_id!r} resolves to {universe_dir}, "
            "which does not exist."
        )

    bot_token = resolve_slack_token(universe_dir, args.connection)
    if not bot_token:
        raise SystemExit(
            f"No Slack bot token for connection {args.connection!r} in {universe_dir}.\n"
            "Provision this connection through the connection door before running this diagnostic."
        )

    team_id, bot_user_id, team_name = identify(bot_token)
    logger.info(
        "identified as bot %s in workspace %s (%s)",
        bot_user_id,
        team_name or team_id,
        team_id,
    )

    # Derived, not configured. A review found the api_app_id check was opt-in
    # and production never set it — so a vault pairing App A's bot token with
    # App B's app token would receive B's events and answer as A. The app token
    # carries its own app id, so there is nothing for an operator to look up.
    api_app_id = app_id_from_token(
        resolve_slack_app_token(universe_dir, args.connection)
    )
    if api_app_id:
        logger.info("enforcing events for app %s only", api_app_id)
    else:
        logger.warning(
            "could not derive the app id from the app-level token; "
            "cross-app event filtering is OFF"
        )

    try:
        # Rebuilt with the real identity now that Slack has told us who we are.
        config = SlackAgentConfig(
            universe_id=universe_id,
            connection_id=args.connection,
            team_id=team_id,
            bot_user_id=bot_user_id,
            api_app_id=api_app_id,
        )
    except SlackAgentConfigError as exc:
        raise SystemExit(str(exc)) from None

    print(f"Listening as {bot_user_id} for universe {universe_id}. Ctrl-C to stop.")
    try:
        handled = asyncio.run(run_slack_agent(config))
    except SlackAgentConfigError as exc:
        # Fail loudly and specifically: this is the "up but answering nobody"
        # class, and the whole point is that it is visible at startup.
        raise SystemExit(f"Cannot start: {exc}") from None
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\nStopped.")
        return 0
    print(f"Socket closed after handling {handled} event(s).")
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main())
