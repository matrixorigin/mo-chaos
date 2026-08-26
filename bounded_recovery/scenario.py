"""Single-deadline state machine for the bounded multi-CN recovery contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Protocol

from .contract import (
    ContractError,
    RemoteScopeObservation,
    SourceArtifact,
    build_remote_execution_witness,
    canonical_digest,
    parse_utc,
    require_digest,
    require_revision,
    stable_identity,
)


TARGET_REF = "target/chaos/multi-cn-bounded-recovery-v1"
TEST_CONTRACT_ID = "contract/chaos/multi-cn-stale-topology-bounded/v1"
CONTRACT_ALLOWED_RETRIABLE_ERRORS = (
    "backend_create_timeout",
    "retryable_topology_change",
)
MAX_EXTERNAL_DIAGNOSTIC_CHARS = 4096
EXTERNAL_DIAGNOSTIC_CODES = frozenset(
    {
        "cleanup_detail",
        "cleanup_failed",
        "cleanup_exception",
        "contract_error",
        "coverage_not_exercised",
        "harness_failure",
        "scenario_timeout",
        "unexpected_harness_failure",
    }
)


def _bounded_external_diagnostic(code: str, value: Any) -> str:
    """Serialize untrusted diagnostics as one allowlisted code and bounded digest."""

    if code not in EXTERNAL_DIAGNOSTIC_CODES:
        raise AssertionError(f"diagnostic code is not allowlisted: {code}")
    try:
        source = str(value)
    except Exception:
        source = "unprintable-external-diagnostic"
    hasher = hashlib.sha256()
    for offset in range(0, len(source), MAX_EXTERNAL_DIAGNOSTIC_CHARS):
        hasher.update(
            source[offset : offset + MAX_EXTERNAL_DIAGNOSTIC_CHARS].encode("utf-8")
        )
    digest = "sha256:" + hasher.hexdigest()
    truncated = "true" if len(source) > MAX_EXTERNAL_DIAGNOSTIC_CHARS else "false"
    return f"{code}; diagnostic_digest={digest}; source_truncated={truncated}"


class HarnessFailure(RuntimeError):
    """The observer/checker failed, so product behavior is not evaluable."""


class CoverageNotExercised(RuntimeError):
    """A safe precondition was absent, so no fault may be injected."""


class ScenarioDriver(Protocol):
    def monotonic(self) -> float: ...

    def utc_now(self) -> str: ...

    def preflight(self, deadline: float) -> "DeploymentObservation": ...

    def run_probe(self, kind: str, timeout_seconds: float) -> "ProbeResult": ...

    def inject_fault(self, deadline: float) -> "FaultReceipt": ...

    def observe_topology(self, deadline: float) -> "TopologyObservation": ...

    def wait(self, seconds: float, deadline: float) -> None: ...

    def cleanup(self, deadline: float) -> "CleanupReceipt": ...


@dataclass(frozen=True)
class ScenarioConfig:
    candidate_revision: str
    cluster_uid: str
    generation: str
    expected_image_digest: str
    scenario_budget_seconds: float = 1800.0
    probe_budget_seconds: float = 30.0
    poll_interval_seconds: float = 1.0
    allowed_retriable_errors: tuple[str, ...] = CONTRACT_ALLOWED_RETRIABLE_ERRORS

    def validate(self) -> None:
        require_revision(self.candidate_revision)
        require_digest(self.expected_image_digest, "expected_image_digest")
        if not self.cluster_uid or not self.generation:
            raise ContractError("cluster identity and generation are required")
        if self.scenario_budget_seconds <= 0:
            raise ContractError("scenario budget must be positive")
        if not 0 < self.probe_budget_seconds <= self.scenario_budget_seconds:
            raise ContractError("probe budget must fit inside the scenario budget")
        if not 0 < self.poll_interval_seconds <= self.probe_budget_seconds:
            raise ContractError("poll interval must fit inside the probe budget")


@dataclass(frozen=True)
class DeploymentObservation:
    cluster_uid: str
    generation: str
    image_digests: tuple[str, ...]
    fault_target_cn: str
    fault_endpoint: str


@dataclass(frozen=True)
class FaultReceipt:
    target_cn: str
    endpoint: str
    started_at_utc: str


@dataclass(frozen=True)
class TopologyObservation:
    observed_at_utc: str
    replacement_ready: bool
    old_member_visible: bool


@dataclass(frozen=True)
class CleanupReceipt:
    ok: bool
    finished_at_utc: str
    detail: str = ""


@dataclass(frozen=True)
class ProbeResult:
    kind: str
    statement_id: str
    query_digest: str
    started_at_utc: str
    finished_at_utc: str
    coordinator_cn: str
    outcome: str
    duration_seconds: float
    consistency_ok: bool | None
    error_class: str | None = None
    remote_observations: tuple[RemoteScopeObservation, ...] = field(default_factory=tuple)
    source_artifacts: tuple[SourceArtifact, ...] = field(default_factory=tuple)


class BoundedRecoveryScenario:
    def __init__(
        self,
        config: ScenarioConfig,
        driver: ScenarioDriver,
        *,
        attempt_id: str,
        on_partial: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        config.validate()
        if not attempt_id:
            raise ContractError("attempt_id is required")
        self.config = config
        self.driver = driver
        self.attempt_id = attempt_id
        self.on_partial = on_partial or (lambda _: None)
        self.started = driver.monotonic()
        self.deadline = self.started + config.scenario_budget_seconds
        self.probes: list[dict[str, Any]] = []
        self.started_at_utc = parse_utc(driver.utc_now())
        self.deadline_utc = self.started_at_utc + timedelta(
            seconds=config.scenario_budget_seconds
        )
        self.timeline: dict[str, str] = {
            "fault_start": "not_started",
            "replacement_ready_at": "unknown",
            "stale_window_observed_at": "unknown",
            "old_member_evicted_at": "unknown",
            "stale_probe_started_at": "not_exercised",
            "stale_probe_finished_at": "not_exercised",
            "recovery_probe_started_at": "not_started",
            "recovery_probe_finished_at": "not_started",
            "recovery_frontier": "unknown",
            "scenario_deadline": self.deadline_utc.isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z"),
        }
        self.failure_domain: str | None = None
        self.failure_reason: str | None = None
        self.evidence_validity = "partial"
        self.product_result = "not_evaluated"
        self.exercise_state = "not_exercised"
        self.cleanup_receipt: CleanupReceipt | None = None
        self.fault_receipt: FaultReceipt | None = None
        self.deployment: DeploymentObservation | None = None

    def _remaining(self) -> float:
        return max(0.0, self.deadline - self.driver.monotonic())

    def _require_remaining(self, stage: str) -> float:
        remaining = self._remaining()
        if remaining <= 0:
            raise TimeoutError(f"scenario deadline exhausted during {stage}")
        return remaining

    def _emit_partial(self, stage: str) -> None:
        partial = self._result(raw_conclusion="unknown", execution_state="started")
        partial.pop("content_digest")
        partial["stage"] = stage
        partial["content_digest"] = canonical_digest(partial)
        self.on_partial(partial)

    def _validate_deployment(self, value: DeploymentObservation) -> None:
        if value.cluster_uid != self.config.cluster_uid:
            raise ContractError("deployment cluster_uid mismatch")
        if value.generation != self.config.generation:
            raise ContractError("deployment generation mismatch")
        if not value.fault_target_cn or not value.fault_endpoint:
            raise ContractError("fault target identity is incomplete")
        observed = set(value.image_digests)
        if observed != {self.config.expected_image_digest}:
            raise ContractError("deployment image digests are mixed or unexpected")

    @staticmethod
    def _validate_topology(value: TopologyObservation) -> None:
        parse_utc(value.observed_at_utc)
        if type(value.replacement_ready) is not bool:
            raise ContractError("topology replacement_ready must be a boolean")
        if type(value.old_member_visible) is not bool:
            raise ContractError("topology old_member_visible must be a boolean")

    def _validate_fault_receipt(
        self, value: FaultReceipt, *, baseline_finished_at_utc: str
    ) -> None:
        assert self.deployment is not None
        if (
            value.target_cn != self.deployment.fault_target_cn
            or value.endpoint != self.deployment.fault_endpoint
        ):
            raise ContractError("fault receipt does not match the preflight target")
        baseline_finished = parse_utc(baseline_finished_at_utc)
        fault_started = parse_utc(value.started_at_utc)
        observed_now = parse_utc(self.driver.utc_now())
        if not (
            self.started_at_utc
            <= baseline_finished
            <= fault_started
            <= observed_now
            <= self.deadline_utc
        ):
            raise ContractError("fault receipt timestamp is outside the scenario interval")

    @staticmethod
    def _sanitize_cleanup(value: CleanupReceipt) -> CleanupReceipt:
        if type(value.ok) is not bool:
            raise ContractError("cleanup ok must be a boolean")
        parse_utc(value.finished_at_utc)
        detail = (
            _bounded_external_diagnostic("cleanup_detail", value.detail)
            if value.detail
            else ""
        )
        return CleanupReceipt(
            ok=value.ok,
            finished_at_utc=value.finished_at_utc,
            detail=detail,
        )

    def _run_probe(self, kind: str) -> dict[str, Any]:
        remaining = self._require_remaining(kind)
        budget = min(self.config.probe_budget_seconds, remaining)
        probe = self.driver.run_probe(kind, budget)
        if probe.kind != kind:
            raise HarnessFailure(f"driver returned {probe.kind} for {kind}")
        if probe.duration_seconds < 0 or probe.duration_seconds > budget:
            raise HarnessFailure(f"{kind} probe exceeded its bounded budget")
        self._validate_probe_outcome(probe)
        assert self.deployment is not None
        witness = build_remote_execution_witness(
            probe_kind=kind,
            candidate_revision=self.config.candidate_revision,
            cluster_uid=self.config.cluster_uid,
            generation=self.config.generation,
            coordinator_cn=probe.coordinator_cn,
            statement_id=probe.statement_id,
            probe_query_digest=probe.query_digest,
            started_at_utc=probe.started_at_utc,
            finished_at_utc=probe.finished_at_utc,
            fault_target_cn=self.deployment.fault_target_cn,
            fault_endpoint=self.deployment.fault_endpoint,
            observations=probe.remote_observations,
            source_artifacts=probe.source_artifacts,
        )
        record = {
            "probe_kind": kind,
            "statement_id": probe.statement_id,
            "query_digest": probe.query_digest,
            "started_at_utc": probe.started_at_utc,
            "finished_at_utc": probe.finished_at_utc,
            "duration_seconds": probe.duration_seconds,
            "outcome": probe.outcome,
            "consistency_ok": probe.consistency_ok,
            "error_class": probe.error_class,
            "remote_execution_witness": witness,
            "remote_execution_witness_digest": canonical_digest(witness),
        }
        self.probes.append(record)
        return record

    def _validate_probe_outcome(self, probe: ProbeResult) -> None:
        if probe.outcome == "success":
            if type(probe.consistency_ok) is not bool or probe.error_class is not None:
                raise ContractError("success probe outcome tuple is invalid")
            return
        if probe.outcome == "retriable_error":
            if (
                probe.consistency_ok is not None
                or type(probe.error_class) is not str
                or probe.error_class not in CONTRACT_ALLOWED_RETRIABLE_ERRORS
                or probe.error_class not in self.config.allowed_retriable_errors
            ):
                raise ContractError("retriable probe outcome tuple is invalid")
            return
        raise ContractError("probe outcome is outside the contract")

    def _probe_contract_passes(self, probe: dict[str, Any], *, final: bool) -> bool:
        if probe["remote_execution_witness"]["witness_state"] != "verified":
            return False
        if final:
            return (
                probe["outcome"] == "success"
                and probe["consistency_ok"] is True
                and probe["error_class"] is None
            )
        if probe["outcome"] == "success":
            return probe["consistency_ok"] is True and probe["error_class"] is None
        return (
            probe["outcome"] == "retriable_error"
            and probe["consistency_ok"] is None
            and probe["error_class"] in self.config.allowed_retriable_errors
        )

    def run(self) -> dict[str, Any]:
        raw_conclusion = "success"
        execution_state = "completed"
        stale_observed = False
        try:
            self.deployment = self.driver.preflight(self.deadline)
            self._validate_deployment(self.deployment)
            self._emit_partial("preflight")

            baseline = self._run_probe("baseline")
            self._emit_partial("baseline")
            if not self._probe_contract_passes(baseline, final=True):
                self.evidence_validity = (
                    "mismatch"
                    if baseline["remote_execution_witness"]["witness_state"] == "mismatch"
                    else "partial"
                )
                self.failure_reason = "baseline remote execution was not verified"
                raise CoverageNotExercised(self.failure_reason)

            self._require_remaining("fault injection")
            self.fault_receipt = self.driver.inject_fault(self.deadline)
            self._validate_fault_receipt(
                self.fault_receipt,
                baseline_finished_at_utc=baseline["finished_at_utc"],
            )
            self.timeline["fault_start"] = self.fault_receipt.started_at_utc
            self._emit_partial("fault_injected")

            while True:
                self._require_remaining("stale topology observation")
                topology = self.driver.observe_topology(self.deadline)
                self._validate_topology(topology)
                if topology.replacement_ready and self.timeline["replacement_ready_at"] == "unknown":
                    self.timeline["replacement_ready_at"] = topology.observed_at_utc
                if topology.replacement_ready and topology.old_member_visible:
                    stale_observed = True
                    self.timeline["stale_window_observed_at"] = topology.observed_at_utc
                    self.exercise_state = "exercised"
                    stale = self._run_probe("stale_window")
                    self.timeline["stale_probe_started_at"] = stale["started_at_utc"]
                    self.timeline["stale_probe_finished_at"] = stale["finished_at_utc"]
                    self._emit_partial("stale_probe")
                    break
                if not topology.old_member_visible:
                    self.timeline["old_member_evicted_at"] = topology.observed_at_utc
                    break
                self.driver.wait(
                    min(self.config.poll_interval_seconds, self._require_remaining("stale wait")),
                    self.deadline,
                )

            if self.timeline["old_member_evicted_at"] == "unknown":
                while True:
                    self._require_remaining("old member eviction")
                    topology = self.driver.observe_topology(self.deadline)
                    self._validate_topology(topology)
                    if not topology.old_member_visible:
                        self.timeline["old_member_evicted_at"] = topology.observed_at_utc
                        break
                    self.driver.wait(
                        min(self.config.poll_interval_seconds, self._require_remaining("eviction wait")),
                        self.deadline,
                    )

            recovery = self._run_probe("post_eviction")
            self.timeline["recovery_probe_started_at"] = recovery["started_at_utc"]
            self.timeline["recovery_probe_finished_at"] = recovery["finished_at_utc"]
            self.timeline["recovery_frontier"] = recovery["finished_at_utc"]
            self._emit_partial("post_eviction_probe")

            all_witnesses_verified = all(
                item["remote_execution_witness"]["witness_state"] == "verified"
                for item in self.probes
            )
            stale = next(
                (item for item in self.probes if item["probe_kind"] == "stale_window"),
                None,
            )
            if not stale_observed or stale is None or not all_witnesses_verified:
                self.evidence_validity = "partial"
                self.product_result = "not_evaluated"
                self.exercise_state = "not_exercised"
                self.failure_reason = "stale window or remote scope was not exercised"
            elif not self._probe_contract_passes(stale, final=False):
                self.evidence_validity = "valid"
                self.product_result = "failed"
                self.failure_domain = "product"
                self.failure_reason = "stale-window probe violated the bounded recovery contract"
                raw_conclusion = "failure"
            elif not self._probe_contract_passes(recovery, final=True):
                self.evidence_validity = "valid"
                self.product_result = "failed"
                self.failure_domain = "product"
                self.failure_reason = "post-eviction consistency or recovery probe failed"
                raw_conclusion = "failure"
            else:
                self.evidence_validity = "valid"
                self.product_result = "passed"
                self.exercise_state = "exercised"
        except ContractError as exc:
            self.evidence_validity = "mismatch"
            self.product_result = "not_evaluated"
            self.failure_domain = "infra"
            self.failure_reason = _bounded_external_diagnostic("contract_error", exc)
            raw_conclusion = "failure"
            execution_state = "blocked" if self.fault_receipt is None else "completed"
        except CoverageNotExercised as exc:
            self.product_result = "not_evaluated"
            self.exercise_state = "not_exercised"
            self.failure_reason = _bounded_external_diagnostic(
                "coverage_not_exercised", exc
            )
        except HarnessFailure as exc:
            self.evidence_validity = "partial"
            self.product_result = "not_evaluated"
            self.failure_domain = "harness"
            self.failure_reason = _bounded_external_diagnostic("harness_failure", exc)
            raw_conclusion = "failure"
        except TimeoutError as exc:
            self.evidence_validity = "valid" if self.fault_receipt is not None else "partial"
            self.product_result = "failed" if self.fault_receipt is not None else "not_evaluated"
            self.failure_domain = "product" if self.fault_receipt is not None else "infra"
            self.failure_reason = _bounded_external_diagnostic("scenario_timeout", exc)
            raw_conclusion = "timed_out"
        except Exception as exc:
            self.evidence_validity = "partial"
            self.product_result = "not_evaluated"
            self.failure_domain = "harness"
            self.failure_reason = _bounded_external_diagnostic(
                "unexpected_harness_failure", exc
            )
            raw_conclusion = "failure"
        finally:
            try:
                raw_cleanup = self.driver.cleanup(self.deadline)
                self.cleanup_receipt = self._sanitize_cleanup(raw_cleanup)
                if self.driver.monotonic() > self.deadline:
                    self.evidence_validity = "infra_invalid"
                    self.product_result = "not_evaluated"
                    self.failure_domain = "infra"
                    self.failure_reason = "cleanup_deadline_exceeded"
                    raw_conclusion = "failure"
                elif not self.cleanup_receipt.ok:
                    self.evidence_validity = "infra_invalid"
                    self.product_result = "not_evaluated"
                    self.failure_domain = "infra"
                    self.failure_reason = _bounded_external_diagnostic(
                        "cleanup_failed", raw_cleanup.detail
                    )
                    raw_conclusion = "failure"
            except Exception as exc:  # cleanup must never hide the primary state
                self.evidence_validity = "infra_invalid"
                self.product_result = "not_evaluated"
                self.failure_domain = "infra"
                self.failure_reason = _bounded_external_diagnostic(
                    "cleanup_exception", exc
                )
                raw_conclusion = "failure"

        return self._result(raw_conclusion=raw_conclusion, execution_state=execution_state)

    def _result(self, *, raw_conclusion: str, execution_state: str) -> dict[str, Any]:
        cleanup = None
        if self.cleanup_receipt is not None:
            cleanup = {
                "ok": self.cleanup_receipt.ok,
                "finished_at_utc": self.cleanup_receipt.finished_at_utc,
                "detail": self.cleanup_receipt.detail,
            }
        result = {
            "format": "recovery-contract-result/v1",
            "target_ref": TARGET_REF,
            "test_contract_id": TEST_CONTRACT_ID,
            "attempt_id": self.attempt_id,
            "candidate_revision": self.config.candidate_revision,
            "cluster": {
                "cluster_uid": stable_identity(self.config.cluster_uid),
                "generation": self.config.generation,
                "expected_image_digest": self.config.expected_image_digest,
            },
            "execution_state": execution_state,
            "raw_conclusion": raw_conclusion,
            "evidence_validity": self.evidence_validity,
            "product_result": self.product_result,
            "failure_domain": self.failure_domain,
            "failure_reason": self.failure_reason,
            "oracle_observation": {"exercise_state": self.exercise_state},
            "timeline": dict(self.timeline),
            "probes": list(self.probes),
            "cleanup": cleanup,
        }
        result["content_digest"] = canonical_digest(result)
        return result
