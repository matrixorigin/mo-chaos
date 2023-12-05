from litmus.scenarios.base import Base


class CnPodDeleteAll(Base):
    def __init__(self):
        super().__init__()
        self.name = 'cn-pod-delete-all'
