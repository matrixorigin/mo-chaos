import threading
import yaml
import subprocess
import time
import random
import pymysql
import logging
import os
from datetime import datetime
import shutil


def load_yaml(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


class Subtask_Test_Thread(threading.Thread):
    def __init__(self, task, tool_parent_dir_path, test_report_path, event, logger):
        threading.Thread.__init__(self)
        self.task = task
        self.tool_parent_dir_path = tool_parent_dir_path
        self.test_report_path = test_report_path
        self.subtask_verify_event = event
        self.logger = logger

    def run(self):
        work_path = os.path.join(self.tool_parent_dir_path, self.task['work-path'])
        self.logger.info(f"Starting task: {self.task['name']} in {work_path}")
        os.chdir(work_path)
        for step in self.task['run-steps']:
            self.logger.info(f"Executing {self.task['name']} Subtask_Test command: {step['command']}")
            # Execute the command and redirect output to the log file
            process = subprocess.Popen(step['command'], shell=True, stderr=subprocess.STDOUT)
            process.wait()  # Wait for the command to complete

            if process.returncode == 0:
                self.logger.info(f"Step completed successfully: {step['command']}")
            else:
                self.logger.error(f"Step failed: {step['command']} with return code {process.returncode}")

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

    def run(self):
        work_path = os.path.join(self.tool_parent_dir_path, self.task['work-path'])
        self.logger.info(f"Starting task: {self.task['name']} in {work_path}")
        os.chdir(work_path)
        # 如果需要等待subtaskA结束
        if not self.is_parallel:
            self.subtask_test_event.wait()
        while self.running:
            for step in self.task['verify']:
                self.logger.info(f"Executing {self.task['name']} Subtask_Verify command: {step['command']}")
                # Execute the command and redirect output to the log file
                process = subprocess.Popen(step['command'], shell=True, stderr=subprocess.STDOUT)
                process.wait()  # Wait for the command to complete
                if process.returncode == 0:
                    self.logger.info(f"Step completed successfully: {step['command']}")
                else:
                    self.logger.error(f"Step failed: {step['command']} with return code {process.returncode}")
                time.sleep(30)  # 模拟命令执行时间
            if not self.is_parallel:
                break

    def stop(self):
        self.running = False


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

    def execute_tasks(self):
        threads = []
        now = datetime.now()
        test_start_time = now.strftime('%Y-%m-%d-%H-%M-%S')
        self.test_report_path = os.path.join(self.test_tool_report_parent_dir_path,
                                             'test_report_' + test_start_time)
        if not os.path.exists(self.test_report_path):
            os.makedirs(self.test_report_path)

        for task in self.tasks:
            subtask_verify_mode = task['verify-mode']
            test_subtask_event = threading.Event()

            test_subtask_thread = Subtask_Test_Thread(task, self.tool_parent_dir_path, self.test_report_path,
                                                      test_subtask_event, self.logger)
            threads.append(test_subtask_thread)

            test_subtask_thread.start()

            is_parallel = (subtask_verify_mode == 'parallel')

            subtask_verify = Subtask_Verify_Thread(task, self.tool_parent_dir_path, test_subtask_event,
                                                   self.test_report_path, is_parallel, self.logger)
            threads.append(subtask_verify)
            subtask_verify.start()
            self.subtask_verify_threads.append(subtask_verify)

        for thread in threads:
            if isinstance(thread, Subtask_Test_Thread):
                thread.join()

        for subtask_verify_thread in self.subtask_verify_threads:
            subtask_verify_thread.stop()

        self.stop_event.set()

        for task in self.tasks:
            task_work_path = os.path.join(self.tool_parent_dir_path, task['work-path'])
            for log in task['log-paths']:
                src_path_log_path = os.path.join(task_work_path, log['path'])
                dst_path_log_path = os.path.join(self.test_report_path, log['path'])
                if os.path.exists(src_path_log_path):
                    shutil.move(src_path_log_path, dst_path_log_path)

    def stop(self):
        self.stop_event.set()
