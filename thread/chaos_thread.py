import threading
import yaml
import subprocess
import time
import random
import pymysql
import logging
import os
from queue import Queue

def load_yaml(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

class Chaos_Thread:
    def __init__(self, chaos_yaml_full_path, cm_chaos_yml_path, logger):
        self.chaos_yaml_data = load_yaml(chaos_yaml_full_path)
        cm_chaos = self.chaos_yaml_data.get('chaos', {}).get('cm-chaos', [])
        sql_chaos = self.chaos_yaml_data.get('chaos', {}).get('sql-chaos', [])
        if cm_chaos is None:
            cm_chaos = []
        if sql_chaos is None:
            sql_chaos = []
        self.tasks = cm_chaos + sql_chaos
        self.mode = self.chaos_yaml_data.get('chaos', {}).get('chaos_combination', {}).get('mode', 'in-turn')
        self.namespace = self.chaos_yaml_data.get('chaos', {}).get('namespace', {})
        self.cm_chaos_yml_path = cm_chaos_yml_path
        self.logger = logger
        self.stop_event = threading.Event()
        self.db_config = self.chaos_yaml_data.get('chaos', {}).get('mo-env', {})

    # 顺序执行
    def execute_tasks(self):
        if self.mode == "in-turn":
            self.execute_task_sequential()
        elif self.mode == "random-turn":
            self.execute_task_random()
        elif self.mode == "parallel":
            self.execute_task_parallel()
        else:
            logger.error("execute task mode f{self.mode} not exists")

    def execute_task_sequential(self):
        while not self.stop_event.is_set():
            for task in self.tasks:
                if self.stop_event.is_set():
                    break
                self.run_task(task)

    # 随机执行
    def execute_task_random(self):
        while not self.stop_event.is_set():
            tasks = self.tasks.copy()
            random.shuffle(tasks)
            for task in tasks:
                if self.stop_event.is_set():
                   break
                self.run_task(task)

    # 并行执行
    def execute_task_parallel(self):
        while not self.stop_event.is_set():
            threads = []
            for task in self.tasks:
                if self.stop_event.is_set():
                    break
                t = threading.Thread(target=self.run_task, args=(task,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

    def database_flush_chaos(self, task, db_config):
        connection = None
        self.logger.info(f"execute sql chaos: {task['name']}")
        try:
            # Establish a connection to the MySQL server
            connection = pymysql.connect(**db_config)
            with connection.cursor() as cursor:
                sql = "use {}".format(task['dbname'])
                self.logger.info(f"execute sql {sql}")
                cursor.execute(sql)
                sql = "show tables"
                self.logger.info(f"execute sql {sql}")
                cursor.execute(sql)
                tables = cursor.fetchall()

                for _ in range(task['times']):
                    for table in tables:
                        sql = "SELECT mo_ctl('dn', 'flush', '{}.{}')".format(task['dbname'], table[0])
                        self.logger.info(f"execute sql {sql}")
                        cursor.execute(sql)
                        connection.commit()
                    time.sleep(task['interval'])
        except pymysql.MySQLError as e:
            self.logger.error(f"Error {e}")
        finally:
            # Close the connection
            if connection:
                connection.close()

    def table_flush_chaos(self, task, db_config):
        connection = None
        self.logger.info(f"execute sql chaos: {task['name']}")
        try:
            # Establish a connection to the MySQL server
            connection = pymysql.connect(**db_config)
            with connection.cursor() as cursor:

                for _ in range(task['times']):
                    sql = "SELECT mo_ctl('dn', 'flush', '{}.{}')".format(task['dbname'], task['tablename'])
                    self.logger.info(f"execute sql {sql}")
                    cursor.execute(sql)
                    connection.commit()
                    time.sleep(task['interval'])
        except pymysql.MySQLError as e:
            self.logger.error(f"Error {e}")
        finally:
            # Close the connection
            if connection:
                connection.close()

    def database_merge_chaos(self, task, db_config):
        connection = None
        self.logger.info(f"execute sql chaos: {task['name']}")
        try:
            # Establish a connection to the MySQL server
            connection = pymysql.connect(**db_config)
            with connection.cursor() as cursor:
                sql = "use {}".format(task['dbname'])
                self.logger.info(f"execute sql {sql}")
                cursor.execute(sql)
                sql = "show tables"
                self.logger.info(f"execute sql {sql}")
                cursor.execute(sql)
                tables = cursor.fetchall()

                for _ in range(task['times']):
                    for table in tables:
                        sql = "SELECT mo_ctl('dn', 'mergeobjects', '{}.{}:all:small')".format(task['dbname'], table[0])
                        self.logger.info(f"execute sql {sql}")
                        try:
                            cursor.execute(sql)
                            connection.commit()
                        except pymysql.MySQLError as merge_exception:
                            self.logger.error(f"Error {merge_exception}")
                    time.sleep(task['interval'])
        except pymysql.MySQLError as e:
            self.logger.error(f"Error {e}")
        finally:
            # Close the connection
            if connection:
                connection.close()

    def table_merge_chaos(self, task, db_config):
        connection = None
        self.logger.info(f"execute sql chaos: {task['name']}")
        try:
            # Establish a connection to the MySQL server
            connection = pymysql.connect(**db_config)
            with connection.cursor() as cursor:
                for _ in range(task['times']):
                    sql = "SELECT mo_ctl('dn', 'mergeobjects', '{}.{}:all:small')".format(task['dbname'],
                                                                                          task['tablename'])
                    self.logger.info(f"execute sql {sql}")
                    try:
                        cursor.execute(sql)
                        connection.commit()
                    except pymysql.MySQLError as merge_exception:
                        self.logger.error(f"Error {merge_exception}")
                    time.sleep(task['interval'])
        except pymysql.MySQLError as e:
            self.logger.error(f"Error {e}")
        finally:
            # Close the connection
            if connection:
                connection.close()

    def checkpoint_chaos(self, task, db_config):
        connection = None
        self.logger.info(f"execute sql chaos: {task['name']}")
        try:
            # Establish a connection to the MySQL server
            connection = pymysql.connect(**db_config)
            with connection.cursor() as cursor:
                for _ in range(task['times']):
                    sql = "select mo_ctl('dn','checkpoint','');"
                    self.logger.info(f"execute sql {sql}")
                    try:
                        cursor.execute(sql)
                        connection.commit()
                    except pymysql.MySQLError as merge_exception:
                        self.logger.error(f"Error {merge_exception}")
                    time.sleep(task['interval'])
        except pymysql.MySQLError as e:
            self.logger.error(f"Error {e}")
        finally:
            # Close the connection
            if connection:
                connection.close()

    def execute_sql_chaos(self, task, db_config):
        if task['type'] == 'database_flush_chaos':
            self.database_flush_chaos(task, db_config)
        elif task['type'] == 'table_flush_chaos':
            self.table_flush_chaos(task, db_config)
        elif task['type'] == 'database_merge_chaos':
            self.database_merge_chaos(task, db_config)
        elif task['type'] == 'table_merge_chaos':
            self.table_merge_chaos(task, db_config)
        elif task['type'] == 'checkpoint_chaos':
            self.checkpoint_chaos(task, db_config)
        else:
            self.logger.info(f"sql chaos name {task['type']} is not exists!")

    def execute_cm_chaos(self, task):
        cm_chaos_yml_file = os.path.join(self.cm_chaos_yml_path, task['name'] + ".yaml")
        # Save the kubectl YAML content to a local file
        if os.path.exists(cm_chaos_yml_file):  # 判断文件是否存在
            os.remove(cm_chaos_yml_file)
        with open(cm_chaos_yml_file, 'w') as f:
            f.write(task['kubectl_yaml'])
        self.logger.info(f"Saved kubectl YAML to {cm_chaos_yml_file}")

        command_apply = f"kubectl apply -f {cm_chaos_yml_file}"
        command_delete = f"kubectl delete -f {cm_chaos_yml_file}"

        try:
            for _ in range(task['times']):
                self.logger.info(f"Executing: {command_apply}")
                result = subprocess.run(command_apply, shell=True, check=True, capture_output=True, text=True)
                self.logger.info(f"Success: {result.stdout}")
                time.sleep(task['interval'])
                if task['is_delete_after_apply']:
                    # Clean up after execution
                    self.logger.info(f"Executing: {command_delete}")
                    result = subprocess.run(command_delete, shell=True, check=True, capture_output=True, text=True)
                    self.logger.info(f"Cleanup Success: {result.stdout}")

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error executing command: {e.stderr}")

    def execute_chaos(self, task):
        command_delete_all_cm_chaos = f"kubectl delete chaos -n {self.namespace} --all"
        try:
            result = subprocess.run(command_delete_all_cm_chaos, shell=True, check=True, capture_output=True, text=True)
            self.logger.info(f"{command_delete_all_cm_chaos} success: {result.stdout}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"{command_delete_all_cm_chaos} failed: {e.stderr}")

        if 'kubectl_yaml' in task:
            self.execute_cm_chaos(task)
        else:
            self.execute_sql_chaos(task, self.db_config)

    # 执行每个任务
    def run_task(self, task):
        task_name = task['name']
        times = task['times']
        interval = task['interval']
        for i in range(times):
            if self.stop_event.is_set():
                break
            self.logger.info(f"Executing Chaos Task: {task_name}, iteration {i + 1}")
            self.execute_chaos(task)
            time.sleep(interval)

    def stop(self):
        self.stop_event.set()


