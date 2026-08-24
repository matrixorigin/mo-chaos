# Bounded multi-CN recovery contract

This package owns the fail-closed state machine for
`target/chaos/multi-cn-bounded-recovery-v1`.  The current CLI is deliberately
offline-only: it can replay captured observations and verify the required
20-attempt campaign, but it cannot inject a live fault.

The state machine enforces these boundaries:

- deployment image digests, cluster identity, and generation are verified
  before any fault;
- baseline remote execution must be verified before fault injection;
- the first fault probe runs only in the intersection
  `replacement Ready && old member visible`;
- the second fault probe runs after old-member eviction;
- readiness, membership, both probes, and cleanup share one monotonic total
  deadline;
- each probe has a statement-bound `remote_execution_witness/v1`;
- raw endpoints are digested and rejected if they appear in a source artifact;
- a missing stale window or remote scope is `not_exercised/not_evaluated`, never
  a pass;
- checker/observer failure is a Harness failure; cleanup failure makes the
  attempt `infra_invalid`;
- a campaign passes only when exactly 20 comparable, untampered attempts are
  valid, exercised, cleanup-complete product passes.

Run local tests:

```bash
python3.11 -m unittest discover -s tests -v
```

Replay one fixture without touching Kubernetes:

```bash
python3.11 -m bounded_recovery.cli replay \
  --fixture /path/to/fixture.json \
  --output /path/to/recovery-contract-result.json
```

The live Kubernetes/MatrixOne driver is intentionally a separate adapter.  It
must use a fixed fault plan and typed inputs from the Nightly allowlist; this
offline CLI will never accept a command, executable path, or arbitrary Chaos
YAML.
