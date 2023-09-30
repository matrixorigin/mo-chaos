import time
import subprocess
from Yaml import Yaml
import yaml
from Logger import Logger
import os
import re

def command_for_case(conf, ip, port, user, pwd):
    case_path = conf["case-path"]

    if ip == None:
        ip = conf['case-ip']
    
    if port == None:
        port = conf['case-port']

    if user == None:
        user = conf['case-user']
    
    if pwd == None:
        pwd = conf['case-pwd']
  
    if conf['case-name'] == 'mo-tester':
        command = command_for_tester(case_path, ip, port, user, pwd)
    elif conf['case-name'] == 'mo-tpch':
        command = command_for_tpch(ip)
    elif conf['case-name'] == 'mo-tpcc':
        command = command_for_tpcc()
    else:
        print('Error')
        exit(0)

    return conf, command


def command_for_tester(path, ip, port, user, pwd):
    with open("../mo-tester/mo.yml", 'r') as file:
        data = yaml.safe_load(file)

    addr = "{}:{}".format(ip, port)
    data["jdbc"]["server"][0]["addr"] = addr
    data["user"]["name"] = user
    data["user"]["password"] = pwd    

    with open("/root/mo-tester/mo.yml", 'w') as file:
        yaml.dump(data, file)

    command = "./run.sh -n {} -m run -g".format(path)
    return command


def command_for_tpch(ip):
    command = "./run.sh -h {ip} -q all -s 10 -t 5"
    return command


def command_for_tpcc():
    command = "./runBenchmark.sh props_10.mo"
    return command


def run_case_tool(conf, command, log, report):
    times = conf["case-times"]
    error_folder = "error"
    try:
        os.makedirs(error_folder)
    except OSError:
        pass

    for i in range(1, times+1):        
        os.chdir("/root/mo-tester")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 
        stdout, stderr =  process.communicate()
        try:
            with open("../mo-tester/report/report.txt", "r") as file:
                report_content = file.read().strip()

            if i == 1:
                report_content = "The result of the 1st case tests: \n{}".format(report_content)
            elif i == 2:
                report_content = "The result of the 2nd case tests: \n{}".format(report_content)
            elif i == 3:
                report_content = "The result of the 3rd case tests: \n{}".format(report_content)
            else:
                report_content = "The result of the {}th case tests: \n{}".format(i, report_content)

            log.logger.info(report_content)
            
            fail_or_not(report_content, i, log, report)
                       
            
        except IOError:
            log.logger.error("There is something wrong with case tests:\n{}".format(stderr.strip()))
        
    os.chdir("/root/chaos_injection_tool")
    

def fail_or_not(content, i, log, report):
    matches = re.findall(r'FAILED :.', content)
    fail = matches[0]
    num = fail[len(fail)-1]
    if int(num) > 0:
        error_file = "{}th_error.txt".format(i)
        copy_command = "cp /root/mo-tester/report/error.txt /root/chaos_injection_tool/error/{}".format(error_file)
        subprocess.Popen(copy_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)  
        error_path = "The path for error.txt: /root/chaos_injection_tool/error/{}".format(error_file)

        log.logger.info(error_path)
        report.logger.info(content)
        report.logger.info(error_path)