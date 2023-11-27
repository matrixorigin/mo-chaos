from kubernetes import client, config


def fetch_pod_names(namespace: str, label: str) -> str:
    config.load_kube_config()
    # config.load_incluster_config()

    v1 = client.CoreV1Api()
    # ret = v1.list_namespaced_pod('chaos-ccpal6tim1ayi3q4', label_selector='matrixorigin.io/component=CNSet')
    ret = v1.list_namespaced_pod(namespace, label_selector=label)
    return ','.join([i.metadata.name for i in ret.items])
