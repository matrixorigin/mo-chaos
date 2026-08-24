"""Offline replay CLI.  It never performs a live fault injection."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .campaign import evaluate_campaign
from .contract import canonical_json
from .replay import ReplayDriver
from .scenario import BoundedRecoveryScenario, ScenarioConfig


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical_json(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _config(value: dict[str, Any]) -> ScenarioConfig:
    return ScenarioConfig(
        candidate_revision=value["candidate_revision"],
        cluster_uid=value["cluster_uid"],
        generation=value["generation"],
        expected_image_digest=value["expected_image_digest"],
        scenario_budget_seconds=float(value.get("scenario_budget_seconds", 1800)),
        probe_budget_seconds=float(value.get("probe_budget_seconds", 30)),
        poll_interval_seconds=float(value.get("poll_interval_seconds", 1)),
    )


def replay(args: argparse.Namespace) -> int:
    fixture = _load(args.fixture)
    partial_path = args.output.with_name("partial-recovery-contract-result.json")
    scenario = BoundedRecoveryScenario(
        _config(fixture["config"]),
        ReplayDriver(fixture),
        attempt_id=fixture["attempt_id"],
        on_partial=lambda value: _write_atomic(partial_path, value),
    )
    result = scenario.run()
    _write_atomic(args.output, result)
    return 0 if result["product_result"] == "passed" and result["evidence_validity"] == "valid" else 1


def campaign(args: argparse.Namespace) -> int:
    attempts = [_load(path) for path in args.attempt]
    result = evaluate_campaign(attempts)
    _write_atomic(args.output, result)
    return 0 if result["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay_parser = subparsers.add_parser("replay", help="replay one offline fixture")
    replay_parser.add_argument("--fixture", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path, required=True)
    replay_parser.set_defaults(handler=replay)

    campaign_parser = subparsers.add_parser("campaign", help="verify a fixed offline campaign")
    campaign_parser.add_argument("--attempt", type=Path, action="append", required=True)
    campaign_parser.add_argument("--output", type=Path, required=True)
    campaign_parser.set_defaults(handler=campaign)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (KeyError, TypeError, ValueError) as exc:
        print(f"bounded recovery contract error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
