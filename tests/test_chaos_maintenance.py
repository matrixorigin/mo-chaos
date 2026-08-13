import logging
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

if "pymysql" not in sys.modules:
    pymysql = types.ModuleType("pymysql")
    pymysql.MySQLError = RuntimeError
    pymysql.connect = Mock()
    sys.modules["pymysql"] = pymysql

if "yaml" not in sys.modules:
    yaml_module = types.ModuleType("yaml")
    yaml_module.safe_load = json.load
    sys.modules["yaml"] = yaml_module

from thread.chaos_thread import Chaos_Thread
from thread.thread_controller import Thread_Controller


class FakeGate:
    def __init__(self, allowed):
        self.allowed = iter(allowed)
        self.entered = 0
        self.left = 0

    def enter_task(self, stop_event):
        self.entered += 1
        return next(self.allowed)

    def leave_task(self):
        self.left += 1

    def wait_interval(self, seconds, stop_event):
        return not stop_event.is_set()


class ChaosMaintenanceTest(unittest.TestCase):
    def make_chaos_thread(self, gate, times=1):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        config_path = os.path.join(tempdir.name, "chaos.yaml")
        with open(config_path, "w") as config:
            json.dump({
                "chaos": {
                    "cm-chaos": [{
                        "name": "fault",
                        "times": times,
                        "interval": 0,
                        "kubectl_yaml": "kind: PodChaos",
                        "is_delete_after_apply": True,
                    }],
                    "chaos_combination": {"mode": "in-turn", "task_interval": 0},
                    "namespace": "test-ns",
                }
            }, config)
        return Chaos_Thread(config_path, tempdir.name, logging.getLogger("test"), maintenance_gate=gate)

    def test_gate_denial_prevents_fault_injection(self):
        gate = FakeGate([False])
        chaos = self.make_chaos_thread(gate)
        chaos.execute_chaos = Mock()
        chaos.run_task(chaos.tasks[0])
        chaos.execute_chaos.assert_not_called()
        self.assertEqual(gate.left, 0)

    def test_leave_runs_when_fault_raises(self):
        gate = FakeGate([True])
        chaos = self.make_chaos_thread(gate)
        chaos.execute_chaos = Mock(side_effect=RuntimeError("boom"))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            chaos.run_task(chaos.tasks[0])
        self.assertEqual(gate.left, 1)

    def test_each_iteration_checks_gate(self):
        gate = FakeGate([True, False])
        chaos = self.make_chaos_thread(gate, times=3)
        chaos.execute_chaos = Mock()
        chaos.run_task(chaos.tasks[0])
        self.assertEqual(chaos.execute_chaos.call_count, 1)
        self.assertEqual(gate.entered, 2)
        self.assertEqual(gate.left, 1)


class ControllerLifecycleTest(unittest.TestCase):
    def test_gate_lifecycle_wraps_worker_threads(self):
        events = []

        class Gate:
            def start(self): events.append("gate.start")
            def stop(self): events.append("gate.stop")

        class Chaos:
            maintenance_gate = Gate()
            def execute_tasks(self): events.append("chaos.execute")
            def stop(self): events.append("chaos.stop")

        class StopEvent:
            def wait(self): events.append("test.done")

        class Test:
            stop_event = StopEvent()
            def execute_tasks(self): events.append("test.execute")

        class FakeThread:
            count = 0
            def __init__(self, target):
                self.target = target
                self.name = "test" if FakeThread.count == 0 else "chaos"
                FakeThread.count += 1
            def start(self): events.append(self.name + ".start")
            def join(self): events.append(self.name + ".join")

        controller = Thread_Controller.__new__(Thread_Controller)
        controller.chaos_class = Chaos()
        controller.test_class = Test()
        controller.logger = Mock()
        with patch("thread.thread_controller.threading.Thread", FakeThread):
            controller.start()

        self.assertEqual(events, [
            "gate.start", "test.start", "chaos.start", "test.done",
            "chaos.stop", "chaos.join", "gate.stop",
        ])


if __name__ == "__main__":
    unittest.main()
