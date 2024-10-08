import argparse
import threading
import time
import logging
import os
import sys

from thread.test_thread import *
from thread.chaos_thread import *
from thread.thread_controller import *

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Execute chaos faults and testcases ')
    parser.add_argument('-c', type=str, required=True, help='chaos config yaml')
    parser.add_argument('-t', type=str, required=True, help='test tool config')

    args = parser.parse_args()
    current_path = os.path.dirname(os.path.abspath(__file__))
    mo_chaos_log_path = os.path.join(current_path, "logs")
    cm_chaos_yml_path = os.path.join(current_path, "cm_yaml")
    config_dir_path = os.path.join(current_path, "config")
    test_tool_parent_dir_path = os.path.join(current_path, "test-tool")
    test_tool_report_parent_dir_path = os.path.join(current_path, "test-report")
    chaos_config_full_path = os.path.join(config_dir_path, args.c)
    test_config_full_path = os. path.join(config_dir_path, args.t)
    if not os.path.exists(mo_chaos_log_path):
        os.makedirs(mo_chaos_log_path)
    if not os.path.exists(cm_chaos_yml_path):
        os.makedirs(cm_chaos_yml_path)
    log_file_full_path = os.path.join(mo_chaos_log_path, "mo_chaos_test.log")
    logging.basicConfig(
        filename=log_file_full_path,
        level=logging.DEBUG,  # Set the logging level
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger('mo_chaos_test')
    if not os.path.exists(chaos_config_full_path):
        logger.error(f"The config file {chaos_config_full_path} is not exist!")
        sys.exit(1)
    if not os.path.exists(test_config_full_path):
        logger.error(f"The config file {test_config_full_path} is not exist!")
        sys.exit(1)

    controller = Thread_Controller(chaos_config_full_path, test_config_full_path, cm_chaos_yml_path, test_tool_parent_dir_path, test_tool_report_parent_dir_path, logger)
    controller.start()

if __name__ == '__main__':
    main()
