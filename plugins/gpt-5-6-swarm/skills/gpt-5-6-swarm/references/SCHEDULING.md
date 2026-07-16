# Scheduling and bounded concurrency

Protocol reference set: `1.4.0`.

Throughput comes from launching everything that is safe and nothing that is
not. This policy bounds "safe" with observed state, host limits, and
reconciliation capacity - not optimism.

## The peak formula

At every scheduling step the coordinator computes:

```
peak = min(
  ready_lanes,          # nodes whose dependencies and joins are satisfied
  host_threads,         # host concurrency ceiling (see below)
  class_ceiling,        # PURE vs write-class ceiling from this table
  attention_budget      # what the coordinator can actually supervise
)
```

`host_threads` is the lowest verified limit exposed by configuration or the
live client, minus one slot reserved for coordinator/reconciliation work. The
documented `agents.max_threads` default is 6, but a surface may expose fewer
live slots; that lower value wins. When no authoritative live limit is
available, use the conservative defaults in `ROUTES.md` rather than deriving a
guarantee from configuration alone.

Class ceilings:

| Work | Default ceiling | Rationale |
| --- | --- | --- |
| `PURE` read-only lanes | 4 | matches the protocol's auto cap; raising it is an experiment, not a default |
| `ISOLATED` writers, disjoint scopes | 3 | review bandwidth for diffs is the binding constraint |
| Overlapping write scopes | 1 | the validator enforces serialization regardless |
| `NON_IDEMPOTENT` / `EXCLUSIVE_UNKNOWN` / `ONE_SHOT` | 1, serial, never overlapped | doctrine, now also enforced |

These defaults are deliberately conservative. No measurement yet justifies
shipping a higher `PURE` default; when a local evaluation baseline exists
(see the experimental path), raise it there first.

## Backpressure comes from observed state

- Any unresolved `UNKNOWN` freezes new non-`PURE` creation and launches
  until reconciled - the enforcement tool refuses them with exit 7.
  Reconciliation consumes the coordinator's attention; the freeze makes
  that cost explicit instead of letting ambiguity pile up.
- Two or more unresolved `UNKNOWN` nodes, or any drift-freeze event, halts
  *all* new launches including `PURE` until the run is reconciled. This
  threshold is protocol guidance; the single-`UNKNOWN` non-`PURE` freeze is
  enforced.
- A stalled lane is observed, never presumed dead: no replacement launch,
  no resource takeover, no heartbeat-expiry reclamation. Escalate to the
  user when a stall blocks the critical path.
- Count only enforcement-ledger states as truth. "The worker said done" is
  not a scheduling signal; a validated receipt is.

## Cost and duration awareness

Prefer fewer, better-briefed lanes over maximum fan-out. Fan out wide only
when lanes are short, independent, and individually checkable. Long-running
lanes (builds, suites) should be claimed early so their scope conflicts
surface before dependent work queues behind them. Luna lanes are a
lower-resource routing hypothesis, not a measured cost claim;
Terra/Sol lanes are not - a Sol adjudication node at `xhigh`/`max` costs
more than most whole recon fans, so budget it deliberately.

## Experimental path (gated, fail closed)

Raising the `PURE` ceiling above 4 (toward 6-8) is permitted only when all
of the following hold, and must be recorded in the run kickoff line:

1. the host's thread ceiling is verified at or above the requested peak + 1;
2. zero unresolved `UNKNOWN` nodes exist in the run;
3. a recorded evaluation baseline on this host shows the higher peak
   completes equivalent work without added `UNKNOWN`, drift, or gate
   failures;
4. the user asked for maximum throughput or approved the experiment.

Absent any one condition, the default ceiling applies. There is no
experimental path for write classes or the one-shot barrier.
