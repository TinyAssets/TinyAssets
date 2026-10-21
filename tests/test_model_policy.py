"""Policy decisions only: these tests do not prove inference or grant authority."""



from tinyassets.providers.model_policy import (
    Catalog,
    Charge,
    ConnectionModels,
    Interaction,
    Model,
    ModelPolicy,
    Pricing,
    order_models,
)

COMPONENTS = frozenset({"input_million_tokens_usd", "output_million_tokens_usd"})
FREE = Pricing("fresh", tuple(Charge(name, 0, True) for name in sorted(COMPONENTS)))
NEEDS = Interaction(True, frozenset({"text"}), COMPONENTS, min_context=100)
AUTO = ModelPolicy(7, "automatic", (), ranking_source="comparable-v1")


def model(name="opaque-A", *, score=None):
    return Model(name, True, frozenset({"text"}), 1000, FREE, score)


def connection(name="gateway", *, models=None, **kwargs):
    return ConnectionModels(
        name,
        "adapter-scope",
        "http",
        "fresh",
        True,
        True,
        tuple(models or [model()]),
        **kwargs,
    )


def order(connections, policy=AUTO, needs=NEEDS, exhaustion=()):
    return order_models(
        Catalog("owner", "universe", tuple(connections)),
        policy,
        needs,
        owner_id="owner",
        universe_id="universe",
        exhaustion=exhaustion,
    )


def refs(result):
    return [c.ref for c in result.candidates]


























































