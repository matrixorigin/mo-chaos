import yaml


class Yaml():
    def __init__(self, path):
        self.path = path
        


    def parse_case_yaml(self):
        with open(self.path, 'r') as file:
            data = yaml.safe_load(file)
        
        conf = {}

        conf["case-name"] = data['case']["name"]
        conf["case-times"] = data["case"]['times']
        conf["case-interval"] = data['case']['interval']
        conf["case-path"] = data['case']['path']
        conf["case-ip"] = data["case"]['ip']
        conf["case-port"] = data["case"]['port']
        conf["case-user"] = data["case"]['user']
        conf["case-pwd"] = data["case"]['pwd']

        conf['chaos-name'] = data['chaos']['name']
        conf['chaos-duration'] =  data['chaos']['duration']
        conf['chaos-interval'] = data['chaos']['interval']
        conf['chaos-total'] = data['chaos']['total']

        return conf

