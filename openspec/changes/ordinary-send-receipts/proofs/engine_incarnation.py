"""Synthetic process-boundary proof; no engine/provider/account/device API.

The receipt/custody model uses the EXISTING process_liveness OS lock observer.
Serving fixture and delivery observer are actual separate processes with distinct
BOOT identities. This does not prove production engine routing or scope wiring.
Run from repository root with: python <this file>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from protocol import A, B, Held, Model

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tinyassets.process_liveness import (  # noqa: E402
    ALIVE,
    DEAD,
    UNKNOWN,
    liveness_path,
    owner_state,
)

SERVING_FIXTURE = """
import json, sys
sys.path.insert(0, sys.argv[2])
from protocol import A, Model
from tinyassets.process_liveness import owner_token
root = sys.argv[1]
serving = Model(root, issuer=owner_token(root))
key = serving.prepare(A, 'synthetic original')
assert serving.start(key, A, 'synthetic original')
serving.enqueue(key, A, 'first input')
print(json.dumps({'key':key, 'issuer':serving.issuer, 'boot':serving.boot}), flush=True)
for command in sys.stdin:
    if command.strip() == 'more':
        serving.enqueue(key, A, 'retained input')
    elif command.strip() == 'freeze':
        serving.freeze(key, A)
    else:
        break
    print('ok', flush=True)
"""


class EngineIncarnationProof(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="receipt-process-proof-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        Model(self.root).initialize_fixture()
        self.serving = subprocess.Popen(
            [sys.executable, "-c", SERVING_FIXTURE, str(self.root), str(Path(__file__).parent)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_serving)
        self.record = json.loads(self.serving.stdout.readline())
        self.key = self.record["key"]
        self.engine = Model(
            self.root,
            issuer_alive=lambda token: owner_state(self.root, token) == ALIVE,
        )
        self.assertNotEqual(self.record["boot"], self.engine.boot)
        self.assertEqual(owner_state(self.root, self.record["issuer"]), ALIVE)

    def stop_serving(self):
        if self.serving.poll() is None:
            self.serving.terminate()
        self.serving.wait(timeout=5)
        for stream in (self.serving.stdin, self.serving.stdout, self.serving.stderr):
            stream.close()

    def command(self, command):
        self.serving.stdin.write(command + "\n")
        self.serving.stdin.flush()
        self.assertEqual(self.serving.stdout.readline().strip(), "ok")

    def test_distinct_engine_delivers_then_dead_issuer_holds_remaining_input(self):
        self.assertEqual(self.engine.take(self.key, A), ["first input"])
        self.command("more")
        self.serving.kill()  # kernel releases lock even on abrupt termination
        self.serving.wait(timeout=5)
        self.assertEqual(owner_state(self.root, self.record["issuer"]), DEAD)
        with self.assertRaises(Held):
            self.engine.take(self.key, A)
        self.assertFalse(self.engine.start(self.key, A, "synthetic original"))
        self.assertEqual([r["state"] for r in self.engine.rows(self.key)], ["attempted", "claimed"])
        self.assertEqual(self.engine.observe(self.key, A), {"state": "held"})

    def test_live_but_closed_issuer_cannot_deliver_or_grant_a_new_start(self):
        self.command("freeze")
        self.assertEqual(owner_state(self.root, self.record["issuer"]), ALIVE)
        with self.assertRaises(Held):
            self.engine.take(self.key, A)
        self.assertFalse(self.engine.start(self.key, A, "synthetic original"))
        self.assertEqual(self.engine.rows(self.key)[0]["state"], "closed_claimed")

    def test_missing_liveness_is_unknown_and_cannot_deliver(self):
        # Synthetic missing-proof fault, not production lock cleanup.
        liveness_path(self.root, self.record["issuer"]).unlink()
        self.assertEqual(owner_state(self.root, self.record["issuer"]), UNKNOWN)
        with self.assertRaises(Held):
            self.engine.take(self.key, A)
        self.assertEqual(self.engine.rows(self.key)[0]["state"], "claimed")

    def test_liveness_never_overrides_scope_or_grants_server_mutations(self):
        for scope in (B, ("alice", "home-a", "other")):
            with self.assertRaises(Held):
                self.engine.take(self.key, scope)
        with self.assertRaises(Held):
            self.engine.publish(self.key, A, "unearned reply")
        with self.assertRaises(Held):
            self.engine.enqueue(self.key, A, "untrusted origin")
        self.assertFalse(self.engine.start(self.key, A, "synthetic original"))
        self.assertEqual(self.engine.take(self.key, A), ["first input"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
