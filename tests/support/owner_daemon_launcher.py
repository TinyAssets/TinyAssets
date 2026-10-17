"""runsc substitute only: launch the real owner app in a separate Linux process."""
import subprocess
import sys
import tempfile

from tinyassets.boxes.gvisor_runtime import GvisorRuntime


class ProcessRuntime:
    box_id = staticmethod(GvisorRuntime.box_id)

    def __init__(self, roots):
        self.roots = roots
        self.processes = {}
        self.logs = []

    def start(self, uid, gid, *, relay, app_dir, secret_file, memory_bytes):
        log = tempfile.TemporaryFile()
        self.logs.append(log)
        self.processes[uid] = subprocess.Popen([
            sys.executable, "-m", "tests.support.owner_router_process", str(self.roots[uid]),
            str(relay), str(secret_file), str(app_dir / "app.sock"),
        ], stdout=log, stderr=log)

    def alive(self, uid):
        return uid in self.processes and self.processes[uid].poll() is None

    def stop(self, uid):
        process = self.processes.get(uid)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(10)

    def shutdown(self, uid):
        process = self.processes.get(uid)
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(30)

    def close(self):
        for uid in self.processes:
            self.stop(uid)
        for log in self.logs:
            log.close()
