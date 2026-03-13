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
from thread.chaos_thread import *
from thread.test_thread import *
class Thread_Controller:
    def __init__(self, chaos_yaml , test_yaml, cm_chaos_yml_path, test_tool_parent_dir_path,
                               test_tool_report_parent_dir_path, logger):
        self.chaos_class = Chaos_Thread(chaos_yaml, cm_chaos_yml_path, logger)
        self.test_class = Test_Thread(test_yaml, logger, test_tool_parent_dir_path, test_tool_report_parent_dir_path)
        self.logger = logger

    def start(self):
        test_thread = threading.Thread(target=self.test_class.execute_tasks)
        test_thread.start()

        chaos_thread = threading.Thread(target=self.chaos_class.execute_tasks)
        chaos_thread.start()

        self.test_class.stop_event.wait()

        self.chaos_class.stop()

        chaos_thread.join()

        self.logger.info("Test tasks finished, stopping chaos tasks.")

