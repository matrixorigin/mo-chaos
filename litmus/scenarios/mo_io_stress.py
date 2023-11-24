import os

from litmus.scenarios.base import Base


class MoIoStress(Base):
    def __init__(self):
        super().__init__()
        self.name = 'mo-io-stress'
        self.filesystem_utilization_bytes = os.getenv('FILESYSTEM_UTILIZATION_BYTES') or '40g'
        self.workers = os.getenv('WORKERS') or '16'
