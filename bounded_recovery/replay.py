"""Deterministic driver for offline scenario and campaign verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .contract import ContractError, RemoteScopeObservation, SourceArtifact
from .scenario import (
    CleanupReceipt,
    DeploymentObservation,
    FaultReceipt,
    HarnessFailure,
    ProbeResult,
    TopologyObservation,
)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_json_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ContractError(f"{field} must be a JSON boolean")
    return value


@dataclass
class ReplayDriver:
    fixture: dict[str, Any]

    def __post_init__(self) -> None:
        self._time = 0.0
        self._start = _utc(self.fixture["started_at_utc"])
        self._topology_index = 0
        self.cleanup_calls = 0

    def monotonic(self) -> float:
        return self._time

    def utc_now(self) -> str:
        value = self._start + timedelta(seconds=self._time)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

    def preflight(self, deadline: float) -> DeploymentObservation:
        del deadline
        item = self.fixture["deployment"]
        self._time += float(item.get("duration_seconds", 0))
        return DeploymentObservation(
            cluster_uid=item["cluster_uid"],
            generation=item["generation"],
            image_digests=tuple(item["image_digests"]),
            fault_target_cn=item["fault_target_cn"],
            fault_endpoint=item["fault_endpoint"],
        )

    def run_probe(self, kind: str, timeout_seconds: float) -> ProbeResult:
        item = self.fixture["probes"].get(kind)
        if item is None:
            raise HarnessFailure(f"fixture has no {kind} probe")
        duration = float(item["duration_seconds"])
        if item.get("raise"):
            raise HarnessFailure(item["raise"])
        if duration > timeout_seconds:
            self._time += timeout_seconds
            raise TimeoutError(f"{kind} replay exceeded probe budget")
        started = self.utc_now()
        self._time += duration
        finished = self.utc_now()
        observations = tuple(
            RemoteScopeObservation(
                probe_kind=value.get("probe_kind", kind),
                statement_id=value.get("statement_id", item["statement_id"]),
                query_digest=value.get("query_digest", item["query_digest"]),
                coordinator_cn=value.get("coordinator_cn", item["coordinator_cn"]),
                remote_cn=value["remote_cn"],
                endpoint=value["endpoint"],
                first_observed_at_utc=value.get("first_observed_at_utc", started),
                last_observed_at_utc=value.get("last_observed_at_utc", finished),
                source_kind=value.get("source_kind", "query_trace"),
            )
            for value in item.get("remote_observations", [])
        )
        artifacts = tuple(
            SourceArtifact(value["logical_name"], value["content"].encode("utf-8"))
            for value in item.get("source_artifacts", [])
        )
        return ProbeResult(
            kind=kind,
            statement_id=item["statement_id"],
            query_digest=item["query_digest"],
            started_at_utc=started,
            finished_at_utc=finished,
            coordinator_cn=item["coordinator_cn"],
            outcome=item["outcome"],
            duration_seconds=duration,
            consistency_ok=item.get("consistency_ok"),
            error_class=item.get("error_class"),
            remote_observations=observations,
            source_artifacts=artifacts,
        )

    def inject_fault(self, deadline: float) -> FaultReceipt:
        del deadline
        item = self.fixture["fault"]
        self._time += float(item.get("duration_seconds", 0))
        return FaultReceipt(
            target_cn=item["target_cn"],
            endpoint=item["endpoint"],
            started_at_utc=self.utc_now(),
        )

    def observe_topology(self, deadline: float) -> TopologyObservation:
        del deadline
        items = self.fixture["topology"]
        if self._topology_index >= len(items):
            raise HarnessFailure("topology replay was exhausted")
        item = items[self._topology_index]
        self._topology_index += 1
        self._time = max(self._time, float(item["at_seconds"]))
        return TopologyObservation(
            observed_at_utc=self.utc_now(),
            replacement_ready=_require_json_bool(
                item["replacement_ready"], "topology.replacement_ready"
            ),
            old_member_visible=_require_json_bool(
                item["old_member_visible"], "topology.old_member_visible"
            ),
        )

    def wait(self, seconds: float, deadline: float) -> None:
        if self._time + seconds > deadline:
            self._time = deadline
        else:
            self._time += seconds

    def cleanup(self, deadline: float) -> CleanupReceipt:
        del deadline
        self.cleanup_calls += 1
        item = self.fixture.get("cleanup", {"ok": True})
        self._time += float(item.get("duration_seconds", 0))
        return CleanupReceipt(
            ok=_require_json_bool(item["ok"], "cleanup.ok"),
            finished_at_utc=self.utc_now(),
            detail=item.get("detail", ""),
        )
