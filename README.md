# GPT-5.6 Swarm

**Parallel where independent. Relay where dependent.**

GPT-5.6 Swarm is a Codex skill that turns a task into a coordinator-owned dependency graph, runs safe independent lanes in parallel, and reconciles their actual artifacts through explicit gates.

It is designed to make multi-chat work faster without allowing duplicate launches, overlapping writers, ambiguous one-shot retries, or unbounded thread growth.

This is an independent community derivative of [Forward Future's GPT-5.6 Relay](https://github.com/Forward-Future/gpt-5-6-relay). It is not affiliated with or endorsed by Forward Future, Matthew Berman, or OpenAI.

## Host compatibility

- Minimum: a Codex environment that supports skills plus host-managed subagent
  creation, accounting, and result collection.
- Full model-specific routing additionally needs per-child model and effort
  controls plus access to GPT-5.6 Sol, Terra, and Luna.
- Guarded retries and one-shot work additionally need unique-launch discovery,
  thread inspection, cancellation/accounting, and the relevant real resource
  fences.
- Deterministic enforcement needs Python 3.9+ and permission to write
  control-plane state under `.swarm/runs/` (or an explicitly selected root).

If the host lacks the minimum host-managed subagent controls, the skill reports the proposed graph and continues serially in the coordinator. If stronger cancellation, isolation, or resource-locking capabilities are unavailable, it narrows to read-only fan-out or serialized work instead of pretending full parallel safety.

## Install

Copy the skill into a Codex project:

```sh
mkdir -p /path/to/project/.agents/skills
cp -R .agents/skills/gpt-5-6-swarm /path/to/project/.agents/skills/
```

Or install it for one user across repositories:

```sh
mkdir -p ~/.agents/skills
cp -R .agents/skills/gpt-5-6-swarm ~/.agents/skills/
```

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
- Model-aware routing across GPT-5.6 Sol, Terra, and Luna.
- Parallel snapshot-pinned research, independent review lenses, and disjoint worktree implementation.
- One exclusive integration lease for the canonical result.
- Launch nonces and explicit state transitions to prevent duplicate work.
- Capability-based fallback to read-only fan-out or serialized work.
- Fail-closed handling for unknown writers, stalled workers, cancellation, external effects, and one-shot science.
- Evidence-bearing handoffs and actual—not planned—route receipts.

The normative protocol is in [SKILL.md](.agents/skills/gpt-5-6-swarm/SKILL.md). Its supporting references cover [concurrency and safety](.agents/skills/gpt-5-6-swarm/references/CONCURRENCY.md), [route templates](.agents/skills/gpt-5-6-swarm/references/ROUTES.md), [reporting](.agents/skills/gpt-5-6-swarm/references/REPORTING.md), and [deployment](.agents/skills/gpt-5-6-swarm/DEPLOYMENT.md).

## Deterministic enforcement (Phase 1)

The protocol's safety-critical control-plane invariants are enforced by a standard-library Python tool, not just prose: a run-local ledger under `.swarm/runs/<run-id>/` (gitignored) with a legal-transition state machine, fingerprint deduplication, launch/arm nonce uniqueness, one-active-owner and resource-scope rules, receipt-gated known terminal outcomes, generation compare-and-set, secure atomic persistence, and fail-closed `UNKNOWN` handling.

```sh
python3 .agents/skills/gpt-5-6-swarm/scripts/swarm_ledger.py --help
python3 -m unittest discover -s tests   # offline, deterministic
```

See [ENFORCEMENT.md](.agents/skills/gpt-5-6-swarm/references/ENFORCEMENT.md) for the lifecycle, scope boundary, exit codes, recovery, and the versioning contract; [SCHEDULING.md](.agents/skills/gpt-5-6-swarm/references/SCHEDULING.md) for bounded concurrency; [HOSTS.md](.agents/skills/gpt-5-6-swarm/references/HOSTS.md) for verified host capabilities versus gated experiments. The prompt-only workflow remains available when command execution or permission for control-plane state writes is absent.

## Safety model

Parallelism is concentrated where it is genuinely safe: fixed-snapshot reads, independent hypotheses, distinct review questions, and isolated worktrees with disjoint resources.

Shared checkout mutation, integration, mutable databases and daemons, deployment, publication, cleanup, and one-shot actions remain serial and explicitly authorized. If the host cannot prove isolation, unique launch identity, or required resource ownership, Swarm narrows the route or stops.

## Provenance and credit

This project is an independent derivative of [Forward Future's GPT-5.6 Relay](https://github.com/Forward-Future/gpt-5-6-relay), originally committed by Matthew Berman and distributed under the MIT License.

Swarm retains Relay's core ideas around visible model-specific Codex threads, explicit model/effort routing, concrete handoffs, one writer per checkout, and serial deployment. It adds the parallel scheduler, concurrency controls, launch-state protocol, resource isolation, one-shot barrier, route library, and reconciliation rules.

The upstream copyright and MIT permission notice are preserved in [LICENSE](LICENSE). A detailed provenance record appears in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions are accepted under the repository's MIT License and must preserve third-party notices.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
