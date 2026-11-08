"""The handles canary across the cutover (Fact 11): which side serves ``/mcp``, and its handles.

    python scripts/cutover_handles_canary.py --side auto|old|new [--url URL] [--verbose]

Each side's handle set is data (``canary_handles.json``):

* ``old``: the old stack. Its own full handles canary, ``mcp_public_canary.py
  --assert-handles``, as the canary principal (``TINYASSETS_WIKI_CANARY_TOKEN``):
  the anonymous challenge, exactly the old handle set, ``converse`` refused at
  both boundaries, and ``get_status``'s uptime fields.
* ``new``: the doors. The old canary's token means nothing to them (their ``/mcp``
  takes only their own OAuth grants), so three read-only checks:

  1. an anonymous ``initialize`` is ``401``, its challenge naming the doors' own
     resource document;
  2. that document is the ``/mcp`` resource, and names the doors' authorization
     server;
  3. the handle set, with a probe owner's API key (``TA_PROBE_KEY``): one ``GET
     /api/tools/<name>`` a name. ``platform/doors/src/api.ts`` answers a tool
     outside the key's profile ``404`` before it reads the method, and a ``GET`` on
     one inside ``405``; a ``GET`` never reaches an owner door
     (``doors/test/api-keys.test.ts`` holds both). So every new handle must be
     ``405``, and every old-only handle and an unknown name ``404``.

  With no ``TA_PROBE_KEY`` the handle set is unchecked, and the run fails (exit 7):
  a skip is not a pass.

``auto`` takes the side whose authorization server the public resource document names;
an explicit side must be the one that answers (exit 8 otherwise). Nothing is ever
changed, and no credential is printed.

Exit codes: 0 healthy; 2 unreachable; 4 handle drift; 6 an authentication boundary or a
gate refused the probe; 7 the new side's handle set unchecked (no ``TA_PROBE_KEY``); 8
the side asked for is not the one serving; 9 neither side's authorization server is
named. The old side keeps ``mcp_public_canary.py``'s own codes.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mcp_public_canary as public  # noqa: E402
from mcp_public_canary import CanaryError  # noqa: E402

HANDLES_FILE = _SCRIPTS / "canary_handles.json"
PROBE_KEY_ENV = "TA_PROBE_KEY"
SIDES = ("old", "new")
#: A name no side serves: the 404 a profile answers for a tool outside it.
_UNKNOWN = "no_such_tool"


def handles() -> dict[str, Any]:
    """Each side's record: its handle set and its authorization server."""
    return json.loads(HANDLES_FILE.read_text(encoding="utf-8"))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def resource_document(url: str, timeout: float) -> dict[str, Any]:
    """The public protected-resource document for ``url`` (RFC 9728's path form)."""
    advertised, _legacy = public.resource_metadata_urls(url)
    return public._get_json(advertised, timeout)


def serving_side(url: str, timeout: float) -> str:
    """The side whose authorization server the public resource document names."""
    servers = resource_document(url, timeout).get("authorization_servers")
    record = handles()
    for side in SIDES:
        if servers == [record[side]["authorization_server"]]:
            return side
    raise CanaryError(9, f"{url}'s resource document names {servers!r}, neither side's "
                         "authorization server")


def _get_status(url: str, key: str, timeout: float) -> int:
    """One read-only ``GET`` with the probe key: its HTTP status."""
    request = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
        "User-Agent": "tinyassets-handles-canary/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl.create_default_context()) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        raise CanaryError(2, f"unreachable GET {url}: {exc}") from exc


def assert_new_challenge(url: str, timeout: float) -> None:
    """Anonymous ``initialize`` is ``401``, naming the doors' resource document, which names
    the doors' own authorization server."""
    status, headers, _ = public._post(url, public._INIT_PAYLOAD, timeout,
                                      accepted_http_statuses=frozenset({401}), bearer=None)
    advertised, _legacy = public.resource_metadata_urls(url)
    challenge = headers.get("www-authenticate", "")
    if status != 401 or not challenge.startswith("Bearer ") \
            or f'resource_metadata="{advertised}"' not in challenge:
        raise CanaryError(6, f"{url} did not challenge an anonymous initialize with the doors' "
                             f"resource document: HTTP {status}")
    document = resource_document(url, timeout)
    expected = handles()["new"]["authorization_server"]
    if document.get("resource") != url.rstrip("/") \
            or document.get("authorization_servers") != [expected]:
        raise CanaryError(6, f"{url}'s resource document is not the doors' /mcp resource "
                             f"naming {expected}")


def assert_new_handles(url: str, key: str, timeout: float) -> None:
    """Every new handle served (``405``), every old-only one and an unknown name not
    (``404``), on ``/api/tools/<name>``, read-only."""
    record = handles()
    new = set(record["new"]["handles"])
    absent = (set(record["old"]["handles"]) - new) | {_UNKNOWN}
    base = f"{_origin(url)}/api/tools/"
    missing: list[str] = []
    extra: list[str] = []
    for name in sorted(new | absent):
        status = _get_status(base + name, key, timeout)
        if status == 401:
            raise CanaryError(6, f"the doors refused {PROBE_KEY_ENV} (HTTP 401): it is not a "
                                 "live API key")
        if status == 503:
            raise CanaryError(6, "the doors' gate refused the probe owner (HTTP 503): the "
                                 "control record must admit it")
        if name in new and status != 405:
            missing.append(f"{name} (HTTP {status})")
        elif name in absent and status != 404:
            extra.append(f"{name} (HTTP {status})")
    if missing or extra:
        raise CanaryError(4, f"handle drift on {_origin(url)}: missing={missing} extra={extra}")


def run(side: str, url: str, timeout: float, verbose: bool) -> int:
    """The canary for ``side`` (``auto``: the one serving). Its exit code."""
    try:
        serving = serving_side(url, timeout)
        if side != "auto" and side != serving:
            raise CanaryError(8, f"asked for the {side} side, but the {serving} side serves "
                                 f"{url}")
        if serving == "old":
            # The old stack's own canary, whole; it exits with its own codes.
            try:
                return public.main(["--url", url, "--assert-handles",
                                    *(["--verbose"] if verbose else [])])
            except SystemExit as exit_:
                return exit_.code if isinstance(exit_.code, int) else 1
        assert_new_challenge(url, timeout)
        key = os.environ.get(PROBE_KEY_ENV, "").strip()
        if not key:
            raise CanaryError(7, f"the new side's handle set is unchecked: {PROBE_KEY_ENV} (a "
                                 "probe owner's API key) is not set")
        assert_new_handles(url, key, timeout)
    except CanaryError as exc:
        print(f"[handles-canary] {exc.msg}", file=sys.stderr)
        return exc.code
    if verbose:
        print(f"[handles-canary] OK {url} [the new side: anonymous initialize challenged; the "
              f"doors' resource document; {len(handles()['new']['handles'])} handles served, "
              "the old-only ones not]")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The handles canary across the cutover.")
    parser.add_argument("--side", choices=("auto", *SIDES), default="auto",
                        help="the side to hold; auto: the one the resource document names")
    parser.add_argument("--url", default=public.DEFAULT_URL)
    parser.add_argument("--timeout", type=float, default=public.DEFAULT_TIMEOUT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    return run(args.side, args.url, args.timeout, args.verbose)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
