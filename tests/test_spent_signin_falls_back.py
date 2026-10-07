"""The founder's outage, as a test: a spent sign-in must not end the turn.

Live on 2026-09-26. Their ChatGPT subscription's stored refresh token was spent, the
turn stopped, and nothing routed to the model they had configured next.

The mechanism: the pre-launch credential refresh runs inside
`authorize_served_provider_call_async`, which WRAPS `_call_routed`. So the refusal is
raised before the provider loop is entered, the loop's own
`except ProviderAuthenticationError` never sees it, and the exception escaped the turn.
Probed before the fix: `RAISED ProviderAuthenticationError`, turn `abandoned`.
"""

from __future__ import annotations

import pytest

from tests import test_served_model_preferences as prefs

_converse = prefs._converse
rig = prefs.rig
reader = prefs.reader
configured = prefs.configured
served = prefs.served
agent = prefs.agent


def _refuse_before_launch(monkeypatch, dead="codex"):
    """Make the pre-launch refresh refuse for ONE source, as a spent token does.

    Only for ``dead``. An earlier version of this refused for every source and the
    turn still failed -- correctly, because it had then exhausted the owner's whole
    accepted order. The seam already scopes its raise to the source being launched
    (`launching`), and the fixture has to respect that or it is testing a universe
    with no working model in it at all.
    """
    from tinyassets import subscription_refresh
    from tinyassets.exceptions import ProviderAuthenticationError

    seen: list[str] = []

    def refuse(**kwargs):
        launching = str(kwargs.get("launching") or "")
        seen.append(launching)
        if launching == dead:
            raise ProviderAuthenticationError(
                f"{dead} needs you to sign in again: the stored sign-in is spent")

    monkeypatch.setattr(subscription_refresh, "refresh_deposited_subscriptions", refuse)
    return seen


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_a_spent_signin_answers_from_the_next_allowed_model(agent, monkeypatch):
    """The whole point. The turn ANSWERS; it does not stop."""
    # Known finite HTTP capacity makes the native sign-in the first automatic
    # candidate, so its pre-launch failure is actually exercised.
    monkeypatch.setattr("tinyassets.request_budget.requests_today", lambda *a, **kw: (0, 0))
    seen = _refuse_before_launch(monkeypatch)

    reply = _converse(agent, monkeypatch)

    assert "codex" in seen, "the pre-launch refresh never ran for the dead source"
    assert reply, "the turn produced no reply at all"
    # Not from the source whose sign-in is dead.
    assert "codex:" not in reply
    # ...and the turn is finished, not held or abandoned, which is what the founder
    # saw before: a turn that stopped with nothing to show.
    latest = agent.latest()
    assert latest.state not in ("abandoned", "held_native_unknown"), latest.state


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_the_refusal_is_classified_as_a_sign_in_failure_not_an_outage(agent, monkeypatch):
    """The conversion must carry the class, or the router cools the source instead."""
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.router import ProviderRouter, _routable_authorization

    _refuse_before_launch(monkeypatch)

    class _Inner:
        async def __aenter__(self):
            from tinyassets.exceptions import ProviderAuthenticationError

            raise ProviderAuthenticationError("the stored sign-in is no longer accepted")

        async def __aexit__(self, *_a):
            return False

    async def drive():
        async with _routable_authorization(_Inner(), ModelRef("codex", "")):
            pass

    import asyncio

    with pytest.raises(AllProvidersExhaustedError) as caught:
        asyncio.run(drive())
    aggregate = caught.value
    assert aggregate.failure_class == "auth_invalid"
    assert [a.skip_class for a in aggregate.attempts] == ["auth_invalid"]
    # `none` is a FACT here: authorization did not finish, so nothing launched. That
    # is what lets the turn move on without replaying an effect.
    assert [a.side_effect_state for a in aggregate.attempts] == ["none"]
    assert [a.provider for a in aggregate.attempts] == ["codex"]
    assert ProviderRouter  # the helper belongs to this module, not a test shim


@pytest.mark.parametrize("error,converted", [
    ("ProviderAuthenticationError", True),
    ("ProviderAuthorityHeldError", False),
    ("PermissionError", False),
])
def test_only_a_sign_in_refusal_is_converted(error, converted):
    """A held authority or a permission refusal must keep travelling as itself.

    Turning those into "exhausted" would report a provider that was never asked as
    having failed, and would hide the authority problem behind a routing outcome.
    """
    import asyncio

    from tinyassets import exceptions
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.router import _routable_authorization

    raised = getattr(exceptions, error, None) or PermissionError

    class _Inner:
        async def __aenter__(self):
            raise raised("refused")

        async def __aexit__(self, *_a):
            return False

    async def drive():
        async with _routable_authorization(_Inner(), ModelRef("codex", "")):
            pass

    with pytest.raises(AllProvidersExhaustedError if converted else raised):
        asyncio.run(drive())


def test_a_successful_authorization_passes_its_authority_through():
    """The wrapper must be transparent when nothing refuses."""
    import asyncio

    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.router import _routable_authorization

    class _Inner:
        async def __aenter__(self):
            return "the-authority"

        async def __aexit__(self, *_a):
            return False

    async def drive():
        async with _routable_authorization(_Inner(), ModelRef("codex", "")) as got:
            return got

    assert asyncio.run(drive()) == "the-authority"
