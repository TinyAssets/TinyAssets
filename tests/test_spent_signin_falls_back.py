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
