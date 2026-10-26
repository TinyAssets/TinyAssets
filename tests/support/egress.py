"""A real admitted owner for standalone proxy transport tests."""
from pathlib import Path

import pytest

from tests.support.admission import drive, projection
from tinyassets.broker.egress import identity
from tinyassets.broker.owner_identities import OwnerIdentities


@pytest.fixture(autouse=True)
def admitted_egress(tmp_path):
    state = Path(tmp_path) / "egress-authority"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True)
    identities.resolve("transport-owner", allocate=True)
    drive(identities.admit("transport-owner", routing=projection(identities.path)))
    from tinyassets.broker.policy import PolicyStore

    store = PolicyStore(tmp_path, identities)
    store.bootstrap()
    binding = (identities, "transport-owner", store, None)
    token = identity.set(binding)
    try:
        yield binding
    finally:
        identity.reset(token)
