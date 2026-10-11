"""Explicit test transport for the legacy fantasy graph's model prompts."""

import json


def answer(prompt: str, system: str = "", **_kwargs: object) -> str:
    if "Choose only tools" in prompt:
        return '{"tools": []}'
    if "beat sheet" in system.lower() or "beat sheets" in prompt.lower():
        beats = [
            {"beat_number": index, "description": description, "tension": tension}
            for index, description, tension in (
                (1, "Ryn approaches the Northern Gate.", 0.3),
                (2, "Ryn meets a guard.", 0.6),
                (3, "Ryn chooses to cross.", 0.9),
                (4, "Ryn enters the city.", 0.5),
            )
        ]
        return json.dumps({"alternatives": [{
            "alternative_id": 0, "beats": beats,
            "done_when": "Ryn crosses the gate.",
            "promise_resolutions": [], "estimated_word_count": 1200,
        }]})
    if "scene beats" in system.lower() or "scene prose" in prompt.lower():
        return (
            "Ryn approached the Northern Gate through the mist. "
            "She paused, listened to the guard, and chose to cross. "
        ) * 20
    if "extract" in system.lower() or "facts" in prompt.lower():
        return "[]"
    return "{}"
