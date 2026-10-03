"""Synthetic positive-identity design evidence, NOT production integration.

Original serving subprocess self-captures then delegates observation descriptors
to its engine subprocess through explicit pass_fds. The controller's socket is
only test transport. All databases, processes, text and fault injections are local.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from positive_identity import ALIVE, UNKNOWN, Epoch, Observer
from protocol import Model

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tinyassets.process_liveness import owner_state, remove_if_dead  # noqa: E402

ENGINE = """
import json, socket, sys
from unittest.mock import patch
from positive_identity import Observer, ALIVE
from protocol import A, Held, Model
metadata=json.loads(sys.argv[1])
observer=Observer.adopt_inherited(metadata)
channel=socket.socket(fileno=int(sys.argv[2])).makefile('rw')
model=Model(sys.argv[3],issuer_alive=lambda label:observer.state(label)==ALIVE)
key=sys.argv[4]
for line in channel:
    command=json.loads(line)
    op=command['op']
    try:
        if op=='state':
            answer=observer.state(command.get('epoch',metadata['epoch']))
        elif op=='take':
            answer=model.take(key,A)
        elif op in ('denied','io_error'):
            error=PermissionError('synthetic denial') if op=='denied' else OSError('synthetic IO')
            with patch('signal.pidfd_send_signal',side_effect=error):
                answer=model.take(key,A)
        elif op=='second_start':
            answer=model.start(key,A,'synthetic original')
        elif op=='other_scope':
            answer=model.take(key,('alice','home-a','other'))
        elif op=='death_after_first':
            import os, signal, select
            observed=[]
            def die_after_first(label):
                state=observer.state(label)
                observed.append(state)
                if len(observed)==1:
                    os.kill(os.getppid(),signal.SIGKILL)  # synthetic serving parent only
                    poll=select.poll();poll.register(observer._fds[0],select.POLLIN)
                    if not poll.poll(5000):raise RuntimeError('fixture death timeout')
                return state==ALIVE
            model.issuer_alive=die_after_first
            answer=model.take(key,A)
        elif op=='rows':
            answer=[r['state'] for r in model.rows(key)]
        elif op=='leak':
            import subprocess, os
            inode=os.fstat(observer._fds[1]).st_ino
            probe=("import os,sys; print(any("
                   "os.readlink('/proc/self/fd/'+f)=='anon_inode:[pidfd]' or "
                   "os.stat('/proc/self/fd/'+f).st_ino==int(sys.argv[1]) "
                   "for f in os.listdir('/proc/self/fd') "
                   "if os.path.exists('/proc/self/fd/'+f)))")
            answer=subprocess.check_output([sys.executable,'-c',probe,str(inode)],text=True).strip()
        elif op=='quit':
            observer.close(); answer='closed'
        else:
            raise ValueError('unknown fixture operation')
    except Held:
        answer='held'
    channel.write(json.dumps(answer)+'\\n');channel.flush()
    if op=='quit':break
"""

SERVING = """
import json, os, socket, subprocess, sys
from positive_identity import Epoch
from protocol import A, Model
sys.path.insert(0,sys.argv[4])
from tinyassets.process_liveness import owner_token
legacy=owner_token(sys.argv[1])
epoch=Epoch()
model=Model(sys.argv[1],issuer=epoch.label)
key=model.prepare(A,'synthetic original')
assert model.start(key,A,'synthetic original')
model.enqueue(key,A,'first input')
control=int(sys.argv[2])
with epoch.launch_binding() as (metadata,fds):
    engine=subprocess.Popen([sys.executable,'-c',sys.argv[3],json.dumps(metadata),str(control),sys.argv[1],key],pass_fds=(*fds,control))
os.close(control)
print(json.dumps({'key':key,'epoch':epoch.label,'engine_pid':engine.pid,'legacy':legacy}),flush=True)
for command in sys.stdin:
    op=command.strip()
    if op=='retire': epoch.retire()
    elif op=='exec':
        os.execv(sys.executable,[sys.executable,'-c',"print('exec-ready',flush=True);input()"])
    elif op=='stop':break
    else:raise ValueError('unknown serving fixture command')
    print('ok',flush=True)
epoch.retire()
engine.wait(timeout=5)
"""


class DelegationProof(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="positive-issuer-proof-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        Model(self.root).initialize_fixture()
        self.control, child = socket.socketpair()
        self.control.settimeout(5)
        self.channel = self.control.makefile("rw")
        self.serving = subprocess.Popen(
            [sys.executable, "-c", SERVING, str(self.root), str(child.fileno()), ENGINE, str(ROOT)],
            cwd=Path(__file__).parent,
            pass_fds=(child.fileno(),),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        child.close()
        self.addCleanup(self.cleanup)
        self.record = json.loads(self.serving.stdout.readline())
        # Fixture cleanup only: engine is alive and still unreaped by original
        # serving parent. Never used as serving identity or receipt authority.
        self.engine_handle = os.pidfd_open(self.record["engine_pid"])
        self.assertEqual(self.command("state"), ALIVE)

    def command(self, op, **fields):
        self.channel.write(json.dumps({"op": op, **fields}) + "\n")
        self.channel.flush()
        return json.loads(self.channel.readline())

    def serving_command(self, op):
        self.serving.stdin.write(op + "\n")
        self.serving.stdin.flush()
        return self.serving.stdout.readline().strip()

    def cleanup(self):
        import signal

        try:
            self.command("quit")
        except (OSError, ValueError):
            pass
        if self.serving.poll() is None:
            self.serving.stdin.write("stop\n")
            self.serving.stdin.flush()
        try:
            self.serving.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.serving.kill()
            self.serving.wait(timeout=5)
        if hasattr(self, "engine_handle"):
            try:
                signal.pidfd_send_signal(self.engine_handle, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(self.engine_handle)
        for stream in (self.serving.stdin, self.serving.stdout, self.serving.stderr):
            stream.close()
        self.channel.close()
        self.control.close()

    def test_genuine_distinct_engine_delivers_once_without_second_start(self):
        self.assertEqual(self.command("take"), ["first input"])
        self.assertEqual(self.command("take"), [])
        self.assertFalse(self.command("second_start"))
        self.assertEqual(self.command("leak"), "False")

    def test_dead_reaped_original_is_not_replaced_by_any_live_process(self):
        self.serving.kill()
        self.serving.wait(timeout=5)
        self.assertEqual(self.command("state"), UNKNOWN)
        self.assertEqual(self.command("take"), "held")
        self.assertFalse(self.command("second_start"))
        self.assertEqual(self.command("rows"), ["claimed"])
        # Current controller is alive; its numeric PID is irrelevant. Engine
        # probes only its inherited kernel object, never reopens a saved PID.
        self.assertNotEqual(os.getpid(), self.serving.pid)
        self.assertEqual(self.command("state", pid=os.getpid()), UNKNOWN)

    def test_retirement_while_original_process_remains_alive_holds(self):
        self.assertEqual(self.serving_command("retire"), "ok")
        self.assertIsNone(self.serving.poll())
        self.assertEqual(self.command("take"), "held")
        self.assertEqual(self.command("rows"), ["claimed"])

    def test_exec_retires_same_pid_without_claiming_process_death(self):
        self.assertEqual(self.serving_command("exec"), "exec-ready")
        self.assertIsNone(self.serving.poll())
        self.assertEqual(self.command("state"), UNKNOWN)
        self.assertEqual(self.command("take"), "held")

    def test_probe_permission_and_io_errors_hold_without_marking_attempted(self):
        for op in ("denied", "io_error"):
            self.assertEqual(self.command(op), "held")
            self.assertEqual(self.command("rows"), ["claimed"])
        self.assertEqual(self.command("take"), ["first input"])

    def test_unmatched_epoch_cannot_borrow_a_live_pin(self):
        self.assertEqual(self.command("state", epoch="stale-or-substituted"), UNKNOWN)
        self.assertEqual(self.command("state"), ALIVE)
        self.assertEqual(self.command("other_scope"), "held")

    def test_death_between_attempt_and_return_retains_unknown_without_replay(self):
        self.assertEqual(self.command("death_after_first"), "held")
        self.assertEqual(self.command("rows"), ["attempted"])
        self.assertFalse(self.command("second_start"))

    def test_dead_issuer_cleanup_contention_never_becomes_positive_proof(self):
        self.serving.kill()
        self.serving.wait(timeout=5)
        entered, release = threading.Event(), threading.Event()

        def still_named(_token):
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture timeout")
            return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            cleanup = pool.submit(remove_if_dead, self.root, self.record["legacy"], still_named)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(owner_state(self.root, self.record["legacy"]), ALIVE)
                self.assertEqual(self.command("state"), UNKNOWN)
                self.assertEqual(self.command("take"), "held")
                self.assertEqual(self.command("rows"), ["claimed"])
            finally:
                release.set()
                self.assertFalse(cleanup.result(timeout=5))


class OwnershipProof(unittest.TestCase):
    def setUp(self):
        self.epoch = Epoch()
        self.addCleanup(self.epoch.retire)
        self.observer = self.epoch.local_observer()
        self.addCleanup(self.observer.close)

    def test_retire_is_irreversible_and_new_same_process_epoch_is_distinct(self):
        old = self.epoch.label
        self.epoch.retire()
        self.assertEqual(self.observer.state(old), UNKNOWN)
        with self.assertRaises(RuntimeError):
            with self.epoch.launch_binding():
                pass
        new = Epoch()
        self.addCleanup(new.retire)
        newer = new.local_observer()
        self.addCleanup(newer.close)
        self.assertNotEqual(new.label, old)
        self.assertEqual(newer.state(old), UNKNOWN)
        self.assertEqual(newer.state(new.label), ALIVE)

    def test_new_epoch_cannot_start_prepared_receipt_from_same_process_boot(self):
        from protocol import A

        with tempfile.TemporaryDirectory(prefix="same-boot-new-epoch-") as root:
            original = Model(root, boot="same-boot", issuer=self.epoch.label)
            original.initialize_fixture()
            key = original.prepare(A, "body")
            self.epoch.retire()
            new = Epoch()
            self.addCleanup(new.retire)
            current = Model(root, boot="same-boot", issuer=new.label)
            self.assertFalse(current.start(key, A, "body"))

    def test_closed_observer_never_uses_recycled_descriptor_numbers(self):
        old = list(self.observer._fds)
        self.observer.close()
        raw = os.open(os.devnull, os.O_RDONLY)
        try:
            for fd in old:
                os.dup2(raw, fd, inheritable=False)  # deterministic descriptor-number reuse
            self.assertEqual(self.observer.state(self.epoch.label), UNKNOWN)
        finally:
            for fd in {*old, raw}:
                os.close(fd)

    def test_unsupported_or_denied_capture_has_no_lock_or_pid_fallback(self):
        for error in (PermissionError("denied"), OSError(38, "unsupported")):
            with patch("os.pidfd_open", side_effect=error), self.assertRaises(OSError):
                Epoch()

    def test_epoch_data_is_unknown_even_when_original_process_is_alive(self):
        os.write(self.epoch._writer, b"unexpected fixture data")
        self.assertEqual(self.observer.state(self.epoch.label), UNKNOWN)

    def test_engine_respawn_observes_only_same_active_epoch(self):
        script = (
            "import json,sys;from positive_identity import Observer;"
            "m=json.loads(sys.argv[1]);o=Observer.adopt_inherited(m);"
            "print(o.state(m['epoch']));o.close()"
        )
        for _ in range(2):
            with self.epoch.launch_binding() as (metadata, fds):
                result = subprocess.run(
                    [sys.executable, "-c", script, json.dumps(metadata)],
                    cwd=Path(__file__).parent,
                    pass_fds=fds,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(result.stdout.strip(), ALIVE)

    def test_raw_fork_child_keeps_no_epoch_writer_or_observer(self):
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        child = os.fork()  # single-threaded synthetic fixture only
        if child == 0:
            os.close(ready_r)
            os.close(release_w)
            valid = not self.epoch._active and not self.observer._fds
            os.write(ready_w, b"closed" if valid else b"leaked")
            os.read(release_r, 1)
            os._exit(0)
        os.close(ready_w)
        os.close(release_r)
        try:
            self.assertEqual(os.read(ready_r, 6), b"closed")
            self.epoch.retire()
            self.assertEqual(self.observer.state(self.epoch.label), UNKNOWN)
        finally:
            os.write(release_w, b"x")
            os.close(ready_r)
            os.close(release_w)
            os.waitpid(child, 0)

    def test_poll_error_is_unknown_and_never_falls_back_to_lock_state(self):
        with patch("select.poll", side_effect=OSError("synthetic probe error")):
            self.assertEqual(self.observer.state(self.epoch.label), UNKNOWN)

    def test_spawn_failure_closes_exports_and_keeps_original_epoch(self):
        exported = []
        with self.assertRaises(FileNotFoundError):
            with self.epoch.launch_binding() as (_metadata, fds):
                exported = list(fds)
                subprocess.Popen(["/synthetic-missing-executable"], pass_fds=fds)
        for fd in exported:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertEqual(self.observer.state(self.epoch.label), ALIVE)

    def test_export_serializes_against_retire(self):
        started, finished = threading.Event(), threading.Event()

        def retire():
            started.set()
            self.epoch.retire()
            finished.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.epoch.launch_binding() as (_metadata, fds):
                task = pool.submit(retire)
                self.assertTrue(started.wait(5))
                self.assertFalse(finished.is_set())
                for fd in fds:
                    os.fstat(fd)
            task.result(timeout=5)
        self.assertEqual(self.observer.state(self.epoch.label), UNKNOWN)

    def test_wrong_descriptor_type_is_rejected_not_reported_alive(self):
        raw = os.open(os.devnull, os.O_RDONLY)
        reader, writer = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            with self.assertRaises(OSError):
                Observer(self.epoch.label, raw, reader)
        finally:
            os.close(writer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
