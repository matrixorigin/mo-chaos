from litmus.query import query


def get_cluster_id(name: str, project_id: str) -> str:
    res = query('''
        query listClusters($projectID: String!, $clusterType: String){
            listClusters(projectID: $projectID, clusterType: $clusterType){
                clusterID
                clusterName
            }
        }''', {"projectID": project_id})
    for c in res['listClusters']:
        if c['clusterName'].lower() == name.lower():
            return c['clusterID']
    return ''
