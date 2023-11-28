import os

from litmus.scenarios.base import Base


class CnPodDelete(Base):
    def __init__(self):
        super().__init__()
        self.name = 'cn-pod-cpu-hog'
        self.cpu_load = os.getenv('CPU_LOAD') or '80'
        self.all_pods = True
        self.target_pods = ''
