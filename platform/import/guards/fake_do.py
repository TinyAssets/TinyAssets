#!/usr/bin/env python3
"""host.py against a fake DigitalOcean and a fake Cloudflare, for host.test.ts.

  fake_do.py <state.json> <host.py arguments...>

The state file is the fake account: `droplets`, `reserved_ips` (address -> region and
droplet id), `keys`, `images`, `zones`, `now` (ISO), `fail` (a list of "METHOD path" calls
that answer 500), and `calls`, every request made ("METHOD path" and its JSON body). A made
Droplet answers `new` once, then `active`. Nothing here reaches a network; ssh, ssh-keyscan
and ssh-keygen are the fake host's (`fake-host.sh`), by PATH.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("host", HERE.parent / "host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

state_file = Path(sys.argv[1])
state = json.loads(state_file.read_text(encoding="utf-8"))
state.setdefault("calls", [])


def answer(status: int, body: object | None) -> bytes:
    if status >= 400:
        raise host.DigitalOceanRequestError(status=status, diagnostic=f"id=fake; message={status}")
    return b"" if body is None else json.dumps(body).encode()


def fake(method: str, url: str, data: bytes | None) -> bytes:
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "api.digitalocean.com", url
    path = parts.path.removeprefix("/v2")
    query = {name: values[0] for name, values in parse_qs(parts.query).items()}
    body = None if data is None else json.loads(data)
    state["calls"].append({"call": f"{method} {path}", "query": query, "body": body})
    if f"{method} {path}" in state.get("fail", []):
        return answer(500, None)
    droplets = state.setdefault("droplets", [])
    if method == "GET" and path == "/droplets":
        tag = query.get("tag_name")
        return answer(200, {"droplets": [d for d in droplets if tag in d["tags"]],
                            "links": {}, "meta": {"total": 0}})
    if path.startswith("/droplets/"):
        found = [d for d in droplets if str(d["id"]) == path.split("/")[2]]
        if not found:
            return answer(404, None)
        if method == "GET":
            droplet = found[0]
            shown = dict(droplet)
            if droplet["status"] == "new":
                droplet["status"] = "active"
            return answer(200, {"droplet": shown})
        if method == "DELETE":
            droplets.remove(found[0])
            for held in state.get("reserved_ips", {}).values():
                if held.get("droplet") == found[0]["id"]:
                    held["droplet"] = None
            return answer(204, None)
    if method == "POST" and path == "/droplets":
        state["next_id"] = state.get("next_id", 9000) + 1
        droplet = {"id": state["next_id"], "name": body["name"], "tags": body["tags"],
                   "status": "new", "created_at": state["now"], "region": {"slug": body["region"]}}
        droplets.append(droplet)
        state.setdefault("made", []).append(body)
        return answer(202, {"droplet": dict(droplet)})
    if method == "GET" and path == "/account/keys":
        return answer(200, {"ssh_keys": state.get("keys", [])})
    if method == "GET" and path == "/images":
        return answer(200, {"images": state.get("images", []), "links": {}})
    if path.startswith("/reserved_ips/"):
        address = path.split("/")[2]
        held = state.get("reserved_ips", {}).get(address)
        if held is None:
            return answer(404, None)
        if method == "GET":
            droplet = None if held.get("droplet") is None else {"id": held["droplet"]}
            return answer(200, {"reserved_ip": {"ip": address, "region": {"slug": held["region"]},
                                                "droplet": droplet}})
        if method == "POST" and path.endswith("/actions") and body["type"] == "assign":
            held["droplet"] = body["droplet_id"]
            return answer(201, {"action": {"status": "in-progress"}})
    return answer(404, None)


def cloudflare(url: str, token: str) -> bytes:
    state["calls"].append({"call": f"CF GET {url}", "token": token == state.get("cf_token")})
    if token != state.get("cf_token"):
        raise OSError("refused")
    return json.dumps(state.get("zones", {"result": []})).encode()


host.transport = fake
host.cloudflare_get = cloudflare
host.sleep = lambda _seconds: None
host.now = lambda: datetime.fromisoformat(state["now"].replace("Z", "+00:00"))
try:
    status = host.main(sys.argv[2:])
finally:
    state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
sys.exit(status)
