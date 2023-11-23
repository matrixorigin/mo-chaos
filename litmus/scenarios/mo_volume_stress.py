import os

from litmus.scenarios.base import Base


class MoIoStress(Base):
    def __init__(self):
        super().__init__()
        self.name = 'mo-io-stress'
        self.interval = os.getenv('FS_UTILIZATION_PERCENTAGE') or '100'
