"""Canonical evidence helpers for the bounded multi-CN recovery scenario.

Raw endpoints and Kubernetes identities are deliberately accepted only at the
scenario boundary.  Evidence produced by this module contains stable digests,
never those raw values.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
PROBE_KINDS = ("baseline", "stale_window", "post_eviction")
SOURCE_KINDS = ("query_trace", "runtime_trace", "bounded_cn_log")
SOURCE_KIND_SUFFIX = {
    "query_trace": "query-trace.json",
    "runtime_trace": "runtime-trace.json",
    "bounded_cn_log": "cn-log.json",
}
SOURCE_ARTIFACT_NAMES = {
    kind: {
        f"{kind}-query-trace.json",
        f"{kind}-runtime-trace.json",
        f"{kind}-cn-log.json",
    }
    for kind in PROBE_KINDS
}


class ContractError(ValueError):
    """Raised when evidence cannot satisfy the versioned contract."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def content_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def query_digest(sql: str) -> str:
    # The scenario owns the exact query text.  Do not normalize it with the
    # product parser or the independent oracle could drift with the product.
    return content_digest(sql.encode("utf-8"))


def stable_identity(value: str) -> str:
    if not value:
        raise ContractError("stable identity source is empty")
    return content_digest(value.encode("utf-8"))


def require_revision(value: str) -> str:
    if not REVISION_RE.fullmatch(value):
        raise ContractError("candidate_revision must be a lowercase 40-character SHA")
    return value


def require_digest(value: str, field: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ContractError(f"{field} must be a sha256 digest")
    return value


def parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ContractError("timestamps must use RFC3339 UTC with a Z suffix")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo != timezone.utc:
        raise ContractError("timestamp is not UTC")
    return parsed


@dataclass(frozen=True)
class SourceArtifact:
    logical_name: str
    content: bytes


@dataclass(frozen=True)
class RemoteScopeObservation:
    probe_kind: str
    statement_id: str
    query_digest: str
    coordinator_cn: str
    remote_cn: str
    endpoint: str
    first_observed_at_utc: str
    last_observed_at_utc: str
    source_kind: str


def build_remote_execution_witness(
    *,
    probe_kind: str,
    candidate_revision: str,
    cluster_uid: str,
    generation: str,
    coordinator_cn: str,
    statement_id: str,
    probe_query_digest: str,
    started_at_utc: str,
    finished_at_utc: str,
    fault_target_cn: str,
    fault_endpoint: str,
    observations: Iterable[RemoteScopeObservation],
    source_artifacts: Iterable[SourceArtifact],
    max_remote_scopes: int = 16,
    max_source_artifacts: int = 8,
    max_source_artifact_bytes: int = 1 << 20,
) -> dict[str, Any]:
    if probe_kind not in PROBE_KINDS:
        raise ContractError(f"unsupported probe_kind: {probe_kind}")
    require_revision(candidate_revision)
    require_digest(probe_query_digest, "query_digest")
    if not statement_id:
        raise ContractError("statement_id is empty")
    started = parse_utc(started_at_utc)
    finished = parse_utc(finished_at_utc)
    if finished < started:
        raise ContractError("probe finished before it started")

    observed = list(observations)
    artifacts = list(source_artifacts)
    if len(observed) > max_remote_scopes:
        raise ContractError("remote scope count exceeds the contract limit")
    if len(artifacts) > max_source_artifacts:
        raise ContractError("source artifact count exceeds the contract limit")
    if sum(len(item.content) for item in artifacts) > max_source_artifact_bytes:
        raise ContractError("source artifact bytes exceed the contract limit")
    artifact_names = [item.logical_name for item in artifacts]
    if len(set(artifact_names)) != len(artifact_names):
        raise ContractError("source artifact logical names must be unique")
    if any(name not in SOURCE_ARTIFACT_NAMES[probe_kind] for name in artifact_names):
        raise ContractError("source artifact logical name is not allowlisted")
    raw_endpoint_bytes = [
        value.encode("utf-8")
        for value in (fault_endpoint, *(item.endpoint for item in observed))
        if value
    ]
    if any(raw in artifact.content for raw in raw_endpoint_bytes for artifact in artifacts):
        raise ContractError("source artifact contains a raw endpoint")
    statement_bytes = statement_id.encode("utf-8")
    query_digest_bytes = probe_query_digest.encode("utf-8")
    linked_source_kinds = {
        source_kind
        for source_kind, suffix in SOURCE_KIND_SUFFIX.items()
        if any(
            artifact.logical_name == f"{probe_kind}-{suffix}"
            and statement_bytes in artifact.content
            and query_digest_bytes in artifact.content
            for artifact in artifacts
        )
    }

    mismatch = False
    remote_scopes: list[dict[str, str]] = []
    for item in observed:
        first = parse_utc(item.first_observed_at_utc)
        last = parse_utc(item.last_observed_at_utc)
        identity_matches = (
            item.probe_kind == probe_kind
            and item.statement_id == statement_id
            and item.query_digest == probe_query_digest
            and item.coordinator_cn == coordinator_cn
        )
        window_matches = started <= first <= last <= finished
        remote_matches = bool(item.remote_cn) and item.remote_cn != coordinator_cn
        source_matches = item.source_kind in SOURCE_KINDS
        if not (identity_matches and window_matches and remote_matches and source_matches):
            mismatch = True
            continue
        remote_scopes.append(
            {
                "remote_cn": stable_identity(item.remote_cn),
                "endpoint_digest": stable_identity(item.endpoint),
                "first_observed_at_utc": item.first_observed_at_utc,
                "last_observed_at_utc": item.last_observed_at_utc,
                "source_kind": item.source_kind,
            }
        )

    if mismatch:
        witness_state = "mismatch"
    elif remote_scopes and any(
        item["source_kind"] in linked_source_kinds for item in remote_scopes
    ):
        witness_state = "verified"
    elif remote_scopes and artifacts:
        witness_state = "mismatch"
    else:
        witness_state = "not_observed"

    remote_scopes.sort(
        key=lambda item: (
            item["remote_cn"],
            item["endpoint_digest"],
            item["first_observed_at_utc"],
            item["last_observed_at_utc"],
            item["source_kind"],
        )
    )
    artifacts.sort(key=lambda item: item.logical_name)

    result = {
        "format": "remote_execution_witness/v1",
        "probe_kind": probe_kind,
        "candidate_revision": candidate_revision,
        "cluster": {
            "cluster_uid": stable_identity(cluster_uid),
            "generation": generation,
            "coordinator_cn": stable_identity(coordinator_cn),
        },
        "query": {
            "statement_id": statement_id,
            "query_digest": probe_query_digest,
            "started_at_utc": started_at_utc,
            "finished_at_utc": finished_at_utc,
        },
        "fault_target": {
            "cn": stable_identity(fault_target_cn),
            "endpoint_digest": stable_identity(fault_endpoint),
        },
        "remote_scopes": remote_scopes,
        "witness_state": witness_state,
        "source_artifacts": [
            {
                "logical_name": artifact.logical_name,
                "digest": content_digest(artifact.content),
            }
            for artifact in artifacts
        ],
    }
    # This assertion also protects future edits from accidentally serializing
    # a raw endpoint through a newly added field.
    encoded = canonical_json(result).decode("utf-8")
    for raw in (fault_endpoint, *(item.endpoint for item in observed)):
        if raw and raw in encoded:
            raise ContractError("remote witness leaked a raw endpoint")
    return result
