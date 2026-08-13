import json
import subprocess
import threading
import time
import unittest
from datetime import datetime, timezone

from maintenance import CONFIGMAP_NAME, MaintenanceGate


class FakeKubectl:
    def __init__(self, data=None, not_found=False):
        self.data = dict(data or {})
        self.exists = not not_found
        self.created = False
        self.create_data = {}

    def run(self, argv, input=None, **kwargs):
        if len(argv) >= 4 and argv[0] == "kubectl" and argv[3] == "apply":
            self.exists = True
            self.created = True
            self.create_data = json.loads(input).get("data", {})
            return subprocess.CompletedProcess(argv, 0, "configured\n", "")
        command = argv[3]
        if command == "get":
            if not self.exists:
                return subprocess.CompletedProcess(argv, 1, "", "Error from server (NotFound)")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"data": self.data}), "")
        if command == "create":
            manifest = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": CONFIGMAP_NAME}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(manifest), "")
        if command == "patch":
            patch = json.loads(argv[argv.index("-p") + 1])
            self.data.update(patch["data"])
            return subprocess.CompletedProcess(argv, 0, json.dumps({"data": self.data}), "")
        raise AssertionError("unexpected command: {}".format(argv))


class MaintenanceGateTest(unittest.TestCase):
    def make_gate(self, fake, **kwargs):
        return MaintenanceGate(
            "mo-chaos-4-2-dev",
            runner_id="runner-1",
            command_runner=fake.run,
            poll_seconds=0.01,
            heartbeat_seconds=0.01,
            **kwargs,
        )

    def test_start_creates_missing_configmap_without_request_fields(self):
        fake = FakeKubectl(not_found=True)
        gate = self.make_gate(fake)
        gate.start()
        time.sleep(0.02)
        gate.stop()
        self.assertTrue(fake.created)
        self.assertNotIn("requested", fake.create_data)

    def test_no_request_allows_task_and_tracks_active_count(self):
        fake = FakeKubectl()
        gate = self.make_gate(fake)
        self.assertTrue(gate.enter_task(threading.Event()))
        self.assertEqual(gate.active_tasks, 1)
        gate.leave_task()
        self.assertEqual(gate.active_tasks, 0)

    def test_request_blocks_new_task_and_acks_when_idle(self):
        fake = FakeKubectl({"requested": "true", "request_id": "run-42", "protect": "false"})
        stop = threading.Event()
        gate = self.make_gate(fake)

        def stop_wait():
            time.sleep(0.03)
            stop.set()

        threading.Thread(target=stop_wait).start()
        self.assertFalse(gate.enter_task(stop))
        self.assertEqual(fake.data["ack_request_id"], "run-42")
        self.assertEqual(fake.data["state"], "paused")

    def test_request_arriving_during_task_is_acked_only_after_leave(self):
        fake = FakeKubectl()
        gate = self.make_gate(fake)
        self.assertTrue(gate.enter_task(threading.Event()))
        fake.data.update(requested="true", request_id="run-43", protect="false")
        gate.leave_task()
        self.assertEqual(fake.data["ack_request_id"], "run-43")
        self.assertEqual(fake.data["active_task"], "0")

    def test_expired_unprotected_request_resumes_but_protected_request_does_not(self):
        now = datetime(2026, 8, 13, 22, 0, tzinfo=timezone.utc)
        fake = FakeKubectl({
            "requested": "true",
            "request_id": "old",
            "expires_at": "2026-08-13T13:59:00Z",
            "protect": "false",
        })
        gate = self.make_gate(fake, now=lambda: now)
        self.assertTrue(gate.enter_task(threading.Event()))
        gate.leave_task()
        fake.data.update(requested="true", protect="true")
        stop = threading.Event()
        stop.set()
        self.assertFalse(gate.enter_task(stop))


if __name__ == "__main__":
    unittest.main()
