"""Explicit test transport for the legacy fantasy graph's model prompts."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.providers.execution_receipt import receipt_from_response
from tinyassets.providers.model_policy import ModelRef


def answer(prompt: str, system: str = "", **_kwargs: object) -> str:
    if "fiction writing self-critic" in system:
        return "Critique: " + prompt.split("# Judge Feedback", 1)[-1].split("# Task", 1)[0].strip()
    if "fiction writing coach" in system:
        return "Reflection on " + prompt.split("# Critique", 1)[-1].split("# Task", 1)[0].strip()
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


class ConnectedFantasySource:
    """Owner-bound deterministic test source at the model-call executor seam."""

    name = "connected-test-source"
    model = "deterministic-v1"

    def __init__(self, center: Path) -> None:
        self.context = UniverseContext(
            universe_dir=center,
            served_provider=SimpleNamespace(owner_user_id="test-owner"),
            model_selection=ModelRef(self.name, self.model),
        )
        self.receipts = []

    def call_sync(self, role, prompt, system, config, *, operation, universe_context):
        assert operation == "run_graph"
        assert universe_context.served_provider.owner_user_id == "test-owner"
        assert universe_context.model_selection == ModelRef(self.name, self.model)
        response = ProviderResponse(
            text=answer(prompt, system), provider=self.name, model=self.model,
            reported_model=self.model, family="test", latency_ms=0.0,
            input_tokens=1, output_tokens=1,
        )
        receipt = receipt_from_response(response, requested=universe_context.model_selection)
        self.receipts.append(receipt)
        return replace(response, execution_receipt=receipt)
