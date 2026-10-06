"""Bounded native line-metadata transport, never a writer or host session.

Only registered metadata methods are sent. The caller supplies an owned
credential snapshot environment and clean cwd, and retains authority checks.
No thread/turn, implicit inference, shell, stderr relay or partial catalogue.

Two ENVELOPES share this one process boundary, because the boundary is the part
worth hardening: owned-snapshot env, byte/page/model ceilings, no stderr relay,
and a process-group teardown that survives a launcher exiting before its
children. :class:`NativeJsonRpcProtocol` frames integer-id JSON-RPC; agent CLIs
that speak a typed control stream instead use :class:`NativeControlProtocol`.
A new executor registers an envelope here rather than forking the transport.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from tinyassets.exceptions import ProviderError
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.owned_process import (
    FamilyAnchorError,
    OwnerCellProcess,
    aspawn_owned,
    kill_owned_tree,
)
from tinyassets.providers.provider_jail import metadata_view, provider_launch_scope


class NativeMetadataUnsupported(ProviderError):
    """The executor ANSWERED that it does not implement this metadata method.

    Distinct from "discovery failed": a CLI older than the one that added the
    method replies with an explicit unsupported-method error, which is a
    truthful "enumeration is unknown here" rather than a fault. Callers turn it
    into the same None that an executor with no protocol returns, so the picker
    says "enumeration unsupported" instead of implying something broke.

    This is how the feature is DETECTED rather than version-gated: a release
    table would be exactly the static provider-release list this repo refuses.
    Measured 2026-10-02 against the installed CLI: an unknown subtype is
    rejected in 0.6s, so the fallback costs a round trip, not the 30s timeout.
    """


_MAX_BYTES = 4 * 1024 * 1024
_MAX_MODELS = 4096
_MAX_PAGES = 64
_REAP_TIMEOUT = 1
_log = logging.getLogger(__name__)


async def _close_cell_process(proc):
    """Revoke a D82 owner cell; its authenticated receipt is authoritative."""
    proc.stdin.close()
    kill_owned_tree(proc)  # Revocation through the lifetime channel only.
    try:
        await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT + 5)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ProviderError("native model discovery cell receipt unavailable") from None
    finally:
        proc._transport.close()


async def _close_metadata_process(proc):
    """Release pipes and the owned family, including after launcher exit."""
    if isinstance(proc, OwnerCellProcess):
        await _close_cell_process(proc)
        return
    proc.stdin.close()
    # The live family anchor owns its group identity. Closing its handle is
    # synchronous, including when cancellation interrupts the following await.
    kill_owned_tree(proc)
    try:
        # wait() alone returns early when the launcher was already reaped.
        # Observe inherited-pipe EOF too, with the same byte/time ceilings.
        await asyncio.wait_for(
            asyncio.gather(proc.wait(), proc.stdout.read(_MAX_BYTES + 1)),
            timeout=_REAP_TIMEOUT,
        )
    except TimeoutError:
        # An executor that escapes its session must not wedge metadata reads.
        # Closing our pipe ends also releases transport references on the loop.
        _log.warning("native metadata process cleanup exceeded its bound")
    finally:
        # Also synchronous on a second cancellation during reaping.
        proc._transport.close()


def _check_keys(required, optional):
    if (any(type(key) is not str or not key or len(key) > 200 for key in required)
            or any(key is not None and (type(key) is not str or not key or len(key) > 200)
                   for key in optional)):
        raise ValueError("invalid native metadata protocol fields")


class _ModelRowFields:
    """Row-field reads shared by every envelope; no transport or authority here."""

    def _check_row_fields(self):
        _check_keys(
            (self.items_key, self.model_key, self.default_key,
             self.modalities_key, self.hidden_key),
            (self.effort_key, self.effort_levels_key, self.effort_level_key),
        )
        if (type(self.assumed_input_modalities) is not frozenset
                or any(type(item) is not str or not item or len(item) > 100
                       for item in self.assumed_input_modalities)):
            raise ValueError("invalid native metadata assumed modalities")
        # The levels field is what makes effort readable at all. A boolean
        # support gate is OPTIONAL, because sources disagree about whether one
        # exists: Claude Code carries `supportsEffort`, while Codex implies
        # support purely by listing `supportedReasoningEfforts`.
        if self.effort_key is not None and self.effort_levels_key is None:
            raise ValueError("native effort support must name its levels field")
        if self.effort_level_key is not None and self.effort_levels_key is None:
            raise ValueError("native effort entries need a levels field")
        if self.default_match_value is not None and (
            type(self.default_match_value) is not str or not self.default_match_value
            or len(self.default_match_value) > 200
        ):
            raise ValueError("invalid native metadata default marker")

    def is_default(self, row):
        """Whether this row is the executor's recommended default.

        Two shapes exist in the wild: a boolean field per row, and a catalogue
        whose default is a distinguished ALIAS row (Claude Code's ``default``
        entry, carrying the same resolved id as the model it points at).
        """
        if self.default_match_value is not None:
            return row.get(self.default_key) == self.default_match_value
        value = row.get(self.default_key, False)
        if type(value) is not bool:
            raise ValueError("invalid native model entry")
        return value

    def row_modalities(self, row):
        """Reported modalities, or the transport's own floor when UNREPORTED.

        ``assumed_input_modalities`` is a property of the registered transport,
        not an invented account fact: an agent CLI whose only metadata contract
        is text in and text out cannot report ``text`` and must not therefore
        read as a model that accepts nothing. A row that DOES report its
        modalities is always believed as-is, including an empty list.
        """
        if self.modalities_key not in row:
            return frozenset(self.assumed_input_modalities)
        modalities = row[self.modalities_key]
        if type(modalities) is not list or any(type(item) is not str for item in modalities):
            raise ValueError("invalid native input modalities")
        return frozenset(modalities)

    def row_effort(self, row):
        """The executor's own per-model effort answer; never a platform default.

        Two advertised shapes, both live: Claude Code gates on a boolean and
        lists plain level names, while Codex omits the boolean and lists
        objects (``{"reasoningEffort": "high", "description": ...}``). The
        protocol names which, so neither source has to be guessed at.
        """
        if self.effort_levels_key is None:
            return False, ()
        if self.effort_key is not None:
            claimed = row.get(self.effort_key, False)
            if type(claimed) is not bool:
                raise ValueError("invalid native effort support")
            if not claimed:
                return False, ()
        elif self.effort_levels_key not in row:
            # No boolean gate and no levels field: this model has no control.
            return False, ()
        levels = row.get(self.effort_levels_key)
        # Claimed support whose levels are missing or malformed is refused
        # rather than downgraded: a control with invented values is worse than
        # no control, and silently dropping it would hide a protocol change.
        if type(levels) is not list:
            raise ValueError("invalid native effort levels")
        if not levels:
            # An empty list from a source with no boolean gate is a truthful
            # "no levels", not a fault. With a gate, support was claimed and
            # then not named, which is a fault.
            if self.effort_key is None:
                return False, ()
            raise ValueError("invalid native effort levels")
        names = []
        for level in levels:
            if self.effort_level_key is not None:
                if type(level) is not dict:
                    raise ValueError("invalid native effort level entry")
                level = level.get(self.effort_level_key)
            if type(level) is not str:
                raise ValueError("invalid native effort levels")
            names.append(level)
        return True, tuple(names)


@dataclass(frozen=True, slots=True)
class NativeJsonRpcProtocol(_ModelRowFields):
    """Trusted executor metadata contract, not user-supplied executable authority."""

    list_method: str
    items_key: str
    model_key: str
    default_key: str
    modalities_key: str
    hidden_key: str
    cursor_key: str | None = None
    cursor_param: str | None = None
    initialize_method: str | None = None
    initialized_notification: str | None = None
    initialize_params_json: str = "{}"
    list_params_json: str = "{}"
    effort_key: str | None = None
    effort_levels_key: str | None = None
    #: Set when a levels entry is an OBJECT rather than a bare level name;
    #: this is the field inside it holding the name (Codex's `reasoningEffort`).
    effort_level_key: str | None = None
    assumed_input_modalities: frozenset[str] = frozenset()
    default_match_value: str | None = None
    #: A row IS a model here, so a repeated execution id is a contradiction.
    aliased_rows: bool = False

    def __post_init__(self):
        _check_keys((self.list_method,), (self.initialize_method,
                                          self.initialized_notification))
        self._check_row_fields()
        if (self.cursor_key is None) != (self.cursor_param is None):
            raise ValueError("invalid native metadata protocol fields")
        for params in (self.initialize_params_json, self.list_params_json):
            if type(params) is not str or type(json.loads(params)) is not dict:
                raise ValueError("invalid native metadata protocol parameters")

    def request_token(self, index):
        return index

    def encode_request(self, method, token, params):
        return {"method": method, "id": token, "params": params}

    def encode_notification(self, method):
        return {"method": method, "params": {}}

    def decode_response(self, message, token):
        if "id" not in message and type(message.get("method")) is str:
            return None  # A notification, not this request's answer.
        if (type(message.get("id")) is not int or message["id"] != token
                or "error" in message or type(message.get("result")) is not dict):
            raise ValueError("unexpected native discovery response")
        return message["result"]


@dataclass(frozen=True, slots=True)
class NativeControlProtocol(_ModelRowFields):
    """Typed control-stream metadata contract for agent CLIs.

    The envelope is ``{"type": "control_request", "request_id": <str>,
    "request": {"subtype": <method>, ...}}`` answered by a ``control_response``
    carrying its own ``request_id``. Unlike JSON-RPC the id is a STRING and the
    result is nested one level deeper, which is the whole reason this exists as
    a separate envelope rather than a flag on the JSON-RPC one.

    These streams also carry unrelated traffic (session/system/transcript
    lines) before the answer. Those are skipped; the shared byte ceiling is
    what bounds the skipping, so a chatty executor cannot stall the read.
    """

    list_method: str
    items_key: str
    model_key: str
    default_key: str
    modalities_key: str
    hidden_key: str
    cursor_key: str | None = None
    cursor_param: str | None = None
    list_params_json: str = "{}"
    effort_key: str | None = None
    effort_levels_key: str | None = None
    #: Set when a levels entry is an OBJECT rather than a bare level name;
    #: this is the field inside it holding the name (Codex's `reasoningEffort`).
    effort_level_key: str | None = None
    assumed_input_modalities: frozenset[str] = frozenset()
    default_match_value: str | None = None
    #: Rows are selectable ENTRIES, several of which may be aliases resolving to
    #: one execution id. Collapsing agreeing repeats is the designed shape here,
    #: not leniency: refusing them would reject a catalogue that is correct.
    aliased_rows: bool = True
    #: A PLATFORM-OWNED substring that marks an executor's "I do not implement
    #: this method" answer, matched case-insensitively against its error text.
    #: Not relayed anywhere -- it is compared and discarded, so no upstream
    #: prose, path or account material escapes the transport's sanitized exit.
    unsupported_error_marker: str | None = None
    #: No handshake: a control stream answers a metadata request immediately.
    initialize_method: str | None = None
    initialized_notification: str | None = None

    def __post_init__(self):
        _check_keys((self.list_method,), (self.unsupported_error_marker,))
        self._check_row_fields()
        if (self.cursor_key is None) != (self.cursor_param is None):
            raise ValueError("invalid native metadata protocol fields")
        if self.initialize_method is not None or self.initialized_notification is not None:
            raise ValueError("control metadata protocol takes no handshake")
        if type(self.list_params_json) is not str or type(
            json.loads(self.list_params_json),
        ) is not dict:
            raise ValueError("invalid native metadata protocol parameters")

    def request_token(self, index):
        return f"tinyassets_model_discovery_{index}"

    def encode_request(self, method, token, params):
        if "subtype" in params:
            raise ValueError("native metadata parameters cannot restate the method")
        return {"type": "control_request", "request_id": token,
                "request": {"subtype": method, **params}}

    def encode_notification(self, method):
        raise ValueError("control metadata protocol sends no notifications")

    def decode_response(self, message, token):
        if message.get("type") != "control_response":
            return None  # Unrelated stream traffic; bounded by the byte ceiling.
        envelope = message.get("response")
        if type(envelope) is not dict or envelope.get("request_id") != token:
            raise ValueError("unexpected native discovery response")
        # An executor too old to implement the method says so explicitly. That
        # is "enumeration is unknown here", not a fault, and it must not read
        # as a broken source -- the deployed CLI is older than the one that
        # added this method, so this is the path production takes today.
        if (self.unsupported_error_marker is not None
                and envelope.get("subtype") == "error"
                and type(envelope.get("error")) is str
                and self.unsupported_error_marker.lower() in envelope["error"].lower()):
            raise NativeMetadataUnsupported("native model enumeration unsupported")
        # Any other error subtype is refused rather than read as an empty
        # catalogue: "no models" and "the call failed" are different answers.
        if envelope.get("subtype") != "success" or type(envelope.get("response")) is not dict:
            raise ValueError("unexpected native discovery response")
        return envelope["response"]


def parse_model_page(result, protocol):
    if type(result) is not dict or type(result.get(protocol.items_key)) is not list:
        raise ValueError("invalid native model page")
    cursor = result.get(protocol.cursor_key) if protocol.cursor_key is not None else None
    if cursor is not None and (type(cursor) is not str or not cursor or len(cursor) > 4096):
        raise ValueError("invalid native model cursor")
    models, defaults, seen = [], [], {}
    for row in result[protocol.items_key]:
        if type(row) is not dict:
            raise ValueError("invalid native model entry")
        # Only the registered execution-ID field is used, never a display label.
        supports_effort, levels = protocol.row_effort(row)
        model = NativeModel(row.get(protocol.model_key), protocol.row_modalities(row),
                            row.get(protocol.hidden_key, False), supports_effort, levels)
        if protocol.is_default(row):
            defaults.append(model.model_id)
        # Only an ALIASED catalogue may repeat an execution id, and only when
        # the repeat agrees. Where a row IS its model (plain JSON-RPC), a
        # repeated id stays a conflict and is refused downstream rather than
        # quietly collapsed -- two rows for one model there means the executor
        # contradicted itself.
        if protocol.aliased_rows:
            if model.model_id in seen:
                if seen[model.model_id] != model:
                    raise ValueError("conflicting native metadata for one model")
                continue
            seen[model.model_id] = model
        models.append(model)
    return models, defaults, cursor


async def read_native_catalogue(
    argv, *, protocol, env, cwd, universe_dir=None, timeout=30,
    spawn_kwargs=None, install_mounts=None, auth_env_names=(),
):
    """Read the complete bounded list or fail with sanitized fixed prose.

    Resource ceilings are transport guards, never silent list truncation. New
    optional fields and notifications are tolerated; invalid required facts,
    duplicate models, repeated cursors and upstream errors refuse the result.

    ``auth_env_names`` carries the caller's own auth directory variable names
    down to the jail, which is channel-agnostic and names no vendor itself.
    """
    proc = None
    try:
        if type(protocol) not in (NativeJsonRpcProtocol, NativeControlProtocol):
            raise ValueError("native metadata requires a registered protocol")
        if universe_dir is None:
            raise ValueError("native metadata requires its owning command center")
        view = metadata_view(universe_dir, cwd, env, auth_env_names)
        process_options = dict(spawn_kwargs or {})
        # Session ownership belongs to the shared family launcher. Adapters
        # cannot replace its view, scope, shell mode or confinement requirement.
        process_options.pop("start_new_session", None)
        from tinyassets import role_decoder
        from tinyassets.broker.supervisor import broker_selected

        async with asyncio.timeout(timeout):
            if role_decoder._bounded_client is not None or broker_selected():
                # D82: a selected broker runs metadata only in the dedicated
                # owner's provider-discovery cell; refusal, never fallback.
                from tinyassets.role_provider_discovery import aspawn_cell

                try:
                    proc = await aspawn_cell(argv, env=env, view=view,
                                             universe_dir=universe_dir,
                                             snapshot_dir=cwd, limit=_MAX_BYTES)
                except (PermissionError, RuntimeError, KeyError) as exc:
                    raise ProviderError("native model discovery cell refused") from exc
            else:
                with provider_launch_scope(universe_dir, credential_dir=cwd):
                    proc = await aspawn_owned(
                        argv, env=env, cwd=cwd, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        limit=_MAX_BYTES, universe_view=view, require_confinement=True,
                        install_mounts=install_mounts, **process_options,
                    )
            consumed = 0

            async def send(message):
                proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
                await proc.stdin.drain()

            async def response(token):
                nonlocal consumed
                while True:
                    line = await proc.stdout.readline()
                    consumed += len(line)
                    if not line or consumed > _MAX_BYTES:
                        raise ValueError("native discovery incomplete or oversized")
                    message = json.loads(line)
                    if type(message) is not dict:
                        raise ValueError("invalid native discovery envelope")
                    # The envelope decides what is unrelated traffic and what is
                    # a protocol fault. Skipping stays bounded by _MAX_BYTES.
                    result = protocol.decode_response(message, token)
                    if result is not None:
                        return result

            if protocol.initialize_method is not None:
                handshake = protocol.request_token(0)
                await send(protocol.encode_request(
                    protocol.initialize_method, handshake,
                    json.loads(protocol.initialize_params_json),
                ))
                await response(handshake)
            if protocol.initialized_notification is not None:
                await send(protocol.encode_notification(protocol.initialized_notification))
            models, defaults, cursors = [], [], set()
            cursor = None
            for index in range(1, _MAX_PAGES + 1):
                params = json.loads(protocol.list_params_json)
                if cursor is not None:
                    params[protocol.cursor_param] = cursor
                token = protocol.request_token(index)
                await send(protocol.encode_request(protocol.list_method, token, params))
                page, recommended, cursor = parse_model_page(await response(token), protocol)
                models.extend(page)
                defaults.extend(recommended)
                if len(models) > _MAX_MODELS or len(set(defaults)) > 1:
                    raise ValueError("native catalogue limit or conflicting defaults")
                if cursor is None:
                    return NativeCatalogue(
                        tuple(models), defaults[0] if defaults else None,
                        datetime.now(timezone.utc),
                    )
                if cursor in cursors:
                    raise ValueError("native catalogue cursor repeated")
                cursors.add(cursor)
            raise ValueError("native catalogue page limit exceeded")
    except NativeMetadataUnsupported:
        # The executor ANSWERED "I do not implement this". That is a truthful
        # unknown, not a fault, and `BaseProvider.enumerate_models` turns it
        # into None so the source keeps its own default. This clause MUST come
        # first: the sanitizing handler below catches ProviderError, which this
        # subclasses, and would widen the honest answer into a generic failure.
        raise
    except (OSError, ValueError, ProviderError, FamilyAnchorError,
            TimeoutError, asyncio.LimitOverrunError):
        # Never relay process output, errors, paths or account material.
        raise ProviderError("native model discovery unavailable") from None
    finally:
        if proc is not None:
            await _close_metadata_process(proc)
