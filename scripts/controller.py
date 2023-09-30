import subprocess
import time
import multiprocessing
from case_controller import run_case_tool
from Logger import Logger


def run_chaosmesh(apply, describe, result, times=1):    
    interval = 10  
    
    for i in range(1, times+1):
        process_apply = subprocess.Popen(apply, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 
        data_apply, error_apply = process_apply.communicate()
        if i == 1:
            prompt = "1st chaos: \n"
        elif i == 2:
            prompt = "2nd chaos: \n"
        elif i == 3:
            prompt = "3rd case tests: \n"
        else:
            prompt = "{}th case tests: \n".format(i)

        result.logger.info(prompt)
        result.logger.info(data_apply.strip())
        if error_apply != '':
            result.logger.error(error_apply)

        process_result = subprocess.Popen(describe, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 
        des_data, des_error = process_result.communicate()

        extracted = get_extracted(des_data)
        extracted = "The status of chaosmesh: \n{}".format(extracted)
        result.logger.info(extracted)
        if des_error != '':
            result.logger.error(des_error)

        process_terminate = subprocess.Popen("kubectl delete podchaos pod-failure-example -n chaos-mesh", stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 
        data_terminate, error_terminate = process_terminate.communicate()
        result.logger.info(data_terminate.strip())
        if error_terminate != '':
            result.logger.error(error_terminate)

        if i < times:
            time.sleep(interval)


def get_extracted(data):
    first_seg = get_segment(data, "Name:", "Labels:")
    start = data.index("Status:")
    second_seg = data[start:]
    extracted = "{}\n{}".format(first_seg, second_seg)
    return extracted


def get_segment(data, start, end):
    start_index = data.find(start)
    end_index = data.find(end)
    if start_index != -1 and end_index != -1:
        seg = data[start_index:end_index].strip()     
    return seg


def execute_both(apply, describe, case_command, conf, result, report):
    interval = 10
    times = (conf["case-times"]*32+conf["case-times"]*conf["case-interval"])//interval

    process_case = multiprocessing.Process(target=run_case_tool, args=(conf, case_command, result, report))

    if times > 0:
        process_chaosmesh = multiprocessing.Process(target=run_chaosmesh, args=(apply, describe, result, times))
    else:
        process_chaosmesh = multiprocessing.Process(target=run_chaosmesh, args=(apply, describe, result))
   

    process_case.start()
    time.sleep(conf["case-interval"])

    process_chaosmesh.start()

    process_case.join()
    process_chaosmesh.join()


def preparation(tool):
    if tool == 'mo-tester':
        prepare_tester()
    else:
        print('Error')
        exit(0)


def prepare_tester():
    subprocess.Popen("kubectl -n mo-chaos get pod  -l matrixorigin.io/component=CNSet -o wide | awk 'NR==2 {print $1}'", stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 
    subprocess.Popen("kubectl -n mo-chaos get pod  -l matrixorigin.io/component=CNSet -o wide | awk 'NR==3 {print $1}'", stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 
    subprocess.Popen("kubectl -n mo-chaos get pod  -l matrixorigin.io/component=CNSet -o wide | awk 'NR==4 {print $1}'", stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) 

    