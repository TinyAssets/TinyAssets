"""C3/C14 off-host job copy, written directly by the broker.

These are control-plane records, never files inside an owner's volume.
"""
import json
import re
import subprocess


class RemoteRecords:
    def __init__(self, remote):
        if not remote:
            raise ValueError("deletion backup destination is required")
        self.remote = remote.rstrip("/")

    @staticmethod
    def _run(*args, input=None):
        result = subprocess.run(["rclone", *args, "--contimeout", "10s", "--timeout", "30s"],
                                input=input, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("off-host deletion record transport failed")
        return result.stdout

    def _path(self, key):
        if not isinstance(key, str) or re.fullmatch("[0-9a-f]{64}", key) is None:
            raise ValueError("invalid deletion record key")
        return f"{self.remote}/deletion-jobs/{key}.json"

    def execute(self, action, arguments):
        if action == "list" and arguments == {}:
            rows = json.loads(self._run("lsjson", self.remote, "--recursive", "--files-only",
                                       "--include", "/deletion-jobs/*.json"))
            keys = []
            for row in rows:
                match = re.fullmatch(r"deletion-jobs/([0-9a-f]{64})\.json", row["Path"])
                if match is None:
                    raise ValueError("invalid off-host deletion record")
                keys.append(match[1])
            return sorted(keys)
        if action == "read" and set(arguments) == {"key"}:
            value = json.loads(self._run("cat", self._path(arguments["key"])))
            if value["id"] != arguments["key"]:
                raise ValueError("deletion record identity differs")
            return value
        if action == "write" and set(arguments) == {"key", "record"}:
            path = self._path(arguments["key"])
            if arguments["record"]["id"] != arguments["key"]:
                raise ValueError("deletion record identity differs")
            data = json.dumps(arguments["record"], sort_keys=True).encode()
            self._run("rcat", path, input=data)
            return None
        raise ValueError("invalid deletion record operation")

    def load(self, key):
        return self.execute("read", {"key": key})

    def save(self, job):
        self.execute("write", {"key": job["id"], "record": job})

    def records(self):
        return [self.load(key) for key in self.execute("list", {})]

