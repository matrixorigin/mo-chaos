from scripts.Command import *
from scripts.controller import execute_both
from scripts.Logger import Logger

if __name__ == "__main__":
    cmd = Command()
    args = cmd.get_args()
    conf = cmd.get_conf(args.configure)

    apply, describe = cmd.get_chaosmesh_command(conf['chaos-name'])
    log = Logger("experiment")
    log.get_log("log")   
  
    report = Logger("report")
    report.logger.addHandler(report.file_handler("report"))
    conf, case_command = cmd.get_case_command(args, conf)
    execute_both(apply, describe, case_command, conf, log, report)



