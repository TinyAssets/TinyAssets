"""Private CLI worker frames; auth writeback never enters execution output."""

from __future__ import annotations

import base64
import re
import struct

from tinyassets.boxes.file_wire import MAX_DATA, MAX_MESSAGE, decode, encode
from tinyassets.boxes.provider import BoxError, BoxOperationRefused, box_relpath


def validate_config(config):
    if (not isinstance(config, dict) or set(config) != {
        "home_dir", "workdir", "auth_files", "nonce", "catalogue_rewrite", "requested_model",
        "custody", "env_from_file"
    } or not isinstance(config["home_dir"], str)
        or not isinstance(config["env_from_file"], dict)
        or len(config["env_from_file"]) > 8
        or any(not isinstance(k, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", k)
               or v not in config["auth_files"] for k, v in config["env_from_file"].items())
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", config["home_dir"])
        or not isinstance(config["workdir"], str)
        or not re.fullmatch(r"/agent-home/jobs/[a-f0-9]{32}", config["workdir"])
        or not isinstance(config["custody"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", config["custody"])
        or not isinstance(config["nonce"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", config["nonce"])
        or not isinstance(config["auth_files"], dict)
        or not 1 <= len(config["auth_files"]) <= 16
        or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", k)
               or not isinstance(v, bytes) or not v for k, v in config["auth_files"].items())
        or sum(map(len, config["auth_files"].values())) > MAX_DATA):
        raise BoxOperationRefused("invalid private CLI materialization")
    encode(config)


class PrivateDecoder:
    def __init__(self, config):
        validate_config(config)
        self.nonce = config["nonce"]
        self.names = set(config["auth_files"])
        self.buffer = bytearray()
        self.result = None

    def feed(self, data):
        self.buffer.extend(data)
        output = bytearray()
        while len(self.buffer) >= 5:
            kind, size = struct.unpack("!BI", self.buffer[:5])
            if self.result is not None or kind not in (1, 2) or size > (
                65536 if kind == 1 else MAX_MESSAGE
            ):
                raise BoxError("invalid private CLI frame")
            if len(self.buffer) < size + 5:
                break
            payload = bytes(self.buffer[5:size + 5])
            del self.buffer[:size + 5]
            if kind == 1:
                output.extend(payload)
                continue
            result = decode(payload)
            if (set(result) != {"external_agent_writeback", "nonce", "exit"}
                or result["nonce"] != self.nonce or type(result["exit"]) is not int
                or not isinstance(result["external_agent_writeback"], dict)
                or not set(result["external_agent_writeback"]).issubset(self.names)):
                raise BoxError("invalid private CLI writeback")
            try:
                files = {name: base64.b64decode(value, validate=True)
                         for name, value in result["external_agent_writeback"].items()}
            except (TypeError, ValueError):
                raise BoxError("invalid private CLI credential encoding") from None
            if sum(map(len, files.values())) > MAX_DATA or (
                result["exit"] == 0 and set(files) != self.names
            ):
                raise BoxError("private CLI credential bounds or declaration changed")
            self.result = {"files": files, "exit_code": result["exit"]}
        return bytes(output)

    def finish(self, exit_code):
        if self.buffer or self.result is None or self.result["exit_code"] != exit_code:
            raise BoxError("private CLI writeback is incomplete")
        return self.result


def run(request):
    from tinyassets.external_agents.cell import _write_all, cell_main

    config = request["cli"]
    validate_config(config)

    def emit(kind, body):
        _write_all(1, struct.pack("!BI", kind, len(body)) + body)

    config = {**config, "argv": request["argv"], "env": request["env"],
              "writeback": True, "engine_port": None,
              "auth_files": {name: base64.b64encode(value).decode("ascii")
                             for name, value in config["auth_files"].items()}}
    config.pop("custody")
    center = box_relpath(request["cwd"]).split("/", 1)[0]
    if not center:
        raise BoxOperationRefused("private CLI center was not projected")
    code = cell_main("/data", egress=True, engine=False, config=config, box_center=center,
                     output=lambda fd, body: emit(1, body),
                     private_writeback=lambda result: emit(2, encode(result)))
    raise SystemExit(code)
