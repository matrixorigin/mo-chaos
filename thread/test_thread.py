import threading
import yaml
import subprocess
import os
from datetime import datetime
import shutil


def load_yaml(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def execute_steps(task, key, work_path, logger):
    success = True
    label = 'Subtask_Test' if key == 'run-steps' else 'Subtask_Verify'
    for step in task[key]:
        logger.info(f"Executing {task['name']} {label} command: {step['command']}")
        # Each shell owns its cwd; never change the controller's process cwd.
        process = subprocess.Popen(
            step['command'], shell=True, stderr=subprocess.STDOUT, cwd=work_path
        )
        process.wait()
        if process.returncode == 0:
            logger.info(f"Step completed successfully: {step['command']}")
        else:
            success = False
            logger.error(f"Step failed: {step['command']} with return code {process.returncode}")
    return success


class Subtask_Test_Thread(threading.Thread):
    def __init__(self, task, tool_parent_dir_path, test_report_path, event, logger):
        threading.Thread.__init__(self)
        self.task = task
        self.tool_parent_dir_path = tool_parent_dir_path
        self.test_report_path = test_report_path
        self.subtask_verify_event = event
        self.logger = logger
        self.success = False

    def run(self):
        try:
            work_path = os.path.join(self.tool_parent_dir_path, self.task['work-path'])
            self.logger.info(f"Starting task: {self.task['name']} in {work_path}")
            self.success = execute_steps(self.task, 'run-steps', work_path, self.logger)
        except Exception:
            self.success = False
            self.logger.exception("Test worker failed")
        finally:
            # Even a spawn/configuration exception must release after verification.
            self.subtask_verify_event.set()


class Subtask_Verify_Thread(threading.Thread):
    def __init__(self, task, tool_parent_dir_path, subtask_test_event, test_report_path, is_parallel, logger):
        threading.Thread.__init__(self)
        self.task = task
        self.tool_parent_dir_path = tool_parent_dir_path
        self.test_report_path = test_report_path
        self.subtask_test_event = subtask_test_event
        self.is_parallel = is_parallel
        self.running = True
        self.logger = logger
        self.success = False
        self._stop_requested = threading.Event()

    def run(self):
        try:
            work_path = os.path.join(self.tool_parent_dir_path, self.task['work-path'])
            self.logger.info(f"Starting task: {self.task['name']} in {work_path}")
            if not self.is_parallel:
                self.subtask_test_event.wait()
            self.success = True
            # A short workload must still receive at least one verification pass.
            first_check = True
            while self.running or first_check:
                first_check = False
                if not execute_steps(self.task, 'verify', work_path, self.logger):
                    self.success = False
                if not self.is_parallel:
                    break
                # Complete in-flight checks, but interrupt the idle interval.
                self._stop_requested.wait(30)
        except Exception:
            self.success = False
            self.logger.exception("Verification worker failed")

    def stop(self):
        self.running = False
        self._stop_requested.set()


class Test_Thread:
    def __init__(self, test_config_full_path, logger, tool_parent_dir_path, test_tool_report_parent_dir_path):
        self.logger = logger
        self.test_yaml_data = load_yaml(test_config_full_path)
        self.tasks = self.test_yaml_data.get('tasks', [])
        self.tool_parent_dir_path = tool_parent_dir_path
        self.test_tool_report_parent_dir_path = test_tool_report_parent_dir_path
        self.stop_event = threading.Event()
        self.subtask_verify_threads = []
        self.test_report_path = ""
        self.success = False

    def execute_tasks(self):
        test_threads = []
        launch_ok = False
        try:
            test_start_time = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
            self.test_report_path = os.path.join(self.test_tool_report_parent_dir_path,
                                                'test_report_' + test_start_time)
            os.makedirs(self.test_report_path, exist_ok=True)
            for task in self.tasks:
                mode = task['verify-mode']
                if mode not in ('parallel', 'after'):
                    raise ValueError(f"Unsupported verify-mode: {mode}")
                event = threading.Event()
                test = Subtask_Test_Thread(task, self.tool_parent_dir_path,
                                          self.test_report_path, event, self.logger)
                verify = Subtask_Verify_Thread(task, self.tool_parent_dir_path, event,
                                              self.test_report_path, mode == 'parallel', self.logger)
                test.start()
                test_threads.append(test)
                verify.start()
                self.subtask_verify_threads.append(verify)
            launch_ok = bool(test_threads)
        except Exception:
            self.logger.exception("Failed to launch test tasks")
        finally:
            for test in test_threads:
                test.join()
            # Stop periodic checks only. The final after check must not be skipped.
            for verify in self.subtask_verify_threads:
                if verify.is_parallel:
                    verify.stop()
            for verify in self.subtask_verify_threads:
                verify.join()
            self.success = launch_ok and all(
                worker.success for worker in test_threads + self.subtask_verify_threads
            )
            try:
                # No worker may still write logs when they are archived.
                for task in self.tasks:
                    task_work_path = os.path.join(self.tool_parent_dir_path, task['work-path'])
                    for log in task['log-paths']:
                        src = os.path.join(task_work_path, log['path'])
                        dst = os.path.join(self.test_report_path, log['path'])
                        if os.path.exists(src):
                            shutil.move(src, dst)
            except Exception:
                self.success = False
                self.logger.exception("Failed to archive test logs")
            finally:
                self.stop_event.set()

    def stop(self):
        self.stop_event.set()
