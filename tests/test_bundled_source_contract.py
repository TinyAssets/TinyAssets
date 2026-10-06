"""Bundled discovery documents: detached data and closed user documents."""


import pytest

from tests.test_discovery_contract import descriptor
from tinyassets.providers.discovery_contract import SourceContract
from tinyassets.providers.discovery_presets import (
    bundled_discovery_documents,
    compatibility_document,
)
from tinyassets.providers.discovery_protocols import DiscoveryProtocol, discovery_protocol

KEY = "openrouter_user_models_v1"
NEW = discovery_protocol(KEY)
CAPS = tuple((name, 123456789) for name in sorted(NEW.price_components))


def test_bundled_data_detaches_and_alternate_layout_uses_shared_interpreters():
    document = compatibility_document()
    document["transport"]["catalogue_path"] = "/different/catalogue"
    document["inference"]["caps"]["image_usd"]["path"] = "/ceilings/image"
    document["capacity"]["cases"] = [{"status": 418, "scope": "model",
                                       "reason": "provider_overloaded"}]
    other = DiscoveryProtocol.from_bundled_document(document)
    encoded = other.constrain_inference({"model": "future", "messages": []}, CAPS)
    assert encoded["ceilings"]["image"] == "123.456789"
    assert other.capacity_decoder(418, None).scope == "model"
    assert NEW.capacity_decoder(418, None) is None
    original = bundled_discovery_documents()[KEY]["transport"]["catalogue_path"]
    assert original != "/different/catalogue"


@pytest.mark.parametrize("key", ["legacy", "compatibility_default", "account_filtered"])
def test_user_document_cannot_enable_bundled_trust_or_compatibility(key):
    document = descriptor()
    document[key] = True
    with pytest.raises(ValueError):
        SourceContract.compile(document)
