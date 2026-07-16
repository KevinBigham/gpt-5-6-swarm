# Benchmarking without fooling ourselves

Swarm now ships an offline declared-record format and anti-selection
diagnostic. It does **not** ship a headline speed claim or authenticate source
evidence. The current Codex host does not expose authoritative monotonic child
timing or live-thread telemetry to this repository, so those facts must arrive
as independently captured and independently verified receipts or remain
`UNKNOWN`.

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

The validator derives the exact planned `(pair_id, replicate, order, warmup)`
map from the frozen case. Unplanned pairs, changed order/warmup flags, duplicate
timing or gate evidence identities, and exclusion text not present in the
preregistered allowlist are rejected.

## Evidence rules

- Duration uses an authoritative monotonic host timing receipt in integer
  nanoseconds. Ledger timestamps are UTC wall clock and are not benchmark
  timers. The offline tool checks the declared receipt hash syntax and
  uniqueness, not the receipt's source bytes or authority.
- `scheduler_issued_peak` is the coordinator's issued peak. It is not observed
  concurrency.
- `observed_peak` stays `null` unless authoritative active-thread telemetry and
  its SHA-256 receipt are present.
- Missing tokens, credits, or cost stay `null`, never zero.
- Failed, canceled, aborted, timed-out, excluded, and `UNKNOWN` trials stay in
  the report. Do not compute speed from them, but do not hide them.
- The fixed-graph comparison rejects mismatched case, host, gate, routing,
  treatment, or evidence identity.

Per-pair declared-data ratio is `serial_duration_ns / swarm_duration_ns`. The
diagnostic emits a median only when every preregistered measured pair has two
successful, gate-passing, non-excluded arms. Missing, failed, `UNKNOWN`, or
excluded measured pairs withhold the median instead of silently shrinking the
sample. The report retains per-arm failure status and evidence identities, and
separately reports declared observed-peak, token, and credit coverage.

Every report is stamped `declared_hashes_unverified`. The tool does not fetch,
recompute, authenticate, or semantically bind the source ledger, journal,
doctor, gate, timing, usage, or telemetry bytes. Independently verify and
publish those bytes before citing a result; comparator output alone is not
empirical evidence.

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

The Draft 2020-12 schemas validate the shipped examples and encode expressible
shape/cross-field constraints in the development CI job:
[case](../schema/benchmark-case.schema.json),
[trial](../schema/benchmark-trial.schema.json), and
[report](../schema/benchmark-report.schema.json). The executable validator
remains authoritative for pair-plan binding, evidence-identity uniqueness, and
cross-record comparisons that JSON Schema cannot express.

## Publication checklist

Use the [case-study template](../case-studies/CASE_STUDY_TEMPLATE.md). Name the
track, host, date, and valid/total pair count in the first sentence. Publish
trial-by-trial failures and exclusions, requested/issued/observed concurrency as
three separate fields, raw evidence hashes, exact reproduction commands,
confounds, independent source-byte verification, and a narrow conclusion.
Never generalize from one host or case or cite the declared-data diagnostic by
itself as measured evidence.
