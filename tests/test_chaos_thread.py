import unittest
from unittest.mock import Mock, patch

from thread.chaos_thread import Chaos_Thread


class ExecuteChaosTest(unittest.TestCase):
    def test_cm_chaos_does_not_run_generic_global_cleanup(self):
        chaos_thread = Chaos_Thread.__new__(Chaos_Thread)
        chaos_thread.namespace = "mo-chaos-test"
        chaos_thread.db_config = {}
        chaos_thread.logger = Mock()
        chaos_thread.execute_cm_chaos = Mock()
        task = {"name": "kill-cn", "kubectl_yaml": "kind: PodChaos"}

        with patch(
            "thread.chaos_thread.subprocess.run",
            side_effect=AssertionError("unexpected generic kubectl cleanup"),
        ):
            chaos_thread.execute_chaos(task)

        chaos_thread.execute_cm_chaos.assert_called_once_with(task)


if __name__ == "__main__":
    unittest.main()
