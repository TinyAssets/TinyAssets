"""Reviewed private imports copied through BoxProvider under a fresh inert folder.

The package pin is the durable owner approval and retry journal. Imported bytes
never replace a live harness or install a hook. No process is started here.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile

from tinyassets import command_center_packages as packages
from tinyassets.boxes.provider import FileWrite, WriteConflict, WriteMode
from tinyassets.okf.bundle import PAGE_BYTES, InvalidBundle, capture_files
from tinyassets.okf.transfer import MANIFEST, _unique_object, ingest_zip


def _text(data):
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return None
    return None if "\x00" in text else text


def _scope(handle):
    return {"owner": handle.account_id, "center": handle.command_center_id, "epoch": handle.epoch}


def _bundle(base, handle, import_id):
    if not isinstance(import_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", import_id):
        raise InvalidBundle("Invalid private import reference")
    from tinyassets.storage import _connect as author_connection
    from tinyassets.storage.current_home import check_principal_not_deleted

    with author_connection(base) as author:
        author.execute("BEGIN")
        check_principal_not_deleted(author, handle.account_id)
    with packages._db(base) as conn:
        row = conn.execute(
            "SELECT blob_sha256 FROM bundle_imports WHERE import_id=? "
            "AND owner_id=? AND command_center_id=? AND epoch=?",
            (import_id, handle.account_id, handle.command_center_id, handle.epoch),
        ).fetchone()
    if row is None:
        raise InvalidBundle("Private import is unavailable for this owner and placement")
    sha = row["blob_sha256"]
    try:
        doc = json.loads(packages.read_blob(base, sha), object_pairs_hook=_unique_object)
        if set(doc) != {"format_version", "manifest", "files"} or doc["format_version"] != 1:
            raise ValueError
        files = {
            path: base64.b64decode(value, validate=True) for path, value in doc["files"].items()
        }
        files[MANIFEST] = json.dumps(doc["manifest"], sort_keys=True).encode()
        # Reuse the ingestion boundary: package-only profile validation is intentionally
        # not widened to grant published-package installation to private imports.
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for path, data in files.items():
                out.writestr(path, data)
        bundle = ingest_zip(archive.getvalue())
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidBundle("Invalid private import custody") from exc
    return sha, bundle.files


def preview_import(base, provider, handle, import_id):
    sha, files = _bundle(base, handle, import_id)
    # A single immutable bundle is copied below an inert namespace. Installing
    # its settings into the recipient's active roster is a separate operation.
    prefix = "imports/" + import_id
    if provider.stat(handle, "/cc/" + prefix) is not None:
        raise InvalidBundle("This import destination already exists")
    if any(len(data) > PAGE_BYTES for data in files.values()):
        raise InvalidBundle("An import file exceeds the provider write envelope")
    generation = provider.read_many(handle, [], max_total=0).generation
    entries = [
        {
            "path": path,
            "to": prefix + "/" + path,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for path, data in sorted(files.items())
    ]
    record = {
        "type": "private-import",
        "scope": _scope(handle),
        "import_id": import_id,
        "sha256": sha,
        "generation": generation,
        "files": entries,
        "destination": prefix,
    }
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    existing = packages.open_pins(
        base, universe_id=handle.command_center_id, kind="install", agent="", digest=digest
    )
    request = (
        existing[0]
        if existing
        else packages.pin(
            base,
            universe_id=handle.command_center_id,
            kind="install",
            agent="",
            digest=digest,
            record=record,
            owner_id=handle.account_id,
        )
    )

    display = []
    for entry in entries:
        content = _text(files[entry["path"]])
        display.append({**entry, "content": content, "binary": content is None})
    return {
        "request": request,
        "destination": prefix,
        "files": display,
        "state": "awaiting_confirmation",
        "runs_code": False,
    }


def activate_import(base, provider, handle, request, *, named_binaries=()):
    pin = packages.pin_for_request(base, universe_id=handle.command_center_id, request_id=request)
    if (
        not pin
        or pin["kind"] != "install"
        or pin["record"].get("type") != "private-import"
        or pin["record"]["scope"] != _scope(handle)
    ):
        raise InvalidBundle("Import confirmation does not belong to this owner and placement")
    record = pin["record"]
    sha, files = _bundle(base, handle, record["import_id"])
    if sha != record["sha256"]:
        raise InvalidBundle("Import custody changed after preview")
    binaries = {path for path, data in files.items() if _text(data) is None}
    if len(set(named_binaries)) != len(named_binaries) or set(named_binaries) != binaries:
        raise InvalidBundle("Name every uninspectable binary before copying")
    generation = provider.read_many(handle, [], max_total=0).generation
    if pin["state"] == "pinned" and generation != record["generation"]:
        raise WriteConflict("Destination changed after import preview")
    state, token = packages.claim(base, universe_id=handle.command_center_id, pin_id=pin["pin_id"])
    if state == "activated":
        return packages.pin_progress(
            base, universe_id=handle.command_center_id, pin_id=pin["pin_id"]
        )
    progress = packages.pin_progress(
        base, universe_id=handle.command_center_id, pin_id=pin["pin_id"]
    )
    completed = progress.get("completed", [])
    try:
        for entry in record["files"]:
            path = entry["path"]
            if path in completed:
                continue
            operation = "import-" + pin["pin_id"] + "-" + hashlib.sha256(path.encode()).hexdigest()
            # Persist intent before the mutation. Explicit retries use the same
            # operation id; an unknown host journal outcome stays blocked there.
            progress = {
                "completed": completed,
                "attempting": path,
                "destination": record["destination"],
                "state": "copying",
                "runs_code": False,
            }
            packages.record_progress(
                base,
                universe_id=handle.command_center_id,
                pin_id=pin["pin_id"],
                progress=progress,
                token=token,
            )
            result = provider.write(
                handle,
                operation,
                "/cc/" + entry["to"],
                files[path],
                max_bytes=PAGE_BYTES,
                mode=WriteMode.CREATE,
            )
            if (
                not isinstance(result, FileWrite)
                or result.path != "/cc/" + entry["to"]
                or result.size != entry["size"]
            ):
                raise InvalidBundle("Invalid provider import write receipt")
            completed = [*completed, path]
            progress = {**progress, "completed": completed, "attempting": None}
            packages.record_progress(
                base,
                universe_id=handle.command_center_id,
                pin_id=pin["pin_id"],
                progress=progress,
                token=token,
            )
        expected = {"/cc/" + entry["to"]: files[entry["path"]] for entry in record["files"]}
        actual = capture_files(
            provider, handle, list(expected), max_total=sum(map(len, expected.values()))
        )
        if actual.missing or dict(actual.files) != expected:
            raise WriteConflict("Copied files changed; inspect before confirming again")
        progress = {**progress, "state": "copied"}
        packages.finish(
            base,
            universe_id=handle.command_center_id,
            pin_id=pin["pin_id"],
            progress=progress,
            token=token,
        )
        return progress
    finally:
        packages.unclaim(
            base, universe_id=handle.command_center_id, pin_id=pin["pin_id"], token=token
        )
