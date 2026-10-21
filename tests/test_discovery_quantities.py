"""Declared aggregate quantities: exact arithmetic, not remote semantics proof."""




UNITS = ("input_million_tokens_usd", "output_million_tokens_usd", "request_usd")
STANDARD = {"version": 1, "quantities": {
    "input_tokens": {"input": 1, "output": 0, "per_attempt": 0, "fixed": 0},
    "output_tokens": {"input": 0, "output": 1, "per_attempt": 0, "fixed": 0},
    "requests": {"input": 0, "output": 0, "per_attempt": 1, "fixed": 0},
}}


def caps(input=0, output=0, request=0):
    return tuple(zip(UNITS, (input, output, request)))
