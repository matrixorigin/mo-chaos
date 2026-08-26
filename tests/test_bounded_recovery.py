from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from bounded_recovery.campaign import evaluate_campaign, validate_attempt_result
from bounded_recovery.cli import main
from bounded_recovery.contract import ContractError, canonical_digest
from bounded_recovery.replay import ReplayDriver
from bounded_recovery.scenario import (
    BoundedRecoveryScenario,
    FaultReceipt,
    HarnessFailure,
    ScenarioConfig,
    TARGET_REF,
    TEST_CONTRACT_ID,
)


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


def run_fixture(
    fixture: dict, driver: ReplayDriver | None = None
) -> tuple[dict, ReplayDriver, list[dict]]:
    config = ScenarioConfig(**fixture["config"])
    driver = driver or ReplayDriver(fixture)
    partials = []
    result = BoundedRecoveryScenario(
        config,
        driver,
        attempt_id=fixture["attempt_id"],
        on_partial=partials.append,
    ).run()
    return result, driver, partials


def redigest(value: dict) -> None:
    unsigned = dict(value)
    unsigned.pop("content_digest", None)
    value["content_digest"] = canonical_digest(unsigned)


def forged_green_attempts() -> list[dict]:
    attempts = []
    for index in range(20):
        attempt = {
            "format": "recovery-contract-result/v1",
            "target_ref": TARGET_REF,
            "test_contract_id": TEST_CONTRACT_ID,
            "attempt_id": f"forged-{index:02d}",
            "candidate_revision": None,
            "cluster": {
                "cluster_uid": None,
                "generation": None,
                "expected_image_digest": None,
            },
            "evidence_validity": "valid",
            "product_result": "passed",
            "oracle_observation": {"exercise_state": "exercised"},
            "probes": [
                {
                    "probe_kind": kind,
                    "remote_execution_witness": {"witness_state": "verified"},
                }
                for kind in ("baseline", "stale_window", "post_eviction")
            ],
            "cleanup": {"ok": True},
        }
        redigest(attempt)
        attempts.append(attempt)
    return attempts


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

    def test_exact_deadline_blocks_fault_injection_but_positive_time_allows_it(self):
        class CountingFaultDriver(ReplayDriver):
            fault_calls = 0

            def inject_fault(self, deadline: float):
                self.fault_calls += 1
                return super().inject_fault(deadline)

        exhausted = valid_fixture()
        exhausted["config"].update(
            scenario_budget_seconds=2,
            probe_budget_seconds=2,
        )
        exhausted["probes"]["baseline"]["duration_seconds"] = 2
        exhausted["fault"]["duration_seconds"] = 0
        exhausted["cleanup"]["duration_seconds"] = 0
        exhausted_driver = CountingFaultDriver(exhausted)
        result, exhausted_driver, _ = run_fixture(exhausted, exhausted_driver)

        self.assertEqual(0, exhausted_driver.fault_calls)
        self.assertEqual("not_started", result["timeline"]["fault_start"])
        self.assertEqual("not_evaluated", result["product_result"])

        positive = deepcopy(exhausted)
        positive["config"]["scenario_budget_seconds"] = 2.001
        positive_driver = CountingFaultDriver(positive)
        run_fixture(positive, positive_driver)
        self.assertEqual(1, positive_driver.fault_calls)

    def test_fault_receipt_timestamp_is_validated_before_publication(self):
        class TimestampFaultDriver(ReplayDriver):
            injected_timestamp = RAW_DEAD_ENDPOINT

            def inject_fault(self, deadline: float):
                receipt = super().inject_fault(deadline)
                return FaultReceipt(
                    target_cn=receipt.target_cn,
                    endpoint=receipt.endpoint,
                    started_at_utc=self.injected_timestamp,
                )

        fixture = valid_fixture()
        malformed_driver = TimestampFaultDriver(fixture)
        result, malformed_driver, _ = run_fixture(fixture, malformed_driver)
        encoded = json.dumps(result, sort_keys=True)

        self.assertNotIn(RAW_DEAD_ENDPOINT, encoded)
        self.assertEqual("not_started", result["timeline"]["fault_start"])
        self.assertEqual("mismatch", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertIn("contract_error", result["failure_reason"])

        boundary_fixture = valid_fixture()
        boundary_driver = TimestampFaultDriver(boundary_fixture)
        boundary_driver.injected_timestamp = "2026-08-24T02:30:00.250000Z"
        boundary_result, _, _ = run_fixture(boundary_fixture, boundary_driver)
        self.assertEqual("passed", boundary_result["product_result"])

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

    def test_string_false_cleanup_is_rejected_and_cannot_pass(self):
        fixture = valid_fixture()
        fixture["cleanup"]["ok"] = "false"
        driver = ReplayDriver(fixture)
        with self.assertRaisesRegex(ContractError, "cleanup.ok must be a JSON boolean"):
            driver.cleanup(30)

        result, driver, _ = run_fixture(fixture)
        self.assertEqual(1, driver.cleanup_calls)
        self.assertEqual("infra_invalid", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertIsNone(result["cleanup"])

    def test_string_false_topology_flags_are_rejected_and_cannot_pass(self):
        for field in ("replacement_ready", "old_member_visible"):
            with self.subTest(field=field):
                fixture = valid_fixture()
                fixture["topology"][1][field] = "false"
                result, driver, _ = run_fixture(fixture)

                self.assertEqual(1, driver.cleanup_calls)
                self.assertEqual("mismatch", result["evidence_validity"])
                self.assertEqual("not_evaluated", result["product_result"])
                self.assertIn("contract_error", result["failure_reason"])

    def test_cleanup_past_shared_deadline_is_infra_invalid(self):
        fixture = valid_fixture()
        fixture["cleanup"]["duration_seconds"] = 60
        result, driver, _ = run_fixture(fixture)

        self.assertEqual(1, driver.cleanup_calls)
        self.assertGreater(driver.monotonic(), fixture["config"]["scenario_budget_seconds"])
        self.assertEqual("infra_invalid", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertEqual("infra", result["failure_domain"])
        self.assertEqual("cleanup_deadline_exceeded", result["failure_reason"])
        self.assertTrue(result["cleanup"]["ok"])

    def test_cleanup_failure_detail_is_bounded_and_endpoint_redacted(self):
        fixture = valid_fixture()
        fixture["cleanup"] = {
            "ok": False,
            "detail": "x" * 5000 + f" failed cleanup at {RAW_DEAD_ENDPOINT}",
        }
        result, _, _ = run_fixture(fixture)
        encoded = json.dumps(result, sort_keys=True)

        self.assertNotIn(RAW_DEAD_ENDPOINT, encoded)
        self.assertIn("cleanup_failed", result["failure_reason"])
        self.assertIn("diagnostic_digest=sha256:", result["failure_reason"])
        self.assertIn("source_truncated=true", result["failure_reason"])
        self.assertIn("cleanup_detail", result["cleanup"]["detail"])
        self.assertLess(len(result["failure_reason"]), 160)
        self.assertLess(len(result["cleanup"]["detail"]), 160)

    def test_all_driver_exception_classes_redact_external_endpoint(self):
        fixture = valid_fixture()

        class PreflightExceptionDriver(ReplayDriver):
            def __init__(self, value: dict, error: Exception) -> None:
                super().__init__(value)
                self.error = error

            def preflight(self, deadline: float):
                del deadline
                raise self.error

        cases = (
            ContractError(f"contract rejected {RAW_DEAD_ENDPOINT}"),
            HarnessFailure(f"checker failed at {RAW_DEAD_ENDPOINT}"),
            TimeoutError(f"driver timed out at {RAW_DEAD_ENDPOINT}"),
            RuntimeError(f"driver crashed at {RAW_DEAD_ENDPOINT}"),
        )
        for error in cases:
            with self.subTest(error=type(error).__name__):
                driver = PreflightExceptionDriver(fixture, error)
                result, driver, _ = run_fixture(fixture, driver)
                encoded = json.dumps(result, sort_keys=True)

                self.assertEqual(1, driver.cleanup_calls)
                self.assertNotIn(RAW_DEAD_ENDPOINT, encoded)
                self.assertEqual("not_evaluated", result["product_result"])
                self.assertIn("diagnostic_digest=sha256:", result["failure_reason"])
                self.assertLess(len(result["failure_reason"]), 160)

    def test_cleanup_exception_is_bounded_and_endpoint_redacted(self):
        fixture = valid_fixture()

        class CleanupExceptionDriver(ReplayDriver):
            def cleanup(self, deadline: float):
                del deadline
                self.cleanup_calls += 1
                raise RuntimeError(f"cleanup crashed at {RAW_DEAD_ENDPOINT}")

        driver = CleanupExceptionDriver(fixture)
        result, driver, _ = run_fixture(fixture, driver)
        encoded = json.dumps(result, sort_keys=True)

        self.assertEqual(1, driver.cleanup_calls)
        self.assertNotIn(RAW_DEAD_ENDPOINT, encoded)
        self.assertEqual("infra_invalid", result["evidence_validity"])
        self.assertEqual("not_evaluated", result["product_result"])
        self.assertIn("cleanup_exception", result["failure_reason"])
        self.assertIn("diagnostic_digest=sha256:", result["failure_reason"])


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

    def test_attempt_validator_preserves_genuine_non_green_results(self):
        fixtures = []
        preflight_mismatch = valid_fixture()
        preflight_mismatch["deployment"]["image_digests"].append("sha256:" + "9" * 64)
        fixtures.append(preflight_mismatch)
        stale_miss = valid_fixture()
        stale_miss["topology"] = [
            {
                "at_seconds": 1,
                "replacement_ready": True,
                "old_member_visible": False,
            }
        ]
        fixtures.append(stale_miss)
        harness_failure = valid_fixture()
        harness_failure["probes"]["stale_window"]["raise"] = "checker crashed"
        fixtures.append(harness_failure)
        timeout = valid_fixture()
        timeout["probes"]["stale_window"]["duration_seconds"] = 10
        fixtures.append(timeout)
        cleanup_failure = valid_fixture()
        cleanup_failure["cleanup"] = {"ok": False, "detail": "remained"}
        fixtures.append(cleanup_failure)
        late_cleanup = valid_fixture()
        late_cleanup["cleanup"]["duration_seconds"] = 60
        fixtures.append(late_cleanup)

        for fixture in fixtures:
            with self.subTest(expected=fixture):
                validate_attempt_result(run_fixture(fixture)[0])

    def test_campaign_rejects_wrong_count_duplicate_ids_and_drift(self):
        attempts = [run_fixture(valid_fixture(f"attempt-{index:02d}"))[0] for index in range(20)]
        with self.assertRaises(ContractError):
            evaluate_campaign(attempts[:-1])
        with self.assertRaises(ContractError):
            evaluate_campaign([*attempts, deepcopy(attempts[-1])])
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
        attempts[4]["attempt_id"] = "attempt-mutated-without-redigest"
        with self.assertRaisesRegex(ContractError, "content digest mismatch"):
            evaluate_campaign(attempts)

    def test_campaign_rejects_recomputed_minimal_forged_green_attempts(self):
        with self.assertRaisesRegex(ContractError, "attempt schema mismatch"):
            evaluate_campaign(forged_green_attempts())

    def test_campaign_rejects_recomputed_semantic_forgeries(self):
        original = [
            run_fixture(valid_fixture(f"attempt-{index:02d}"))[0]
            for index in range(20)
        ]

        def wrong_axis(attempt: dict) -> None:
            attempt["failure_domain"] = "product"

        def missing_probe(attempt: dict) -> None:
            del attempt["probes"][1]

        def wrong_witness_digest(attempt: dict) -> None:
            attempt["probes"][0]["remote_execution_witness_digest"] = (
                "sha256:" + "9" * 64
            )

        def wrong_timeline(attempt: dict) -> None:
            attempt["timeline"]["recovery_frontier"] = attempt["timeline"][
                "stale_probe_finished_at"
            ]

        def string_cleanup_bool(attempt: dict) -> None:
            attempt["cleanup"]["ok"] = "true"

        def untyped_cluster(attempt: dict) -> None:
            attempt["cluster"]["cluster_uid"] = None

        cases = (
            (wrong_axis, "result axes"),
            (missing_probe, "result axes"),
            (wrong_witness_digest, "witness digest mismatch"),
            (wrong_timeline, "recovery frontier"),
            (string_cleanup_bool, "must be a boolean"),
            (untyped_cluster, "non-empty bounded string"),
        )
        for mutate, message in cases:
            with self.subTest(case=mutate.__name__):
                attempts = deepcopy(original)
                mutate(attempts[0])
                redigest(attempts[0])
                with self.assertRaisesRegex(ContractError, message):
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

    def test_campaign_cli_rejects_recomputed_minimal_forged_green_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_path = root / "campaign.json"
            argv = ["campaign", "--output", str(output_path)]
            for index, attempt in enumerate(forged_green_attempts()):
                attempt_path = root / f"attempt-{index:02d}.json"
                attempt_path.write_text(json.dumps(attempt), encoding="utf-8")
                argv.extend(("--attempt", str(attempt_path)))

            self.assertEqual(2, main(argv))
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
