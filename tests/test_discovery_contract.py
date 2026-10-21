"""One unfamiliar source composes all interpreters without registry mutation.

No connection publication, network authority or live provider claims.
"""

from copy import deepcopy


from tests.test_discovery_catalogue_shapes import (
    BENCHMARK,
    CATALOGUE,
    PRICES,
)
from tests.test_discovery_execution_shapes import REQUEST, UNITS
from tests.test_discovery_quantities import STANDARD


def descriptor():
    document = {
        "version": 1,
        "transport": {"protocol": "openai_chat", "auth_scheme": "bearer",
                      "catalogue_path": "/my/catalogue", "catalogue_query": "owned=1",
                      "benchmark_path": "/my/evaluations"},
        "catalogue": deepcopy(CATALOGUE), "prices": deepcopy(PRICES),
        "benchmark": {**BENCHMARK, "score_schema": "index-v3"},
        "inference": deepcopy(REQUEST), "quantity_model": deepcopy(STANDARD),
        "extension_quantities": {"/billing/enforce": "quantity_neutral"},
        "charge_bindings": dict(zip(UNITS, ("input_tokens", "output_tokens", "requests"))),
        "capacity": {"cases": [{"status": 418, "scope": "unknown",
                                 "reason": "provider_rate_limited"}], "retry_after": True},
        "usage": {"cost": "/meter/usd", "encoding": "number", "scale": 10**6},
        "price_bound_basis": "source_request_caps",
    }
    document["inference"]["constants"].append({
        "path": "/samples", "value": 8, "charge_components": [UNITS[1]],
    })
    document["extension_quantities"]["/samples"] = ["output_tokens"]
    document["quantity_model"]["quantities"]["output_tokens"]["output"] = 8
    return document






























