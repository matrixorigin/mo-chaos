import logging
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from thread.test_thread import Test_Thread, Subtask_Test_Thread, Subtask_Verify_Thread
from thread.thread_controller import Thread_Controller


class TestLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "tool").mkdir()
        self.logger = Mock()

    def runner(self, run="true", verify="true", mode="after", logs=()):
        task = {
            "name": "tool", "work-path": "tool",
            "run-steps": [{"command": run}],
            "verify": [{"command": verify}], "verify-mode": mode,
            "log-paths": [{"path": p} for p in logs],
        }
        with patch("thread.test_thread.load_yaml", return_value={"tasks": [task]}):
            return Test_Thread("unused", self.logger, str(self.root), str(self.root / "report"))

    def execute(self, runner):
        runner.execute_tasks()
        for worker in runner.subtask_verify_threads:
            worker.join(timeout=2)
        self.assertTrue(runner.stop_event.is_set())
        self.assertFalse(any(w.is_alive() for w in runner.subtask_verify_threads))

    def test_failed_run_reaches_controller_exit_status(self):
        runner = self.runner(run="exit 7")
        controller = Thread_Controller.__new__(Thread_Controller)
        controller.test_class = runner
        controller.logger = self.logger
        stopped = threading.Event()
        controller.chaos_class = Mock()
        controller.chaos_class.execute_tasks.side_effect = lambda: stopped.wait(5)
        controller.chaos_class.stop.side_effect = stopped.set
        self.assertEqual(controller.start(), 1)
        self.assertFalse(runner.success)
        controller.chaos_class.stop.assert_called_once()

    def test_failed_final_verification_marks_run_failed(self):
        runner = self.runner(verify="exit 9")
        self.execute(runner)
        self.assertFalse(runner.success)
        self.logger.error.assert_any_call("Step failed: exit 9 with return code 9")

    def test_successful_workload_and_final_verification(self):
        runner = self.runner(verify="printf verified > verify.log", logs=["verify.log"])
        self.execute(runner)
        self.assertTrue(runner.success)
        self.assertEqual((Path(runner.test_report_path) / "verify.log").read_text(), "verified")

    def test_delayed_after_verifier_is_not_skipped(self):
        runner = self.runner(verify="printf verified > verify.log", logs=["verify.log"])
        release = threading.Event()
        original = Subtask_Verify_Thread.run
        # Hold the verifier until the orchestrator requests shutdown. That
        # request must only stop periodic checkers, not the one-shot final check.
        def delayed(worker):
            release.wait(2)
            original(worker)
        def stop_and_release(worker):
            worker.running = False
            release.set()
        with patch.object(Subtask_Verify_Thread, "run", delayed), patch.object(
            Subtask_Verify_Thread, "stop", stop_and_release
        ):
            self.execute(runner)
        self.assertTrue(runner.success)
        self.assertEqual((Path(runner.test_report_path) / "verify.log").read_text(), "verified")

    def test_completion_and_archive_wait_for_inflight_verification(self):
        runner = self.runner(logs=["verify.log"])
        entered, release = threading.Event(), threading.Event()
        def verify(worker):
            worker.subtask_test_event.wait(2)
            entered.set()
            release.wait(5)
            (self.root / "tool" / "verify.log").write_text("complete")
            worker.success = True
        execution = threading.Thread(target=runner.execute_tasks)
        try:
            with patch.object(Subtask_Verify_Thread, "run", verify):
                execution.start()
                self.assertTrue(entered.wait(2))
                self.assertFalse(runner.stop_event.wait(0.1))
                release.set()
                execution.join(3)
            self.assertFalse(execution.is_alive())
            self.assertTrue(runner.success)
            self.assertEqual((Path(runner.test_report_path) / "verify.log").read_text(), "complete")
        finally:
            release.set()
            execution.join(3)
            for worker in runner.subtask_verify_threads:
                worker.join(3)

    def test_spawn_exception_releases_final_verifier_and_fails(self):
        runner = self.runner()
        event = threading.Event()
        task = runner.tasks[0]
        worker = Subtask_Test_Thread(task, str(self.root), "", event, self.logger)
        with patch("thread.test_thread.subprocess.Popen", side_effect=OSError("spawn failed")):
            worker.run()
        self.assertTrue(event.is_set())
        self.assertFalse(worker.success)

    def test_missing_work_directory_finishes_without_hanging(self):
        runner = self.runner()
        runner.tasks[0]['work-path'] = 'missing'
        self.execute(runner)
        self.assertFalse(runner.success)
        self.assertFalse(runner.subtask_verify_threads[0].success)

    def test_parallel_failure_is_not_erased_by_later_success(self):
        runner = self.runner(mode='parallel')
        worker = Subtask_Verify_Thread(
            runner.tasks[0], str(self.root), threading.Event(), '', True, self.logger
        )
        cycles = []
        def next_cycle(_seconds):
            cycles.append(1)
            if len(cycles) == 2:
                worker.stop()
        with patch('thread.test_thread.execute_steps', side_effect=[False, True]) as steps, patch.object(
            worker._stop_requested, 'wait', side_effect=next_cycle
        ):
            worker.run()
        self.assertEqual(steps.call_count, 2)
        self.assertFalse(worker.success)

    def test_short_parallel_workload_still_gets_one_check(self):
        runner = self.runner(mode='parallel')
        worker = Subtask_Verify_Thread(
            runner.tasks[0], str(self.root), threading.Event(), '', True, self.logger
        )
        worker.stop()
        with patch('thread.test_thread.execute_steps', return_value=True) as steps:
            worker.run()
        steps.assert_called_once()
        self.assertTrue(worker.success)

    def test_archival_failure_is_reported_and_completion_signalled(self):
        runner = self.runner(verify='printf verified > verify.log', logs=['verify.log'])
        with patch('thread.test_thread.shutil.move', side_effect=OSError('archive failed')):
            self.execute(runner)
        self.assertFalse(runner.success)

    def test_invalid_mode_and_empty_suite_are_not_success(self):
        for mode in ('invalid', 'empty'):
            with self.subTest(mode=mode):
                runner = self.runner(mode=mode)
                if mode == 'empty':
                    runner.tasks = []
                self.execute(runner)
                self.assertFalse(runner.success)

    def test_verifier_thread_start_failure_is_not_success(self):
        runner = self.runner()
        with patch.object(Subtask_Verify_Thread, 'start', side_effect=RuntimeError('start failed')):
            self.execute(runner)
        self.assertFalse(runner.success)

    def test_real_cli_process_exit_codes_without_live_chaos(self):
        script = Path(__file__).resolve().parents[1] / 'mo_chaos_test.py'
        launcher = '''
import runpy, sys, threading
from unittest.mock import patch
class IdleChaos:
    def __init__(self, *args): self.stopped = threading.Event()
    def execute_tasks(self): self.stopped.wait()
    def stop(self): self.stopped.set()
script = sys.argv[1]
sys.argv = [script, '-c', 'chaos.yml', '-t', 'tests.yml']
with patch('thread.thread_controller.Chaos_Thread', IdleChaos):
    runpy.run_path(script, run_name='__main__')
'''
        for run, verify, expected in [('true', 'true', 0), ('exit 7', 'true', 1), ('true', 'exit 9', 1)]:
            with self.subTest(run=run, verify=verify), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'config').mkdir()
                (root / 'test-tool' / 'tool').mkdir(parents=True)
                shutil.copyfile(script, root / script.name)
                task = self.runner(run=run, verify=verify).tasks[0]
                (root / 'config' / 'tests.yml').write_text(json.dumps({'tasks': [task]}))
                (root / 'config' / 'chaos.yml').write_text('{}')
                process = subprocess.run(
                    [sys.executable, '-c', launcher, str(root / script.name)],
                    cwd=script.parent, capture_output=True, text=True, timeout=10
                )
                self.assertEqual(process.returncode, expected, process.stderr)

    def test_entrypoint_propagates_controller_failure(self):
        script = Path(__file__).resolve().parents[1] / "mo_chaos_test.py"
        with patch("sys.argv", [str(script), "-c", "chaos.yml", "-t", "tests.yml"]), patch(
            "os.path.exists", return_value=True
        ), patch.object(logging, "basicConfig"), patch(
            "thread.thread_controller.Thread_Controller"
        ) as controller:
            controller.return_value.start.return_value = 1
            with self.assertRaises(SystemExit) as error:
                runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
