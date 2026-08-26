"""Offline acceptance gate for the required 20-attempt fault campaign."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from .contract import (
    PROBE_KINDS,
    SOURCE_ARTIFACT_NAMES,
    SOURCE_KINDS,
    SOURCE_KIND_SUFFIX,
    ContractError,
    canonical_digest,
    parse_utc,
    require_digest,
    require_revision,
)
from .scenario import (
    CONTRACT_ALLOWED_RETRIABLE_ERRORS,
    TARGET_REF,
    TEST_CONTRACT_ID,
)

CAMPAIGN_ATTEMPTS = 20
ATTEMPT_FIELDS = {
    "format",
    "target_ref",
    "test_contract_id",
    "attempt_id",
    "candidate_revision",
    "cluster",
    "execution_state",
    "raw_conclusion",
    "evidence_validity",
    "product_result",
    "failure_domain",
    "failure_reason",
    "oracle_observation",
    "timeline",
    "probes",
    "cleanup",
    "content_digest",
}
TIMELINE_SENTINELS = {
    "fault_start": "not_started",
    "replacement_ready_at": "unknown",
    "stale_window_observed_at": "unknown",
    "old_member_evicted_at": "unknown",
    "stale_probe_started_at": "not_exercised",
    "stale_probe_finished_at": "not_exercised",
    "recovery_probe_started_at": "not_started",
    "recovery_probe_finished_at": "not_started",
    "recovery_frontier": "unknown",
}
PROBE_FIELDS = {
    "probe_kind",
    "statement_id",
    "query_digest",
    "started_at_utc",
    "finished_at_utc",
    "duration_seconds",
    "outcome",
    "consistency_ok",
    "error_class",
    "remote_execution_witness",
    "remote_execution_witness_digest",
}
WITNESS_FIELDS = {
    "format",
    "probe_kind",
    "candidate_revision",
    "cluster",
    "query",
    "fault_target",
    "remote_scopes",
    "witness_state",
    "source_artifacts",
}
ALLOWED_PROBE_SEQUENCES = {
    (),
    ("baseline",),
    ("baseline", "stale_window"),
    ("baseline", "post_eviction"),
    tuple(PROBE_KINDS),
}
CLEANUP_DETAIL_RE = re.compile(
    r"^cleanup_detail; diagnostic_digest=sha256:[0-9a-f]{64}; "
    r"source_truncated=(?:true|false)$"
)


def _record(value: Any, fields: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != fields:
        raise ContractError(f"{path} schema mismatch")
    return value


def _array(value: Any, limit: int, path: str) -> list[Any]:
    if type(value) is not list or len(value) > limit:
        raise ContractError(f"{path} must be a bounded array")
    return value


def _text(value: Any, path: str, limit: int = 4096) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise ContractError(f"{path} must be a non-empty bounded string")
    return value


def _sha(value: Any, path: str) -> str:
    return require_digest(_text(value, path, 71), path)


def _utc(value: Any, path: str) -> datetime:
    return parse_utc(_text(value, path, 40))


def _choice(value: Any, allowed: set[str], path: str) -> str:
    if type(value) is not str or value not in allowed:
        raise ContractError(f"{path} is outside the contract")
    return value


def _validate_timeline(value: Any) -> tuple[dict[str, Any], dict[str, datetime]]:
    timeline = _record(
        value, set(TIMELINE_SENTINELS) | {"scenario_deadline"}, "attempt.timeline"
    )
    deadline = _utc(timeline["scenario_deadline"], "attempt.timeline.scenario_deadline")
    concrete = {"scenario_deadline": deadline}
    for field, sentinel in TIMELINE_SENTINELS.items():
        raw = _text(timeline[field], f"attempt.timeline.{field}", 40)
        if raw == sentinel:
            continue
        observed = _utc(raw, f"attempt.timeline.{field}")
        if observed > deadline:
            raise ContractError(f"attempt.timeline.{field} exceeds the deadline")
        concrete[field] = observed
    for started, finished in (
        ("stale_probe_started_at", "stale_probe_finished_at"),
        ("recovery_probe_started_at", "recovery_probe_finished_at"),
    ):
        if (started in concrete) != (finished in concrete):
            raise ContractError("attempt timeline probe bounds are incomplete")
        if started in concrete and concrete[finished] < concrete[started]:
            raise ContractError("attempt timeline probe finished before it started")
    if ("recovery_frontier" in concrete) != (
        "recovery_probe_finished_at" in concrete
    ):
        raise ContractError("attempt recovery frontier is incomplete")
    if (
        "recovery_frontier" in concrete
        and concrete["recovery_frontier"] != concrete["recovery_probe_finished_at"]
    ):
        raise ContractError("attempt recovery frontier does not match the probe")
    return timeline, concrete


def _validate_witness(
    witness_value: Any,
    *,
    revision: str,
    attempt_cluster: dict[str, Any],
    probe: dict[str, Any],
) -> None:
    witness = _record(witness_value, WITNESS_FIELDS, "attempt.probe.witness")
    if (
        witness["format"] != "remote_execution_witness/v1"
        or witness["probe_kind"] != probe["probe_kind"]
        or witness["candidate_revision"] != revision
    ):
        raise ContractError("attempt probe witness identity mismatch")

    cluster = _record(
        witness["cluster"],
        {"cluster_uid", "generation", "coordinator_cn"},
        "attempt.probe.witness.cluster",
    )
    if (
        cluster["cluster_uid"] != attempt_cluster["cluster_uid"]
        or cluster["generation"] != attempt_cluster["generation"]
    ):
        raise ContractError("attempt probe witness cluster mismatch")
    coordinator = _sha(cluster["coordinator_cn"], "attempt.probe.witness.coordinator")

    query = _record(
        witness["query"],
        {"statement_id", "query_digest", "started_at_utc", "finished_at_utc"},
        "attempt.probe.witness.query",
    )
    linked_fields = ("statement_id", "query_digest", "started_at_utc", "finished_at_utc")
    if any(query[field] != probe[field] for field in linked_fields):
        raise ContractError("attempt probe witness query mismatch")
    query_started = _utc(query["started_at_utc"], "attempt.probe.witness.query.start")
    query_finished = _utc(query["finished_at_utc"], "attempt.probe.witness.query.finish")

    fault_target = _record(
        witness["fault_target"],
        {"cn", "endpoint_digest"},
        "attempt.probe.witness.fault_target",
    )
    _sha(fault_target["cn"], "attempt.probe.witness.fault_target.cn")
    _sha(
        fault_target["endpoint_digest"],
        "attempt.probe.witness.fault_target.endpoint_digest",
    )

    source_kinds: set[str] = set()
    scopes = _array(witness["remote_scopes"], 16, "attempt.probe.witness.scopes")
    for scope_value in scopes:
        scope = _record(
            scope_value,
            {
                "remote_cn",
                "endpoint_digest",
                "first_observed_at_utc",
                "last_observed_at_utc",
                "source_kind",
            },
            "attempt.probe.witness.scope",
        )
        if _sha(scope["remote_cn"], "attempt.probe.witness.scope.remote_cn") == coordinator:
            raise ContractError("attempt probe witness scope is not remote")
        _sha(scope["endpoint_digest"], "attempt.probe.witness.scope.endpoint")
        first = _utc(scope["first_observed_at_utc"], "attempt.probe.witness.scope.first")
        last = _utc(scope["last_observed_at_utc"], "attempt.probe.witness.scope.last")
        if not query_started <= first <= last <= query_finished:
            raise ContractError("attempt probe witness scope is outside its query")
        source_kinds.add(
            _choice(scope["source_kind"], set(SOURCE_KINDS), "attempt.probe.witness.source")
        )

    artifact_names: set[str] = set()
    artifacts = _array(
        witness["source_artifacts"], 8, "attempt.probe.witness.source_artifacts"
    )
    for artifact_value in artifacts:
        artifact = _record(
            artifact_value,
            {"logical_name", "digest"},
            "attempt.probe.witness.source_artifact",
        )
        name = _text(artifact["logical_name"], "attempt.probe.witness.artifact", 128)
        if name in artifact_names or name not in SOURCE_ARTIFACT_NAMES[probe["probe_kind"]]:
            raise ContractError("attempt probe witness artifact is not allowlisted and unique")
        artifact_names.add(name)
        _sha(artifact["digest"], "attempt.probe.witness.artifact.digest")

    state = _choice(
        witness["witness_state"],
        {"verified", "mismatch", "not_observed"},
        "attempt.probe.witness.state",
    )
    linked_source = any(
        f"{probe['probe_kind']}-{SOURCE_KIND_SUFFIX[source_kind]}" in artifact_names
        for source_kind in source_kinds
    )
    if state == "verified" and (not scopes or not linked_source):
        raise ContractError("verified attempt probe witness is incomplete")


def _validate_probe(
    value: Any, revision: str, cluster: dict[str, Any], deadline: datetime
) -> dict[str, Any]:
    probe = _record(value, PROBE_FIELDS, "attempt.probe")
    _choice(probe["probe_kind"], set(PROBE_KINDS), "attempt.probe.kind")
    _text(probe["statement_id"], "attempt.probe.statement_id", 256)
    _sha(probe["query_digest"], "attempt.probe.query_digest")
    started = _utc(probe["started_at_utc"], "attempt.probe.start")
    finished = _utc(probe["finished_at_utc"], "attempt.probe.finish")
    if not started <= finished <= deadline:
        raise ContractError("attempt probe is outside the scenario deadline")
    duration = probe["duration_seconds"]
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        raise ContractError("attempt probe duration must be finite and non-negative")
    _text(probe["outcome"], "attempt.probe.outcome", 64)
    if type(probe["consistency_ok"]) is not bool and probe["consistency_ok"] is not None:
        raise ContractError("attempt probe consistency must be boolean or null")
    if probe["error_class"] is not None:
        _text(probe["error_class"], "attempt.probe.error_class", 256)
    _validate_witness(
        probe["remote_execution_witness"],
        revision=revision,
        attempt_cluster=cluster,
        probe=probe,
    )
    witness_digest = _sha(
        probe["remote_execution_witness_digest"], "attempt.probe.witness_digest"
    )
    if witness_digest != canonical_digest(probe["remote_execution_witness"]):
        raise ContractError("attempt probe witness digest mismatch")
    return probe


def validate_attempt_result(value: Any) -> None:
    """Fail closed unless value is one exact final state-machine result."""

    attempt = _record(value, ATTEMPT_FIELDS, "attempt")
    claimed_digest = _sha(attempt["content_digest"], "attempt.content_digest")
    unsigned = dict(attempt)
    unsigned.pop("content_digest")
    if (
        attempt["format"] != "recovery-contract-result/v1"
        or attempt["target_ref"] != TARGET_REF
        or attempt["test_contract_id"] != TEST_CONTRACT_ID
    ):
        raise ContractError("campaign contains an attempt for another contract")

    _text(attempt["attempt_id"], "attempt.attempt_id", 256)
    revision = require_revision(_text(attempt["candidate_revision"], "attempt.revision", 40))
    cluster = _record(
        attempt["cluster"],
        {"cluster_uid", "generation", "expected_image_digest"},
        "attempt.cluster",
    )
    _sha(cluster["cluster_uid"], "attempt.cluster.cluster_uid")
    _text(cluster["generation"], "attempt.cluster.generation", 256)
    _sha(cluster["expected_image_digest"], "attempt.cluster.image")

    execution = _choice(attempt["execution_state"], {"blocked", "completed"}, "attempt.execution")
    conclusion = _choice(
        attempt["raw_conclusion"], {"success", "failure", "timed_out"}, "attempt.conclusion"
    )
    validity = _choice(
        attempt["evidence_validity"],
        {"valid", "partial", "mismatch", "infra_invalid"},
        "attempt.validity",
    )
    product = _choice(
        attempt["product_result"],
        {"passed", "failed", "not_evaluated"},
        "attempt.product_result",
    )
    domain = attempt["failure_domain"]
    if domain is not None:
        domain = _choice(domain, {"product", "harness", "infra"}, "attempt.failure_domain")
    reason = attempt["failure_reason"]
    if reason is not None:
        _text(reason, "attempt.failure_reason", 512)
    oracle = _record(
        attempt["oracle_observation"], {"exercise_state"}, "attempt.oracle"
    )
    exercise = _choice(
        oracle["exercise_state"], {"exercised", "not_exercised"}, "attempt.exercise"
    )

    timeline, concrete = _validate_timeline(attempt["timeline"])
    deadline = concrete["scenario_deadline"]
    probes = [
        _validate_probe(probe, revision, cluster, deadline)
        for probe in _array(attempt["probes"], len(PROBE_KINDS), "attempt.probes")
    ]
    probe_kinds = tuple(probe["probe_kind"] for probe in probes)
    if probe_kinds not in ALLOWED_PROBE_SEQUENCES:
        raise ContractError("attempt probe sequence is outside the state machine")

    cleanup = attempt["cleanup"]
    cleanup_finished = None
    if cleanup is not None:
        cleanup = _record(
            cleanup, {"ok", "finished_at_utc", "detail"}, "attempt.cleanup"
        )
        if type(cleanup["ok"]) is not bool:
            raise ContractError("attempt.cleanup.ok must be a boolean")
        cleanup_finished = _utc(cleanup["finished_at_utc"], "attempt.cleanup.finish")
        if type(cleanup["detail"]) is not str or len(cleanup["detail"]) > 160:
            raise ContractError("attempt.cleanup.detail must be bounded")
        if cleanup["detail"] and not CLEANUP_DETAIL_RE.fullmatch(cleanup["detail"]):
            raise ContractError("attempt.cleanup.detail is not redacted")

    if product == "passed":
        if (
            (execution, conclusion, validity, domain, reason, exercise)
            != ("completed", "success", "valid", None, None, "exercised")
            or cleanup is None
            or cleanup["ok"] is not True
            or cleanup_finished is None
            or cleanup_finished > deadline
            or probe_kinds != tuple(PROBE_KINDS)
        ):
            raise ContractError("passed attempt result axes are inconsistent")
        by_kind = {probe["probe_kind"]: probe for probe in probes}
        baseline, stale, recovery = (by_kind[kind] for kind in PROBE_KINDS)
        if any(
            probe["remote_execution_witness"]["witness_state"] != "verified"
            for probe in probes
        ):
            raise ContractError("passed attempt has an unverified witness")
        boundary_probes_pass = all(
            probe["outcome"] == "success"
            and probe["consistency_ok"] is True
            and probe["error_class"] is None
            for probe in (baseline, recovery)
        )
        stale_passes = (
            stale["outcome"] == "success"
            and stale["consistency_ok"] is True
            and stale["error_class"] is None
        ) or (
            stale["outcome"] == "retriable_error"
            and stale["consistency_ok"] is None
            and stale["error_class"] in CONTRACT_ALLOWED_RETRIABLE_ERRORS
        )
        if not boundary_probes_pass or not stale_passes:
            raise ContractError("passed attempt probe outcomes are inconsistent")
        if not set(TIMELINE_SENTINELS).issubset(concrete):
            raise ContractError("passed attempt timeline is incomplete")
        if not (
            _utc(baseline["finished_at_utc"], "baseline.finish")
            <= concrete["fault_start"]
            <= concrete["replacement_ready_at"]
            <= concrete["stale_window_observed_at"]
            <= concrete["stale_probe_started_at"]
            <= concrete["stale_probe_finished_at"]
            <= concrete["old_member_evicted_at"]
            <= concrete["recovery_probe_started_at"]
            <= concrete["recovery_probe_finished_at"]
            <= cleanup_finished
            <= deadline
        ):
            raise ContractError("passed attempt timeline is not monotonic")
        if (
            timeline["stale_probe_started_at"] != stale["started_at_utc"]
            or timeline["stale_probe_finished_at"] != stale["finished_at_utc"]
            or timeline["recovery_probe_started_at"] != recovery["started_at_utc"]
            or timeline["recovery_probe_finished_at"] != recovery["finished_at_utc"]
            or timeline["recovery_frontier"] != recovery["finished_at_utc"]
        ):
            raise ContractError("passed attempt timeline does not match its probes")
    elif product == "failed":
        if (
            validity != "valid"
            or domain != "product"
            or conclusion not in {"failure", "timed_out"}
            or reason is None
        ):
            raise ContractError("failed attempt result axes are inconsistent")
    else:
        if validity == "valid" or reason is None:
            raise ContractError("not-evaluated attempt result axes are inconsistent")
        if validity == "infra_invalid" and domain != "infra":
            raise ContractError("infra-invalid attempt must have an infra failure")

    if claimed_digest != canonical_digest(unsigned):
        raise ContractError("campaign attempt content digest mismatch")


def evaluate_campaign(attempts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for item in attempts:
        items.append(item)
        if len(items) > CAMPAIGN_ATTEMPTS:
            break
    if len(items) != CAMPAIGN_ATTEMPTS:
        raise ContractError(
            f"campaign requires exactly {CAMPAIGN_ATTEMPTS} attempts, got {len(items)}"
        )
    for item in items:
        validate_attempt_result(item)
    attempt_ids = [item["attempt_id"] for item in items]
    if len(set(attempt_ids)) != len(attempt_ids):
        raise ContractError("campaign attempt IDs must be unique")

    candidate_revisions = {item["candidate_revision"] for item in items}
    cluster_generations = {
        (
            item["cluster"]["cluster_uid"],
            item["cluster"]["generation"],
            item["cluster"]["expected_image_digest"],
        )
        for item in items
    }
    if len(candidate_revisions) != 1 or len(cluster_generations) != 1:
        raise ContractError("campaign attempts are not comparable")

    product_counts = Counter(item["product_result"] for item in items)
    validity_counts = Counter(item["evidence_validity"] for item in items)
    stale_exercised = 0
    all_witnesses_verified = 0
    cleanup_ok = 0
    for item in items:
        probe_by_kind = {value["probe_kind"]: value for value in item["probes"]}
        if (
            item["oracle_observation"]["exercise_state"] == "exercised"
            and "stale_window" in probe_by_kind
        ):
            stale_exercised += 1
        if set(probe_by_kind) == set(PROBE_KINDS) and all(
            value["remote_execution_witness"]["witness_state"] == "verified"
            for value in probe_by_kind.values()
        ):
            all_witnesses_verified += 1
        if item["cleanup"] is not None and item["cleanup"]["ok"] is True:
            cleanup_ok += 1

    passed = (
        product_counts == Counter({"passed": CAMPAIGN_ATTEMPTS})
        and validity_counts == Counter({"valid": CAMPAIGN_ATTEMPTS})
        and stale_exercised == CAMPAIGN_ATTEMPTS
        and all_witnesses_verified == CAMPAIGN_ATTEMPTS
        and cleanup_ok == CAMPAIGN_ATTEMPTS
    )
    result = {
        "format": "bounded-recovery-campaign/v1",
        "expected_attempts": CAMPAIGN_ATTEMPTS,
        "observed_attempts": len(items),
        "candidate_revision": next(iter(candidate_revisions)),
        "cluster_contract": list(next(iter(cluster_generations))),
        "product_result_counts": dict(sorted(product_counts.items())),
        "evidence_validity_counts": dict(sorted(validity_counts.items())),
        "stale_window_exercised": stale_exercised,
        "all_probe_witnesses_verified": all_witnesses_verified,
        "cleanup_ok": cleanup_ok,
        "attempts": [
            {
                "attempt_id": item["attempt_id"],
                "content_digest": item["content_digest"],
            }
            for item in items
        ],
        "passed": passed,
    }
    result["content_digest"] = canonical_digest(result)
    return result
