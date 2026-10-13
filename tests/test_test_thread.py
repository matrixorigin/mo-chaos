import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from thread.test_thread import Subtask_Test_Thread, Subtask_Verify_Thread


class SubtaskWorkingDirectoryTest(unittest.TestCase):
    def make_worker(self, kind, root, name, command, logger):
        event = threading.Event()
        task = {
            "name": name,
            "work-path": name,
            "run-steps": [{"command": command}],
            "verify": [{"command": command}],
        }
        if kind == "run":
            worker = Subtask_Test_Thread(task, str(root), "", event, logger)
        else:
            event.set()
            worker = Subtask_Verify_Thread(
                task, str(root), event, "", kind == "parallel", logger
            )
        return worker, event

    def test_concurrent_commands_keep_scripts_inputs_and_logs_in_own_directory(self):
        for kinds in [("run", "run"), ("run", "parallel"), ("after", "parallel")]:
            with self.subTest(kinds=kinds), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original_cwd = Path.cwd()
                logger = Mock()
                workers = []
                for name, kind in zip(("cdc", "fulltext"), kinds):
                    work = root / name
                    work.mkdir()
                    (work / "input.txt").write_text(name + "\n")
                    # Same relative script name, but distinct inputs and output dirs.
                    (work / "task.sh").write_text("#!/bin/sh\ncat input.txt\n")
                    (work / "task.sh").chmod(0o755)
                    worker, _ = self.make_worker(
                        kind, root, name, "./task.sh > result.log 2>&1", logger
                    )
                    workers.append(worker)

                # Both workers reach Popen before either shell is started.
                # With os.chdir(), both children inherit the last writer's cwd.
                barrier = threading.Barrier(2, timeout=5)
                real_popen = subprocess.Popen
                launches = []
                failures = []

                def launch(*args, **kwargs):
                    barrier.wait()
                    process = real_popen(*args, **kwargs)
                    launches.append(process)
                    return process

                def run(worker):
                    try:
                        worker.run()
                    except BaseException as error:
                        failures.append(error)

                def stop_verifiers(_seconds):
                    for worker in workers:
                        if isinstance(worker, Subtask_Verify_Thread):
                            worker.stop()

                threads = [threading.Thread(target=run, args=(w,)) for w in workers]
                try:
                    with patch("thread.test_thread.subprocess.Popen", side_effect=launch), patch(
                        "thread.test_thread.time.sleep", side_effect=stop_verifiers
                    ):
                        for thread in threads:
                            thread.start()
                        for thread in threads:
                            thread.join(timeout=10)
                    self.assertFalse(any(t.is_alive() for t in threads))
                    self.assertEqual(failures, [])
                    self.assertEqual(len(launches), 2)
                    for name in ("cdc", "fulltext"):
                        output = root / name / "result.log"
                        self.assertTrue(output.exists(), f"missing {output}")
                        self.assertEqual(output.read_text(), name + "\n")
                    self.assertEqual(Path.cwd(), original_cwd)
                    logger.error.assert_not_called()
                finally:
                    # Restore cwd even when demonstrating failure on the old code.
                    os.chdir(original_cwd)
                    for process in launches:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=5)

    def test_relative_work_paths_multistep_and_failure_reporting(self):
        for kind in ("run", "after", "parallel"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
                root = Path(tmp)
                original_cwd = Path.cwd()
                (root / "tool").mkdir()
                logger = Mock()
                worker, event = self.make_worker(
                    kind, root.relative_to(original_cwd), "tool", "printf first > first.log", logger
                )
                steps = worker.task["run-steps" if kind == "run" else "verify"]
                steps.extend([
                    {"command": "cat first.log > second.log"},
                    {"command": "exit 7"},
                ])
                try:
                    with patch("thread.test_thread.os.chdir", side_effect=AssertionError(
                        "worker must not change process cwd"
                    )), patch("thread.test_thread.time.sleep", side_effect=lambda _: worker.stop()):
                        worker.run()
                    self.assertEqual((root / "tool" / "second.log").read_text(), "first")
                    self.assertEqual(Path.cwd(), original_cwd)
                    logger.error.assert_called_once_with("Step failed: exit 7 with return code 7")
                    self.assertTrue(event.is_set())
                finally:
                    os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
