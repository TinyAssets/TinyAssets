"""Flat, data-only inputs accepted by paid node bids."""


def validate_node_bid_inputs(inputs: object) -> tuple[bool, str]:
    if not isinstance(inputs, dict):
        return False, "inputs must be a dict"
    rejected = {"_universe_path", "_db_path", "_kg_path", "work_target_ref"}
    for key, value in inputs.items():
        if not isinstance(key, str):
            return False, f"inputs key {key!r} is not a string"
        if key.startswith("_") or key in rejected:
            return False, f"inputs key {key!r} is reserved"
        if not isinstance(value, (str, int, float, bool, type(None))):
            return False, f"inputs value for {key!r} is not a primitive"
    return True, ""
