# Engineering case study: v0.2 audit hardening

Status: `REPRODUCED FROM TAGGED SOURCE`

Type: engineering outcome, **not a performance benchmark**

The v0.2 release converted an external audit into executable claim boundaries,
reference-set compatibility checks, capability truth, Git drift evidence,
one-shot fence gating, and an 85% coverage gate across the supported CI matrix.

Evidence is in the [v0.2 changelog](../CHANGELOG.md#protocol-120--schema-1--tool-020---audit-hardening-2026-07-15),
[invariant map](../docs/INVARIANTS.md), and repository history. The outcome was
quality and falsifiability: claims became categorized as code-enforced,
skill-instructed, host-gated, or externally fenced.

What was not measured: serial time, Swarm time, token usage, credits, observed
concurrency, or break-even. This case supports no speedup claim.
