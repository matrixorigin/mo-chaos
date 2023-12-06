from litmus.scenarios.base import Base


class PodDelete(Base):
    def __init__(self):
        super().__init__()
        self.name = 'pod-delete'
