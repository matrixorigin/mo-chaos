from litmus.scenarios.base import Base


class DnPodDelete(Base):
    def __init__(self):
        super().__init__()
        self.name = 'dn-pod-delete'
