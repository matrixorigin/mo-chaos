import kubernetes
from kubernetes import client, config

# Configs can be set in Configuration class directly or using helper utility
config.load_kube_config()


v1 = client.CoreV1Api()
v1.api_client.configuration.verify_ssl = False
print("Listing pods with their IPs:")
ret = v1.list_namespaced_pod('litmus')
for i in ret.items:
    print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))
