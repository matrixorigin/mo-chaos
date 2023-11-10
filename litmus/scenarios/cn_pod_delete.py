import os


class CnPodDelete:
    def __init__(self):
        self.name = 'cn-pod-delete'
        self.duration = os.getenv('DURATION') or '60'
        self.interval = os.getenv('INTERVAL') or '10'
        self.namespace = os.getenv('NAMESPACE') or open(
            '/var/run/secrets/kubernetes.io/serviceaccount/namespace').read()
        self.label = os.getenv('LABEL') or 'matrixorigin.io/component=CNSet'
