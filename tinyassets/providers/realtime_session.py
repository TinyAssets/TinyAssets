"""One owner session. Each turn is one streaming model call and one receipt.

Cancellation goes through S3 (``TurnBridge.cancel_execution``) and stops only
the current turn. Input audio is not kept. Committed output follows N9.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass, replace

from tinyassets.providers.media_budget import BudgetExhausted, MediaCap
from tinyassets.providers.media_parts import ModelMediaError


class CapabilityGap(Exception):
    def __init__(self, source_id: str, media: str, reason: str):
        self.source_id = source_id
        self.media = media
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class RealtimeSource:
    id: str
    rank: int
    contract: dict


def resolve_realtime(sources: tuple[RealtimeSource, ...] | list[RealtimeSource],
                     media: str) -> RealtimeSource:
    """Strongest connected source only. A gap is not a silent switch."""
    if media not in {"text", "audio", "video"}:
        raise CapabilityGap("", media, "unsupported")
    ordered = tuple(sorted(sources, key=lambda source: (-source.rank, source.id)))
    if not ordered:
        raise CapabilityGap("", media, "no_source")
    strongest = ordered[0]
    contract = strongest.contract if isinstance(strongest.contract, dict) else {}
    directions = contract.get("realtime")
    spec = directions.get(media) if isinstance(directions, dict) else None
    incoming = spec.get("in") if isinstance(spec, dict) else None
    if not isinstance(incoming, list) or not incoming:
        raise CapabilityGap(strongest.id, media, "unsupported")
    return strongest


def sources_for(owner: str, universe_dir) -> tuple[RealtimeSource, ...]:
    """Declared realtime contracts already on connected source records.

    Absent media is a visible gap. This does not dial and does not rank a
    weaker source above the one the model policy already selected.
    """
    del owner, universe_dir
    return ()


class RealtimeSession:
    def __init__(self, *, owner: str, command_center: str, parent_usage_id: str,
                 root, cap: MediaCap | None = None):
        from tinyassets.providers.model_call import Parent

        self.owner = owner
        self.command_center = command_center
        self.parent = Parent(owner, command_center, parent_usage_id)
        self.root = root
        self.cap = cap
        self.history: list[dict] = []
        self.receipts: list = []
        self.current: str | None = None
        self.cancel_event = threading.Event()
        self._cancel = None
        self.blocked = False
        self.disconnect: dict | None = None

    def bind_s3(self, bridge) -> None:
        """Interruption cancels only the turn S3 is executing."""
        self._cancel = bridge.cancel_execution

    def interrupt(self) -> str | None:
        turn = self.current
        self.cancel_event.set()
        if self._cancel is not None and turn is not None:
            self._cancel(turn)
        return turn

    def note_disconnect(self, *, request_left_broker: bool) -> dict:
        self.disconnect = {
            "side_effect_state": "unknown" if request_left_broker else "none",
            "resend": False,
            "history": list(self.history),
        }
        return self.disconnect

    async def commit_turn(self, transcript: str, *, context, model=None,
                          audio: bytes | None = None):
        """One streaming ``model_call``. ``audio`` is capture residue and is not stored."""
        del audio
        if self.blocked:
            raise BudgetExhausted(None)
        if not isinstance(transcript, str):
            raise ValueError("committed input is a transcript")
        self.cancel_event.clear()
        turn_id = secrets.token_hex(8)
        self.current = turn_id
        self.history.append({"text": transcript})
        from tinyassets.providers.model_call import ModelCall, ModelContext, call

        try:
            media_cap = self.cap if self.cap is not None else context.media_cap
            if not isinstance(context, ModelContext):
                raise TypeError("realtime turn requires a model context")
            request_context = replace(
                context, streaming=True, media_cap=media_cap,
                cancel_event=self.cancel_event, prompt=transcript,
            )
            result = await call(ModelCall(model, (), request_context), self.parent)
        except ModelMediaError as exc:
            if exc.result is None:
                raise
            result = exc.result
        except BudgetExhausted:
            self.blocked = True
            raise
        finally:
            self.current = None
        if self.cancel_event.is_set():
            result = _mark(result, "cancelled")
        media = result.receipt.media or {}
        exhausted = result.receipt.outcome == "budget_exhausted"
        if exhausted or media.get("outcome") == "budget_exhausted":
            self.blocked = True
        self.receipts.append(result.receipt)
        return result


def _mark(result, outcome: str):
    from tinyassets.providers.model_call import ModelResult

    media = dict(result.receipt.media or {"part_refs": [], "usage": "measured"})
    media["outcome"] = outcome
    receipt = replace(result.receipt, outcome=outcome, media=media)
    return ModelResult(result.response, receipt, result.parts)


def status_document(sources, media: str = "audio") -> dict:
    try:
        selected = resolve_realtime(tuple(sources), media)
    except CapabilityGap as exc:
        return {
            "available": False, "admitted": False, "state": "incompatible",
            "reason": exc.reason, "gap": exc.reason, "media": media,
            "source": exc.source_id,
        }
    return {
        "available": True, "admitted": True, "state": "ready", "media": media,
        "source": selected.id, "gap": None,
        "contract": selected.contract,
        "disclosure_id": hashlib.sha256(selected.id.encode()).hexdigest(),
        "service_name": "this command center",
        "remediation": "none",
    }


def read_part(root, owner: str, reference: str) -> bytes:
    from tinyassets.storage.owner_media import OwnerMediaStore

    return OwnerMediaStore(root).read(owner, reference)
