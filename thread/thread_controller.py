import threading
import yaml
import subprocess
import time
import random
import pymysql
import logging
import os
import re
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
        self.db_counter = 0

    def execute_sql(self, sql):
        try:
            conn = pymysql.connect(
                host='10.222.6.253',
                port=6001,
                user='tpcc_test:admin',
                password='111',
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute(sql)
            conn.commit()
            cursor.close()
            conn.close()
            self.logger.info(f"SQL executed successfully: {sql}")
        except Exception as e:
            self.logger.error(f"Error executing SQL {sql}: {e}")
            raise

    def update_props_file_db(self, db_name):
        tpcc_path = os.path.join(os.environ.get('GITHUB_WORKSPACE'), 'mo-chaos/test-tool/mo-tpcc')
        props_file_path = os.path.join(tpcc_path, "props.mo")
        try:
            with open(props_file_path, 'r') as f:
                content = f.read()
            pattern = r'(conn=jdbc:mysql://[^/]+/)[^?]+(\?.*)'
            new_content = re.sub(pattern, r'\1' + db_name + r'\2', content)
            with open(props_file_path, 'w') as f:
                f.write(new_content)
            self.logger.info(f"Updated props.mo with db={db_name}")
        except Exception as e:
            self.logger.error(f"Error updating props.mo: {e}")
            raise

    def perform_database_switch(self):
        """根据计数器执行数据库切换和配置文件更新"""
        self.db_counter += 1
        if self.db_counter % 2 == 1:
            target_db = "tpcc_10_bak"
            sql_to_run = "drop database if exists tpcc_10_bak; create database tpcc_10_bak clone tpcc_10;"
        else:
            target_db = "tpcc_10"
            sql_to_run = "drop database if exists tpcc_10; create database tpcc_10 clone tpcc_10_bak;"

        self.logger.info(f"Database switch cycle #{self.db_counter}. Target DB: {target_db}")

        self.execute_sql(sql_to_run)
        self.update_props_file_db(target_db)

    def restart_test_tool(self):
        """重启测试工具"""
        self.logger.info("Restarting test tool...")

        self.test_class.stop()

        time.sleep(5)

        self.test_class = Test_Thread(
            self.test_class.test_yaml_data,
            self.logger,
            self.test_tool_parent_dir_path,
            self.test_class.test_tool_report_parent_dir_path
        )

        test_thread = threading.Thread(target=self.test_class.execute_tasks)
        test_thread.start()
        return test_thread

    def start(self):
        self.perform_database_switch()
        test_thread = threading.Thread(target=self.test_class.execute_tasks)
        test_thread.start()

        chaos_thread = threading.Thread(target=self.chaos_class.execute_tasks)
        chaos_thread.start()

        while not self.test_class.stop_event.is_set():
            # 等待一段时间后切换数据库
            time.sleep(10)  # 每小时的切换间隔，可根据需要调整

            # 执行数据库切换
            self.perform_database_switch()

            # 重启测试工具
            test_thread = self.restart_test_tool()

        # self.test_class.stop_event.wait()

        self.chaos_class.stop()

        chaos_thread.join()

        self.logger.info("Test tasks finished, stopping chaos tasks.")

