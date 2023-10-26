from typing import List

from litmus.cluster import get_cluster_id
from litmus.project import get_project_id
from litmus.query import query


class ChaosWorkflowRequest:
    def __init__(self, scenario_json_str: str, name: str, desc: str, project_id: str, cluster_id: str):
        self.workflow_manifest = scenario_json_str
        self.workflow_name = name
        self.workflow_description = desc
        self.cron_syntax = ""
        self.weightages = []
        self.is_custom_workflow = True
        self.project_id = project_id
        self.cluster_id = cluster_id


def create_workflow(scenario: ChaosWorkflowRequest) -> str:
    res = query('''
        mutation createChaosWorkFlow($input: ChaosWorkFlowRequest!) {
            createChaosWorkFlow(request: $input) {
                workflowID
                cronSyntax
                workflowName
                workflowDescription
                isCustomWorkflow
            }
        }
    ''', {
        "input": {
            "workflowManifest": scenario.workflow_manifest,
            "workflowName": scenario.workflow_name,
            "workflowDescription": scenario.workflow_description,
            "cronSyntax": scenario.cron_syntax,
            "weightages": scenario.weightages,
            "isCustomWorkflow": scenario.is_custom_workflow,
            "projectID": scenario.project_id,
            "clusterID": scenario.cluster_id
        }
    })
    return res['createChaosWorkFlow']['workflowID']


def get_workflow_run_stats(project_id: str, workflow_ids: List[str]) -> str:
    res = query('''
        query ListWorkflowRuns($request: ListWorkflowRunsRequest!) {
            listWorkflowRuns(request: $request) {
                totalNoOfWorkflowRuns
                workflowRuns {
                    workflowRunID
                    workflowID
                    clusterName
                    lastUpdated
                    projectID
                    clusterID
                    workflowName
                    clusterType
                    phase
                    resiliencyScore
                    experimentsPassed
                    experimentsFailed
                    experimentsAwaited
                    experimentsStopped
                    experimentsNa
                    totalExperiments
                    executionData
                    isRemoved
                    executedBy
                    weightages {
                        experimentName
                        weightage
                    }
                }
            }
        }''', {"request": {'projectID': project_id, 'workflowIDs': workflow_ids}})
    if len(res['listWorkflowRuns']['workflowRuns']) == 0:
        raise IndexError('run stats not found')
    return res['listWorkflowRuns']['workflowRuns'][0]['phase']
