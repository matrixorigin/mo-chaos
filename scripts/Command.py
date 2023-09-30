import argparse
from case_controller import command_for_case
import yaml
from Yaml import Yaml


class Command():
    def __init__(self):
        self.parser = argparse.ArgumentParser(description='A tool to run chaosmesh and mo-test')
        conf = {}
        
    
    def arguments(self):    
        """Arguments for mo-test"""

        self.parser.add_argument('--configure', type=str, help='the name of the yaml file')
        self.parser.add_argument('--ip', type=str, help='the ip of the server')
        self.parser.add_argument('--port', type=str, help='the port of the server')
        self.parser.add_argument('--user', type=str, help='the name of the user')
        self.parser.add_argument('--pwd', type=str, help='the password')      


    def get_args(self):
        self.arguments()
        args = self.parser.parse_args()
        return args

    def get_conf(self, path):
        case_yaml = Yaml(path)
        conf = case_yaml.parse_case_yaml()
        return conf

    def get_chaosmesh_command(self, name):
        chaos = "{}.yaml".format(name)
        apply = "kubectl apply -f /root/chaos_injection_tool/chaosmesh/{}".format(chaos)
        describe = "kubectl describe podchaos {} -n chaos-mesh".format(name)  
        return apply, describe
  
    
    def get_case_command(self, args, conf):
        conf, case_command = command_for_case(conf, args.ip, args.port, args.user, args.pwd)
        return conf, case_command
    


        
        

    
    
