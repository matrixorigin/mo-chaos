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
        # Normalize types in db_config
        if 'port' in self.db_config:
            try:
                self.db_config['port'] = int(self.db_config['port'])
            except Exception:
                pass

    def _switch_tpcc_props(self, target_db_name):
        """
        After SQL chaos that logically switches DBs, update TPCC's props.mo to point
        to the desired database by invoking config/switch_tpcc_db.sh in the mo-tpcc
        work directory. Uses current mo-env (db_config) for connection overrides.
        """
        try:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(repo_root, 'config', 'switch_tpcc_db.sh')
            tpcc_work_dir = os.path.join(repo_root, 'test-tool', 'mo-tpcc')

            # Ensure script is executable
            subprocess.run(['chmod', '+x', script_path], check=True)

            # Prepare env overrides to avoid shell-quoting issues
            env = os.environ.copy()
            host = str(self.db_config.get('host', ''))
            port = str(self.db_config.get('port', ''))
            user = str(self.db_config.get('user', ''))
            password = str(self.db_config.get('password', ''))
            if host:
                env['HOST'] = host
            if port:
                env['PORT'] = str(port)
            if user:
                env['USER'] = user
            if password:
                env['PASS'] = password

            cmd = [script_path, '--to', target_db_name]
            self.logger.info(f"switch props.mo using: {cmd} in {tpcc_work_dir} with HOST={env.get('HOST')} PORT={env.get('PORT')} USER={env.get('USER')}")
            subprocess.run(cmd, cwd=tpcc_work_dir, env=env, check=True, capture_output=True, text=True)
            self.logger.info("switch props.mo success")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"switch props.mo failed: {e.stderr}")
        except Exception as e:
            self.logger.error(f"switch props.mo unexpected error: {e}")

    def _toggle_tpcc_props(self):
        """
        Toggle TPCC's props.mo between tpcc_10 and tpcc_10_bak using the switch script.
        """
        try:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(repo_root, 'config', 'switch_tpcc_db.sh')
            tpcc_work_dir = os.path.join(repo_root, 'test-tool', 'mo-tpcc')

            subprocess.run(['chmod', '+x', script_path], check=True)

            env = os.environ.copy()
            host = str(self.db_config.get('host', ''))
            port = str(self.db_config.get('port', ''))
            user = str(self.db_config.get('user', ''))
            password = str(self.db_config.get('password', ''))
            if host:
                env['HOST'] = host
            if port:
                env['PORT'] = str(port)
            if user:
                env['USER'] = user
            if password:
                env['PASS'] = password

            cmd = [script_path, '--toggle']
            self.logger.info(f"toggle props.mo using: {cmd} in {tpcc_work_dir} with HOST={env.get('HOST')} PORT={env.get('PORT')} USER={env.get('USER')}")
            subprocess.run(cmd, cwd=tpcc_work_dir, env=env, check=True, capture_output=True, text=True)
            self.logger.info("toggle props.mo success")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"toggle props.mo failed: {e.stderr}")
        except Exception as e:
            self.logger.error(f"toggle props.mo unexpected error: {e}")

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

    def execute_raw_sql(self, task, db_config):
        connection = None
        self.logger.info(f"execute raw sql chaos: {task['name']}")
        try:
            connection = pymysql.connect(**db_config)
            statements_text = task.get('sql', '')
            # Split by semicolon while preserving order; ignore empty statements
            statements = [stmt.strip() for stmt in statements_text.split(';') if stmt.strip()]
            if not statements:
                self.logger.info("no sql statements to execute")
                return
            for _ in range(task.get('times', 1)):
                with connection.cursor() as cursor:
                    for stmt in statements:
                        self.logger.info(f"execute sql {stmt}")
                        cursor.execute(stmt)
                    connection.commit()
                time.sleep(task.get('interval', 0))
        except pymysql.MySQLError as e:
            self.logger.error(f"Error {e}")
        finally:
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
                # After each CM chaos injection, toggle TPCC props to alternate DB
                self._toggle_tpcc_props()
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
        elif 'sql' in task:
            self.execute_raw_sql(task, self.db_config)
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


