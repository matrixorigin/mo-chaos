import logging
import datetime
import os

class Logger():
    def __init__(self, name):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level="INFO")

    def console_handler(self, level="INFO"):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(self.get_formatter()[0])
        return console_handler
    

    def file_handler(self, type, level="INFO"):

        time = str(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        

        if type == "log":
            log_folder = "log"
            try:
                os.makedirs(log_folder)
            except OSError:
                pass
            logfile = "log{}.txt".format(time)
            file_handler = logging.FileHandler(os.path.join(log_folder, logfile), mode='a', encoding="utf-8")
        elif type == "report":
            report_folder = "report"
            try:
                os.makedirs(report_folder)
            except OSError:
                pass
            report_file = "report{}.txt".format(time)
            file_handler = logging.FileHandler(os.path.join(report_folder, report_file), mode='a', encoding="utf-8")
               
        file_handler.setLevel(level)
        file_handler.setFormatter(self.get_formatter()[1])
        return file_handler


    def get_formatter(self):
        console_fmt = logging.Formatter(fmt="%(asctime)s  [%(levelname)s]  [%(filename)s:%(lineno)d, %(funcName)s]  %(message)s")
        file_fmt = logging.Formatter(fmt="%(asctime)s  [%(levelname)s]  [%(filename)s:%(lineno)d, %(funcName)s]  %(message)s")
        return console_fmt, file_fmt

    
    def get_log(self, type):
        self.logger.addHandler(self.console_handler())
        self.logger.addHandler(self.file_handler(type))






    





