# GPT-5.6 Swarm

[![CI](https://github.com/KevinBigham/gpt-5-6-swarm/actions/workflows/ci.yml/badge.svg)](https://github.com/KevinBigham/gpt-5-6-swarm/actions/workflows/ci.yml)

**Parallel where independent. Relay where dependent.**

GPT-5.6 Swarm is a Codex skill that turns a task into a coordinator-owned dependency graph, runs eligible independent lanes in parallel, and reconciles their actual artifacts through explicit gates.

It is designed to make multi-chat work faster while deterministically enforcing consistency of the coordinator's recorded control plane. It prevents duplicate automatic dispatch in its own ledger and halts on ambiguity; it does not claim exactly-once external effects or locks against untracked writers.

This is an independent community derivative of [Forward Future's GPT-5.6 Relay](https://github.com/Forward-Future/gpt-5-6-relay). It is not affiliated with or endorsed by Forward Future, Matthew Berman, or OpenAI.

## What is enforced, instructed, and host-gated

| Layer | What Swarm provides | Boundary |
| --- | --- | --- |
| Enforced in code | Legal recorded transitions, task/nonce deduplication, task-bound one-shot authority records, one owner per node, declared/re-resolved path scopes, local artifact-byte verification, generation compare-and-set, WAL recovery, optional ignored-file drift evidence, mixed-reference refusal, and fail-closed `UNKNOWN` | Recorded consistency plus named local bytes at verification time; not proof of host behavior, commands, undeclared writes, operator identity, or external effects |
| Instructed by the skill | Graph design, semantic deduplication, resource classification, worktree isolation, artifact inspection, cancellation, integration, and reconciliation | Depends on coordinator/worker compliance and independent evidence |
| Host-gated | Child creation/collection, pinned model and effort routing, nonce discovery, turn inspection, cancel/interrupt, and live-slot telemetry | Missing capabilities narrow the route and are reported in the kickoff |
| Externally fenced | Shared Git state, databases, services, deployment targets, and one-shot effects | Requires real locks, transactions, generation tokens, fresh outputs, or target-side idempotency; Codex alone does not supply these |

This project is experimental orchestration infrastructure for personal use and invited testing. It is not a durable workflow engine like Temporal or Airflow.

## Host compatibility

- Minimum: a Codex environment that supports skills plus host-managed subagent
  creation, accounting, and result collection.
- Full model-specific routing additionally needs a spawn surface that can pin
  a child configuration (directly or through a selectable custom agent), plus
  access to GPT-5.6 Sol, Terra, and Luna. A custom-agent file existing on disk
  is not proof that the active spawn call can select it.
- Guarded retries and one-shot work additionally need unique-launch discovery,
  thread inspection, cancellation/accounting, and the relevant real resource
  fences.
- Deterministic recorded-control-plane enforcement needs Python 3.9+ and permission to write
  control-plane state under `.swarm/runs/` (or an explicitly selected root).

If the host lacks the minimum host-managed subagent controls, the skill reports the proposed graph and continues serially in the coordinator. If stronger cancellation, isolation, or resource-locking capabilities are unavailable, it narrows to read-only fan-out or serialized work instead of pretending full parallel safety.

## Install

This repository is a Codex marketplace with one canonical plugin skill. From a
local clone:

```sh
codex plugin marketplace add /absolute/path/to/gpt-5-6-swarm
codex plugin add gpt-5-6-swarm@gpt-5-6-swarm
```

After the v0.4.0 tag is actually published, a reproducible Git install is:

```sh
codex plugin marketplace add KevinBigham/gpt-5-6-swarm --ref v0.4.0
codex plugin add gpt-5-6-swarm@gpt-5-6-swarm
```

Start a new Codex task after installation. Also ensure the target repository
ignores `.swarm/runs/`; runtime ledgers and journals must never be committed.
`capture-baseline` excludes that exact path from its dirty-state digest even
before the target ignore file is updated. See the [plugin and profile guide](docs/PLUGIN.md).

Invoke it explicitly:

```text
Use $gpt-5-6-swarm to orchestrate: <task>
```

Optional conversational controls:

```text
Use $gpt-5-6-swarm in build mode with workers=6 and parallel=3: <task>
```

`workers` is the total worker-node budget. `parallel` is the peak simultaneous worker-node budget. Both exclude an optional proxy coordinator. They are ceilings, not quotas.

## What it adds

- A coordinator-owned DAG with `all`, `any`, and `quorum:N` joins.
- Bounded worker and peak-concurrency budgets.
- Host-gated model-aware routing across GPT-5.6 Sol, Terra, and Luna, with the
  actual pinned/host-selected status reported.
- Parallel snapshot-pinned research, independent review lenses, and disjoint worktree implementation.
- One ledger-exclusive integration owner; real exclusivity still requires an effective host/resource fence.
- Launch/authorization nonces and explicit transitions to prevent duplicate automatic recorded dispatch and misbound one-shot authority.
- Capability-based fallback to read-only fan-out or serialized work.
- Fail-closed handling for unknown writers, stalled workers, cancellation, external effects, and one-shot science.
- Evidence-bearing handoffs and actual—not planned—route receipts.
- An optional frozen-contract gate that binds exact node fields and ownership
  into the existing task fingerprint without changing ledger schema 2.
- An escaped, dependency-free `render-status` HTML view for operators.
- A preregistered paired-benchmark declared-record format, deterministic
  anti-selection diagnostic, and honest case-study publication template.
- Eight narrow project-scoped specialist profiles for contributors; the plugin
  format does not install or prove selection of those profiles.

The normative protocol is in [SKILL.md](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/SKILL.md). Its supporting references cover [concurrency and safety](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/CONCURRENCY.md), [route templates](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/ROUTES.md), [reporting](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/REPORTING.md), and [deployment](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/DEPLOYMENT.md).

## Deterministic enforcement evidence

The protocol's represented safety-critical control-plane invariants are enforced by a standard-library Python runtime tool, not just prose: a run-local ledger under `.swarm/runs/<run-id>/` (gitignored) with a legal-transition state machine, fingerprint deduplication, launch/arm/authorization nonce uniqueness, one-active-owner and re-resolved resource-scope rules, receipt-gated outcomes, local artifact-byte recomputation, generation compare-and-set, write-ahead recovery, protocol-reference compatibility, and fail-closed `UNKNOWN` handling.

```sh
python3 plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/scripts/swarm_ledger.py --help
python3 -m unittest discover -s tests   # offline, deterministic
python3 plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/scripts/swarm_ledger.py verify-reference-set
```

CI runs the offline suite across Python 3.9/3.11/3.13 on Ubuntu, Python 3.11 on
Windows and macOS, plus branch-aware coverage across all three runtime tools
and Draft 2020-12 schema/example validation. See [the invariant-to-test
map](docs/INVARIANTS.md), [operator runbook](docs/RUNBOOK.md), and [development
roadmap](docs/ROADMAP.md).

See [ENFORCEMENT.md](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/ENFORCEMENT.md) for the lifecycle, scope boundary, exit codes, recovery, and versioning contract; [SCHEDULING.md](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/SCHEDULING.md) for bounded concurrency; [HOSTS.md](plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/references/HOSTS.md) for verified host capabilities versus gated experiments. The prompt-only workflow remains available when command execution or permission for control-plane state writes is absent.

## Evidence status

Swarm publishes [engineering case studies](case-studies/README.md) and a
[paired benchmark methodology](docs/BENCHMARKING.md), but it does not yet claim
a general speedup or break-even point. Example durations are illustrative, not
measurements. `scheduler_issued_peak` is never relabeled as observed host
concurrency, and missing timing, token, credit, or telemetry values remain
`UNKNOWN`. The offline comparator validates declared hashes and preregistered
pair structure; it does not fetch or authenticate the source evidence bytes,
so its arithmetic cannot support an empirical claim by itself.

## Safety model

Parallel eligibility is concentrated in fixed-snapshot reads, independent hypotheses, distinct review questions, and isolated worktrees with disjoint declared resources; the host and real resource fences still determine what can run safely.

Shared checkout mutation, integration, mutable databases and daemons, deployment, publication, cleanup, and one-shot actions remain serial and explicitly authorized. If the host cannot prove isolation, unique launch identity, or required resource ownership, Swarm narrows the route or stops. Repository and worker content is treated as untrusted data; embedded instructions cannot grant authority or modify the graph.

## Provenance and credit

This project is an independent derivative of [Forward Future's GPT-5.6 Relay](https://github.com/Forward-Future/gpt-5-6-relay), originally committed by Matthew Berman and distributed under the MIT License.

Swarm retains Relay's core ideas around visible model-specific Codex threads, host-gated model/effort routing, concrete handoffs, one writer per checkout, and serial deployment. It adds the parallel scheduler, concurrency controls, launch-state protocol, resource isolation, task-bound one-shot authority, local artifact-byte verification, write-ahead recovery, drift/rebinding defenses, route library, doctor/status reports, frozen contracts, benchmark evidence, and reconciliation rules.

The upstream copyright and MIT permission notice are preserved in [LICENSE](LICENSE). A detailed provenance record appears in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions are accepted under the repository's MIT License and must preserve third-party notices.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
