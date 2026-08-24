from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from bounded_recovery.campaign import evaluate_campaign
from bounded_recovery.cli import main
from bounded_recovery.contract import ContractError, canonical_digest
from bounded_recovery.replay import ReplayDriver
from bounded_recovery.scenario import BoundedRecoveryScenario, ScenarioConfig


CANDIDATE = "1" * 40
IMAGE_DIGEST = "sha256:" + "2" * 64
QUERY_DIGESTS = {
    "baseline": "sha256:" + "3" * 64,
    "stale_window": "sha256:" + "4" * 64,
    "post_eviction": "sha256:" + "5" * 64,
}
RAW_DEAD_ENDPOINT = "10.10.211.174:6002"


def valid_fixture(attempt_id: str = "attempt-1") -> dict:
    probes = {}
    for index, kind in enumerate(("baseline", "stale_window", "post_eviction"), 1):
        statement_id = f"00000000-0000-0000-0000-{index:012d}"
        coordinator = "cn-survivor"
        probes[kind] = {
            "statement_id": statement_id,
            "query_digest": QUERY_DIGESTS[kind],
            "coordinator_cn": coordinator,
            "duration_seconds": 0.25,
            "outcome": "success",
            "consistency_ok": True,
            "remote_observations": [
                {
                    "remote_cn": "cn-replacement" if kind != "baseline" else "cn-target",
                    "endpoint": (
                        "10.10.187.82:6002" if kind != "baseline" else RAW_DEAD_ENDPOINT
                    ),
                    "source_kind": "bounded_cn_log",
                }
            ],
            "source_artifacts": [
                {
                    "logical_name": f"{kind}-cn-log.json",
                    "content": (
                        f"bounded trace statement_id={statement_id} "
                        f"query_digest={QUERY_DIGESTS[kind]}"
                    ),
                }
            ],
        }
    return {
        "attempt_id": attempt_id,
        "started_at_utc": "2026-08-24T02:30:00.000000Z",
        "config": {
            "candidate_revision": CANDIDATE,
            "cluster_uid": "cluster-generation-owner",
            "generation": "generation-7",
            "expected_image_digest": IMAGE_DIGEST,
            "scenario_budget_seconds": 30,
            "probe_budget_seconds": 2,
            "poll_interval_seconds": 0.5,
        },
        "deployment": {
            "cluster_uid": "cluster-generation-owner",
            "generation": "generation-7",
            "image_digests": [IMAGE_DIGEST],
            "fault_target_cn": "cn-target",
            "fault_endpoint": RAW_DEAD_ENDPOINT,
        },
        "fault": {
            "target_cn": "cn-target",
            "endpoint": RAW_DEAD_ENDPOINT,
            "duration_seconds": 0.1,
        },
        "topology": [
            {
                "at_seconds": 1.0,
                "replacement_ready": False,
                "old_member_visible": True,
            },
            {
                "at_seconds": 2.0,
                "replacement_ready": True,
                "old_member_visible": True,
            },
            {
                "at_seconds": 3.0,
                "replacement_ready": True,
                "old_member_visible": False,
            },
        ],
        "probes": probes,
        "cleanup": {"ok": True, "duration_seconds": 0.1},
    }


def run_fixture(fixture: dict) -> tuple[dict, ReplayDriver, list[dict]]:
    config = ScenarioConfig(**fixture["config"])
    driver = ReplayDriver(fixture)
    partials = []
    result = BoundedRecoveryScenario(
        config,
        driver,
        attempt_id=fixture["attempt_id"],
        on_partial=partials.append,
    ).run()
    return result, driver, partials


class BoundedRecoveryScenarioTest(unittest.TestCase):
    def test_success_requires_both_fault_probes_and_three_verified_witnesses(self):
        result, driver, partials = run_fixture(valid_fixture())

        self.assertEqual("passed", result["product_result"])
        self.assertEqual("valid", result["evidence_validity"])
        self.assertEqual("exercised", result["oracle_observation"]["exercise_state"])
        self.assertEqual(
            ["baseline", "stale_window", "post_eviction"],
            [probe["probe_kind"] for probe in result["probes"]],
        )
        self.assertTrue(
            all(
                probe["remote_execution_witness"]["witness_state"] == "verified"
                for probe in result["probes"]
            )
        )
        self.assertEqual(1, driver.cleanup_calls)
        self.assertGreaterEqual(len(partials), 5)
        self.assertNotIn(RAW_DEAD_ENDPOINT, json.dumps(result, sort_keys=True))
        without_digest = dict(result)
        digest = without_digest.pop("content_digest")
        self.assertEqual(digest, canonical_digest(without_digest))

    def test_stale_window_miss_is_not_exercised_not_passed(self):
        fixture = valid_fixture()
        fixture["topology"] = [
            {
                "at_seconds": 1.0,
                "replacement_ready": False,
                "old_member_visible": True,
            },
            {
                "at_seconds": 2.0,
                "replacement_ready": True,
                "old_member_visible": False,
            },
        ]
        result, driver, _ = run_fixture(fixture)

        self.assertEqual("not_evaluated", result["product_result"])
        self.assertEqual("not_exercised", result["oracle_observation"]["exercise_state"])
        self.assertEqual(["baseline", "post_eviction"], [p["probe_kind"] for p in result["probes"]])
        self.assertEqual(1, driver.cleanup_calls)

    def test_mixed_image_preflight_blocks_before_fault(self):
        fixture = valid_fixture()
        fixture["deployment"]["image_digests"].append("sha256:" + "9" * 64)
        result, driver, partials = run_fixture(fixture)

        self.assertEqual("blocked", result["execution_state"])
        self.assertEqual("mismatch", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertEqual([], result["probes"])
        self.assertEqual([], partials)
        self.assertEqual(1, driver.cleanup_calls)

    def test_wrong_statement_witness_is_mismatch_and_fault_is_not_injected(self):
        fixture = valid_fixture()
        fixture["probes"]["baseline"]["remote_observations"][0]["statement_id"] = "wrong"
        result, driver, _ = run_fixture(fixture)

        self.assertEqual("mismatch", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertEqual("mismatch", result["probes"][0]["remote_execution_witness"]["witness_state"])
        self.assertEqual("not_started", result["timeline"]["fault_start"])
        self.assertEqual(1, driver.cleanup_calls)

    def test_probe_without_remote_scope_is_not_observed(self):
        fixture = valid_fixture()
        fixture["probes"]["baseline"]["remote_observations"] = []
        result, _, _ = run_fixture(fixture)

        self.assertEqual("not_observed", result["probes"][0]["remote_execution_witness"]["witness_state"])
        self.assertEqual("not_evaluated", result["product_result"])

    def test_checker_failure_is_harness_not_product(self):
        fixture = valid_fixture()
        fixture["probes"]["stale_window"]["raise"] = "checker crashed"
        result, driver, _ = run_fixture(fixture)

        self.assertEqual("harness", result["failure_domain"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertEqual("failure", result["raw_conclusion"])
        self.assertEqual(1, driver.cleanup_calls)

    def test_stale_probe_timeout_is_bounded_product_failure(self):
        fixture = valid_fixture()
        fixture["probes"]["stale_window"]["duration_seconds"] = 10
        result, driver, _ = run_fixture(fixture)

        self.assertEqual("product", result["failure_domain"])
        self.assertEqual("failed", result["product_result"])
        self.assertEqual("timed_out", result["raw_conclusion"])
        self.assertEqual(1, driver.cleanup_calls)

    def test_allowlisted_retriable_stale_failure_can_satisfy_contract(self):
        fixture = valid_fixture()
        fixture["probes"]["stale_window"].update(
            outcome="retriable_error",
            consistency_ok=None,
            error_class="backend_create_timeout",
        )
        result, _, _ = run_fixture(fixture)

        self.assertEqual("passed", result["product_result"])

    def test_cleanup_failure_invalidates_an_otherwise_passing_attempt(self):
        fixture = valid_fixture()
        fixture["cleanup"] = {"ok": False, "detail": "chaos object remained"}
        result, _, _ = run_fixture(fixture)

        self.assertEqual("infra_invalid", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertEqual("infra", result["failure_domain"])


class CampaignTest(unittest.TestCase):
    def test_exact_twenty_attempt_campaign_passes_without_overwriting_attempts(self):
        attempts = [run_fixture(valid_fixture(f"attempt-{index:02d}"))[0] for index in range(20)]
        result = evaluate_campaign(attempts)

        self.assertTrue(result["passed"])
        self.assertEqual(20, result["stale_window_exercised"])
        self.assertEqual(20, len(result["attempts"]))
        self.assertEqual(20, len({item["content_digest"] for item in result["attempts"]}))

    def test_campaign_preserves_a_not_exercised_attempt_and_fails_gate(self):
        attempts = [run_fixture(valid_fixture(f"attempt-{index:02d}"))[0] for index in range(20)]
        missed = valid_fixture("attempt-07")
        missed["topology"] = [
            {
                "at_seconds": 1.0,
                "replacement_ready": True,
                "old_member_visible": False,
            }
        ]
        attempts[7] = run_fixture(missed)[0]
        result = evaluate_campaign(attempts)

        self.assertFalse(result["passed"])
        self.assertEqual(19, result["stale_window_exercised"])
        self.assertEqual({"not_evaluated": 1, "passed": 19}, result["product_result_counts"])

    def test_campaign_rejects_wrong_count_duplicate_ids_and_drift(self):
        attempts = [run_fixture(valid_fixture(f"attempt-{index:02d}"))[0] for index in range(20)]
        with self.assertRaises(ContractError):
            evaluate_campaign(attempts[:-1])
        duplicated = deepcopy(attempts)
        duplicated[1]["attempt_id"] = duplicated[0]["attempt_id"]
        with self.assertRaises(ContractError):
            evaluate_campaign(duplicated)
        drifted = deepcopy(attempts)
        drifted[2]["candidate_revision"] = "a" * 40
        with self.assertRaises(ContractError):
            evaluate_campaign(drifted)

    def test_campaign_rejects_tampered_attempt_even_if_claimed_result_is_green(self):
        attempts = [run_fixture(valid_fixture(f"attempt-{index:02d}"))[0] for index in range(20)]
        attempts[4] = deepcopy(attempts[4])
        attempts[4]["timeline"]["fault_start"] = "2026-08-24T00:00:00.000000Z"
        with self.assertRaisesRegex(ContractError, "content digest mismatch"):
            evaluate_campaign(attempts)


class OfflineCliTest(unittest.TestCase):
    def test_replay_writes_partial_and_final_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "fixture.json"
            output_path = root / "recovery-contract-result.json"
            fixture_path.write_text(json.dumps(valid_fixture()), encoding="utf-8")

            self.assertEqual(
                0,
                main(
                    [
                        "replay",
                        "--fixture",
                        str(fixture_path),
                        "--output",
                        str(output_path),
                    ]
                ),
            )
            self.assertTrue(output_path.exists())
            self.assertTrue((root / "partial-recovery-contract-result.json").exists())
            self.assertEqual("passed", json.loads(output_path.read_text())["product_result"])


if __name__ == "__main__":
    unittest.main()
