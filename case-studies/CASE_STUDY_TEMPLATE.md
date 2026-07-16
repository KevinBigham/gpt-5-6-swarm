# Case study: [case family and scale]

Status: `DRAFT | REPRODUCED | SUPERSEDED`

> In one sentence: name the track, host, date, valid/total pairs, acceptance
> result, and the narrow result. Do not generalize beyond this evidence.

## Preregistered case

| Field | Immutable value |
| --- | --- |
| Case / family / scale | |
| Case SHA-256 | |
| Base revision | |
| Skill / protocol / tool | |
| Prompt / fixture / graph SHA-256 | |
| Gate / evaluator SHA-256 | |
| Track | `fixed_graph_scheduling` or `end_to_end_workflow` |
| Source-evidence verification | verifier, method, and immutable result |

## Host and routing

Record the host profile hash, date, routing status (`pinned` or
`host_selected`), cache/network policy, quotas, and known platform drift.

## Pairing and acceptance

Record warmups, measured pairs, AB/BA seed, restore procedure, timeout,
deterministic gate or blinded review method, and preregistered exclusions.

## Results

| Arm | Total | Passed | Failed | UNKNOWN | Median duration | Tokens/credits coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Serial | | | | | | |
| Swarm | | | | | | |

| Pair | Order | Serial | Swarm | Speedup | Gate | Exclusion |
| --- | --- | ---: | ---: | ---: | --- | --- |
| | | | | | | |

Report requested parallel, scheduler-issued peak, and observed peak separately.
`observed_peak` is `UNKNOWN` without authoritative active-thread telemetry.

## Safety and quality

List retries, escalations, substitutions, integration conflicts, unresolved
ambiguity, safety pauses, escaped defects, cleanup evidence, and gate failures.

## Confounds and limits

Name model/backend drift, load, caching, quota, decomposition, routing, sample
size, open-fixture overfitting, and missing timing/usage/telemetry evidence.

## Raw evidence and reproduction

List hashes for every case, trial, ledger, journal, doctor report, gate log,
timing receipt, telemetry receipt, and artifact. Include exact offline validation
and comparison commands plus the independent procedure that fetched and
recomputed the named source bytes. A `declared_hashes_unverified` comparator
report is a diagnostic, not measured evidence.

## Narrow conclusion

State only what this case, scale, host, and date support. Mark break-even
`insufficient_evidence` unless a preregistered scale series and uncertainty
analysis support it.
