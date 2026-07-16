# Engineering case study: v0.3 evidence binding

Status: `REPRODUCED FROM TAGGED SOURCE`

Type: engineering outcome, **not a performance benchmark**

The v0.3 release bound path-scoped success to coordinator-recomputed bytes and
added task-bound one-shot authority records, write-ahead recovery, ignored-file
drift hashing, path-rebinding defenses, and a conservative `doctor` report.

Evidence is in the [v0.3 changelog](../CHANGELOG.md#protocol-130--schema-2--tool-030---gen-2-evidence-binding-2026-07-16),
[enforcement contract](../plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/ENFORCEMENT.md),
and [v0.3.0 release](https://github.com/KevinBigham/gpt-5-6-swarm/releases/tag/v0.3.0).
The outcome was a stronger distinction between a worker claim, verified local
bytes, host truth, and externally fenced effects.

What was not measured: paired duration, usage, cost, authoritative active-thread
telemetry, or break-even. This case supports no speedup claim.
