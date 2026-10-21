"""Pure execution contract prerequisites, not live source admission evidence."""




UNITS = ("input_million_tokens_usd", "output_million_tokens_usd", "request_usd")
REQUEST = {
    "required": ["model", "messages"], "allowed": ["model", "messages", "max_tokens"],
    "caps": {component: {"path": f"/billing/ceilings/{index}", "divisor": 1000000}
             for index, component in enumerate(UNITS)},
    "constants": [{"path": "/billing/enforce", "value": {"strict": True},
                   "charge_components": ["request_usd"]}],
    "model_exclusions": {"prefixes": ["alias:"], "suffixes": [":network"],
                         "substrings": ["@preset/"]},
}


def caps(input=2, output=3, request=4):
    return tuple(zip(UNITS, (input, output, request)))


def validate(body):
    assert body["messages"] == [{"role": "user", "content": "hello"}]


def body(model="unseen-source/model-2030"):
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}
