# Benchmarking without fooling ourselves

Swarm now ships an offline evidence format and comparison tool. It does **not**
ship a headline speed claim. The current Codex host does not expose authoritative
monotonic child timing or live-thread telemetry to this repository, so those
facts must arrive as independently captured receipts or remain `UNKNOWN`.

The files under `examples/benchmark-*.example.json` are sanitized format
examples with illustrative numbers. They are not measurements and must never be
cited as performance evidence.

## Primary and secondary tracks

The primary track is `fixed_graph_scheduling`: run the same frozen task, graph,
prompts, requested models/efforts, worker ceiling, gate, host profile, and cache
policy twice. The serial arm sets `requested_parallel=1`; the Swarm arm sets it
to the preregistered safe peak. This isolates scheduling better than comparing
one coordinator with a multi-model workflow.

The secondary `end_to_end_workflow` track compares a normal serial coordinator
with a normal Swarm route. It is useful product evidence, but decomposition,
model count, tokens, and routing are confounds and must be named.

## Preregister before measuring

Freeze the case before the first measured run. Record immutable hashes for the
skill, prompt, fixture, graph, evaluator, runtime, and base revision. Predeclare
the number of warmups, measured pairs, AB/BA order seed, cache/network policy,
primary metric, and permitted exclusions. Publish every preregistered case,
including tiny negative controls where parallel overhead should lose.

Pair the arms closely in time and restore the same fixture for every arm. The
deterministic acceptance gate runs outside worker ownership. If scoring needs
human judgment, blind the reviewer to the arm.

## Evidence rules

- Duration uses an authoritative monotonic host timing receipt in integer
  nanoseconds. Ledger timestamps are UTC wall clock and are not benchmark timers.
- `scheduler_issued_peak` is the coordinator's issued peak. It is not observed
  concurrency.
- `observed_peak` stays `null` unless authoritative active-thread telemetry and
  its SHA-256 receipt are present.
- Missing tokens, credits, or cost stay `null`, never zero.
- Failed, canceled, aborted, timed-out, excluded, and `UNKNOWN` trials stay in
  the report. Do not compute speed from them, but do not hide them.
- The fixed-graph comparison rejects mismatched case, host, gate, routing,
  treatment, or evidence identity.

Per-pair speedup is `serial_duration_ns / swarm_duration_ns`. The summary uses
the median of paired ratios, not the ratio of aggregate means. It also reports
paired savings, win/tie/loss counts, arm pass and `UNKNOWN` counts, issued-peak
evidence, observed-peak coverage, and usage coverage.

Break-even is specific to one case family, host, and date. A supported claim
needs a preregistered scale series, enough valid pairs, no worse quality, and an
uncertainty interval wholly above `1.0`. The shipped tool deliberately reports
`insufficient_evidence`; uncertainty analysis and a trustworthy host acquisition
adapter remain future release gates.

## Offline commands

```sh
T=plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/scripts/swarm_benchmark.py
python3 "$T" validate-case examples/benchmark-case.example.json
python3 "$T" plan examples/benchmark-case.example.json
python3 "$T" validate-trial examples/benchmark-case.example.json \
  examples/benchmark-serial-trial.example.json
python3 "$T" compare examples/benchmark-case.example.json \
  examples/benchmark-serial-trial.example.json \
  examples/benchmark-swarm-trial.example.json --format markdown
```

The schemas are informational mirrors of the executable validator:
[case](../schema/benchmark-case.schema.json),
[trial](../schema/benchmark-trial.schema.json), and
[report](../schema/benchmark-report.schema.json).

## Publication checklist

Use the [case-study template](../case-studies/CASE_STUDY_TEMPLATE.md). Name the
track, host, date, and valid/total pair count in the first sentence. Publish
trial-by-trial failures and exclusions, requested/issued/observed concurrency as
three separate fields, raw evidence hashes, exact reproduction commands,
confounds, and a narrow conclusion. Never generalize from one host or case.
