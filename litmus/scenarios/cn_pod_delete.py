from litmus.scenarios.base import Base


class CnPodDelete(Base):
    def __init__(self):
        super().__init__()
        self.name = 'cn-pod-delete'
