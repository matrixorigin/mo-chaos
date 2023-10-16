import json
import os

from yaml import Loader

from litmus.cluster import get_cluster_id
from litmus.project import get_project_id
from litmus.workflow import create_workflow, ChaosWorkflowRequest, get_workflow_run_stats
import yaml
import time
from string import Template
import importlib
import inspect
from datetime import datetime

test_name = os.getenv('NAME') or 'cn-pod-delete'


def load_scenario_class() -> dict:
    # iterate all scenario class
    for f in os.scandir('litmus/scenarios'):
        if f.name.endswith('.py'):
            s_module = importlib.import_module(f'litmus.scenarios.{f.name.replace('.py', '')}')
            for _, obj in inspect.getmembers(s_module):
                if inspect.isclass(obj):
                    s_class = obj()
                    if s_class.name == test_name:
                        return s_class.__dict__
    raise ModuleNotFoundError(f'test class for {test_name} not found')


if __name__ == '__main__':
    # some hack tricks to load configuration dynamically
    params = load_scenario_class()
    # choose scenario
    with open(f'litmus/scenarios/{test_name}.yaml', 'r') as f:
        scenario = yaml.load(f.read(), Loader=Loader)

    # project id should always be same
    project_id = get_project_id()

    # choose cluster
    cluster_id = get_cluster_id('self-agent', project_id)

    # set scenario name, using epoch milliseconds
    generated_name = f'{test_name}-{int(time.time())}'
    scenario['metadata']['name'] = generated_name
    # fill the parameters
    scenario = Template(json.dumps(scenario)).safe_substitute(params)

    scenario = ChaosWorkflowRequest(scenario, generated_name, '', project_id, cluster_id)
    workflow_id = create_workflow(scenario)
    # workflow_id = '788cbd71-c46c-4584-99a8-98bc181439bd'
    print(f'{generated_name} started at {datetime.now().ctime()}')

    # fetch status until it's done
    while 1:
        phase = get_workflow_run_stats(project_id, [workflow_id])
        if phase.lower() not in ['running', 'pending']:
            print(f'{generated_name} ended at {datetime.now().ctime()}, status {phase}')
            break
