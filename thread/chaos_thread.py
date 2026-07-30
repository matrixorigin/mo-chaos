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
        # 获取全局任务间隔时间（秒），默认为0（不等待）
        self.global_task_interval = self.chaos_yaml_data.get('chaos', {}).get('chaos_combination', {}).get('task_interval', 0)
        self.logger.info(f"Global task_interval loaded: {self.global_task_interval} seconds")

    # 顺序执行
    def execute_tasks(self):
        if self.mode == "in-turn":
            self.execute_task_sequential()
        elif self.mode == "random-turn":
            self.execute_task_random()
        elif self.mode == "parallel":
            self.execute_task_parallel()
        else:
            self.logger.error(f"execute task mode {self.mode} not exists")

    def execute_task_sequential(self):
        while not self.stop_event.is_set():
            for idx, task in enumerate(self.tasks):
                if self.stop_event.is_set():
                    break
                self.run_task(task)
                # 在任务之间添加间隔时间（包括最后一个任务）
                task_interval = task.get('task_interval', self.global_task_interval)
                self.logger.info(f"Task '{task.get('name', 'unknown')}' completed. Task interval: {task_interval} seconds (global: {self.global_task_interval})")
                if task_interval > 0:
                    self.logger.info(f"Waiting {task_interval} seconds before next task...")
                    time.sleep(task_interval)
                else:
                    self.logger.info(f"No wait interval (task_interval={task_interval})")

    # 随机执行
    def execute_task_random(self):
        while not self.stop_event.is_set():
            tasks = self.tasks.copy()
            random.shuffle(tasks)
            for idx, task in enumerate(tasks):
                if self.stop_event.is_set():
                   break
                self.run_task(task)
                # 在任务之间添加间隔时间（包括最后一个任务）
                task_interval = task.get('task_interval', self.global_task_interval)
                self.logger.info(f"Task '{task.get('name', 'unknown')}' completed. Task interval: {task_interval} seconds (global: {self.global_task_interval})")
                if task_interval > 0:
                    self.logger.info(f"Waiting {task_interval} seconds before next task...")
                    time.sleep(task_interval)
                else:
                    self.logger.info(f"No wait interval (task_interval={task_interval})")

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

    # kubectl apply/delete can hang (esp. NetworkChaos bandwidth finalizer/tc cleanup).
    # Without a timeout the sequential chaos loop blocks for hours and skips later tasks.
    KUBECTL_TIMEOUT_SEC = 120

    def run_kubectl(self, command, timeout=None):
        """Run kubectl with timeout; on timeout attempt non-blocking/force cleanup for deletes."""
        if timeout is None:
            timeout = self.KUBECTL_TIMEOUT_SEC
        self.logger.info(f"Executing: {command}")
        try:
            result = subprocess.run(
                command, shell=True, check=True, capture_output=True, text=True, timeout=timeout
            )
            return result
        except subprocess.TimeoutExpired as e:
            self.logger.error(f"kubectl timed out after {timeout}s: {command}")
            if "kubectl delete" in command:
                self.force_cleanup_after_delete_timeout(command)
            raise
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error executing command: {e.stderr}")
            raise

    def force_cleanup_after_delete_timeout(self, original_delete_cmd):
        """Best-effort unblock when Chaos Mesh CR delete hangs on finalizers."""
        # Prefer --wait=false so kubectl returns even if finalizers linger.
        force_cmds = []
        if " -f " in original_delete_cmd or original_delete_cmd.strip().endswith(".yaml"):
            # kubectl delete -f <file> ...
            base = original_delete_cmd.replace("kubectl delete", "kubectl delete --wait=false", 1)
            force_cmds.append(base)
            force_cmds.append(
                original_delete_cmd.replace(
                    "kubectl delete", "kubectl delete --force --grace-period=0 --wait=false", 1
                )
            )
        else:
            force_cmds.append(
                original_delete_cmd.replace(
                    "kubectl delete", "kubectl delete --wait=false --timeout=30s", 1
                )
            )
            force_cmds.append(
                original_delete_cmd.replace(
                    "kubectl delete", "kubectl delete --force --grace-period=0 --wait=false", 1
                )
            )
        for cmd in force_cmds:
            try:
                self.logger.info(f"Force cleanup: {cmd}")
                subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            except Exception as fe:
                self.logger.error(f"Force cleanup failed: {fe}")

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

        for _ in range(task['times']):
            if self.stop_event.is_set():
                break
            try:
                result = self.run_kubectl(command_apply)
                self.logger.info(f"Success: {result.stdout}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # Continue soak; do not abort remaining chaos tasks.
                continue
            time.sleep(task['interval'])
            if task['is_delete_after_apply']:
                try:
                    result = self.run_kubectl(command_delete)
                    self.logger.info(f"Cleanup Success: {result.stdout}")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    self.logger.error(
                        f"Cleanup failed or timed out for {cm_chaos_yml_file}; continuing next chaos task"
                    )

    def execute_chaos(self, task):
        # Resource type "chaos" does not exist; delete concrete Chaos Mesh kinds.
        ns = self.namespace
        command_delete_all_cm_chaos = (
            f"kubectl delete networkchaos,podchaos,stresschaos,iochaos "
            f"-n {ns} --all --wait=false --timeout=60s"
        )
        try:
            result = self.run_kubectl(command_delete_all_cm_chaos, timeout=90)
            self.logger.info(f"{command_delete_all_cm_chaos} success: {result.stdout}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # Not found / empty namespace is fine; log and continue.
            err = getattr(e, "stderr", str(e))
            self.logger.error(f"{command_delete_all_cm_chaos} failed: {err}")

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

    def stop(self):
        self.stop_event.set()


