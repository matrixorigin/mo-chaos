"""Offline acceptance gate for the required 20-attempt fault campaign."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .contract import ContractError, canonical_digest
from .scenario import TARGET_REF, TEST_CONTRACT_ID

CAMPAIGN_ATTEMPTS = 20


def evaluate_campaign(attempts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(attempts)
    if len(items) != CAMPAIGN_ATTEMPTS:
        raise ContractError(
            f"campaign requires exactly {CAMPAIGN_ATTEMPTS} attempts, got {len(items)}"
        )
    attempt_ids = [item.get("attempt_id") for item in items]
    if any(not value for value in attempt_ids) or len(set(attempt_ids)) != len(attempt_ids):
        raise ContractError("campaign attempt IDs must be present and unique")
    for item in items:
        if item.get("format") != "recovery-contract-result/v1":
            raise ContractError("campaign contains an unknown attempt format")
        if item.get("target_ref") != TARGET_REF or item.get("test_contract_id") != TEST_CONTRACT_ID:
            raise ContractError("campaign contains an attempt for another contract")
        claimed_digest = item.get("content_digest")
        rebuilt = dict(item)
        rebuilt.pop("content_digest", None)
        if claimed_digest != canonical_digest(rebuilt):
            raise ContractError("campaign attempt content digest mismatch")

    candidate_revisions = {item.get("candidate_revision") for item in items}
    cluster_generations = {
        (
            item.get("cluster", {}).get("cluster_uid"),
            item.get("cluster", {}).get("generation"),
            item.get("cluster", {}).get("expected_image_digest"),
        )
        for item in items
    }
    if len(candidate_revisions) != 1 or len(cluster_generations) != 1:
        raise ContractError("campaign attempts are not comparable")

    product_counts = Counter(item.get("product_result") for item in items)
    validity_counts = Counter(item.get("evidence_validity") for item in items)
    stale_exercised = 0
    all_witnesses_verified = 0
    cleanup_ok = 0
    for item in items:
        probe_by_kind = {value["probe_kind"]: value for value in item.get("probes", [])}
        stale = probe_by_kind.get("stale_window")
        if (
            item.get("oracle_observation", {}).get("exercise_state") == "exercised"
            and stale is not None
        ):
            stale_exercised += 1
        if set(probe_by_kind) == {"baseline", "stale_window", "post_eviction"} and all(
            value.get("remote_execution_witness", {}).get("witness_state") == "verified"
            for value in probe_by_kind.values()
        ):
            all_witnesses_verified += 1
        if item.get("cleanup", {}).get("ok") is True:
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
                "content_digest": item.get("content_digest", canonical_digest(item)),
            }
            for item in items
        ],
        "passed": passed,
    }
    result["content_digest"] = canonical_digest(result)
    return result
