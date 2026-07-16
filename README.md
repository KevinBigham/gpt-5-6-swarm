# GPT-5.6 Swarm

[![CI](https://github.com/KevinBigham/gpt-5-6-swarm/actions/workflows/ci.yml/badge.svg)](https://github.com/KevinBigham/gpt-5-6-swarm/actions/workflows/ci.yml)

**Parallel where independent. Relay where dependent.**

GPT-5.6 Swarm is a Codex skill that turns a task into a coordinator-owned dependency graph, runs eligible independent lanes in parallel, and reconciles their actual artifacts through explicit gates.

It is designed to make multi-chat work faster while deterministically enforcing consistency of the coordinator's recorded control plane. It prevents duplicate automatic dispatch in its own ledger and halts on ambiguity; it does not claim exactly-once external effects or locks against untracked writers.

This is an independent community derivative of [Forward Future's GPT-5.6 Relay](https://github.com/Forward-Future/gpt-5-6-relay). It is not affiliated with or endorsed by Forward Future, Matthew Berman, or OpenAI.

## From relay race to mission control

GPT-5.6 Relay began with a powerful, simple idea: give one part of a job to a
specialized AI chat, collect its handoff, and pass the baton to the next chat.
Different models and effort levels could handle research, implementation,
review, or difficult reasoning without forcing one conversation to do
everything.

GPT-5.6 Swarm asks the next question: **what if every independent part could
move at the same time, while dependent or conflicting work still waited its
turn?**

The result is closer to a carefully managed construction project than a group
chat:

- the **coordinator** is the general contractor and owns the final outcome;
- the **task graph** is the construction plan and records what depends on what;
- each **worker** is a specialist with one bounded assignment and acceptance
  test;
- **resource scopes** say which specialist may touch which files, services, or
  other surfaces;
- the **ledger** records assignments, ownership, launches, results, and
  unresolved uncertainty;
- **receipts and gates** show what was actually produced and whether it is good
  enough for dependent work to begin;
- one **integration owner** assembles accepted work so several writers never
  fight over the final checkout; and
- the **doctor** command checks recorded safety, artifacts, capabilities,
  ambiguity, and the exact state from which a run may resume.

Swarm therefore evolved from "send a job through several smart AI chats" into
"plan, coordinate, parallelize, verify, recover, and safely integrate the work
of an AI engineering team." It still preserves Relay's visible threads,
model-aware routing, concrete handoffs, single-checkout-writer rule, serial
deployment discipline, MIT license, and upstream credit.

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

Copy the skill into a Codex project:

```sh
mkdir -p /path/to/project/.agents/skills
cp -R .agents/skills/gpt-5-6-swarm /path/to/project/.agents/skills/
```

Also ensure the target repository ignores `.swarm/runs/`; runtime ledgers and
journals must never be committed. `capture-baseline` excludes this exact
control-plane path from its dirty-state digest even when the target ignore file
has not yet been updated.

Or install it for one user across repositories:

```sh
mkdir -p ~/.agents/skills
cp -R .agents/skills/gpt-5-6-swarm ~/.agents/skills/
```

The current Codex manual's user-skill location is `~/.agents/skills`. Some
Codex-managed creator/installer flows may use `$CODEX_HOME/skills` (commonly
`~/.codex/skills`). If the skill is already visible there, do not install a
second copy: duplicate skill names can both appear in selectors instead of
being merged.

Invoke it explicitly:

```text
Use $gpt-5-6-swarm to orchestrate: <task>
```

Optional conversational controls:

```text
Use $gpt-5-6-swarm in build mode with workers=6 and parallel=3: <task>
```

`workers` is the total worker-node budget. `parallel` is the peak simultaneous worker-node budget. Both exclude an optional proxy coordinator. They are ceilings, not quotas.

## Plain-English operating specs

### How many agents does it use?

There is always one root coordinator. If the current chat cannot serve as the
required Sol coordinator, and the host can verifiably select both model and
effort, Swarm may add one proxy coordinator. That proxy consumes a host slot
but is reported separately from the worker budget.

The remaining agents are worker threads. Two numbers control them:

- `workers` is the maximum number of worker jobs across the complete run;
- `parallel` is the maximum number of those workers that may be active at the
  same time.

For example, `workers=8 parallel=3` does **not** mean eight workers all run at
once. It means at most eight worker jobs may be used, normally in dependency
waves, with no more than three active simultaneously.

Automatic route budgets are intentionally conservative:

| Job type | Worker jobs across the run | Normally active at once | Typical team shape |
| --- | ---: | ---: | --- |
| Mechanical or sequential | 2 | 1 | executor, then verifier |
| Normal build or bug fix | 5 | 3 | two scouts, one writer, two validators |
| Research or review | 6 | 3-4 | independent evidence lenses, challenger, synthesis |
| Broad disjoint build | 8 | 3-4 | scouts, up to three isolated builders, integrator, validators |
| Incident or diagnosis | 5 | 3 | competing hypothesis owners, fixer, verifier/monitor |
| Content or product design | 6 | 3-4 | distinct options, critics/checker, final editor |

These are ceilings, not targets. Swarm does not invent filler jobs to make the
team look bigger. A small or tightly connected task can stay entirely in the
coordinator, and overlapping writes collapse to one writer.

The scheduler also obeys the actual host limit. It reserves capacity for
coordination and recovery, defaults to a peak of three when host capacity is
unknown, and normally caps automatic parallelism at four. The default class
ceilings are:

| Work class | Default simultaneous ceiling |
| --- | ---: |
| Read-only, independent work | 4 |
| Isolated writers with proven disjoint resources | 3 |
| Overlapping writers | 1 |
| Non-repeatable, unknown-side-effect, or one-shot work | 1, always serial |

The real peak is the smallest of: ready independent jobs, available host
slots, the work-class ceiling, the requested `parallel` value, and the amount
of work the coordinator can responsibly inspect.

### What are the agents actually doing?

The coordinator breaks the requested outcome into checkable jobs rather than
asking several chats to produce near-identical answers. Workers commonly act
as:

- **scouts**, mapping code, evidence, constraints, interfaces, and risks;
- **hypothesis owners**, independently trying to reproduce or disprove a
  suspected cause;
- **builders**, implementing clearly separated components in isolated
  worktrees;
- **reviewers**, examining different lenses such as correctness, security,
  edge cases, compatibility, UX, or performance;
- **validators**, running focused checks against a pinned integrated revision;
- **challengers**, looking for counterexamples and unsupported conclusions;
- **integrators**, applying accepted artifacts in dependency order under one
  exclusive integration lease; and
- **editors or synthesizers**, reconciling evidence and producing one coherent
  final result.

The model roster gives each kind of work an appropriate starting point:

| Model | Plain-English role | Typical work | Starting effort |
| --- | --- | --- | --- |
| Luna | fast scout and checker | focused reconnaissance, deterministic edits, packaging, monitoring | Light or Medium |
| Terra | primary engineer | implementation, tests, refactors, bounded debugging, integration | High |
| Sol | architect and judge | graph design, architecture, hard diagnosis, disagreement resolution, high-risk review | Extra High |

Routing is honest about the host. If the active Codex surface cannot pin a
requested model or effort, Swarm reports that limitation instead of claiming
the route happened. Workers may recommend another job, but they cannot create
children of their own: only the root coordinator launches and accounts for
workers. This prevents an invisible, uncontrolled tree of agents.

### What does a normal five-worker build look like?

1. Two read-only scouts inspect the same pinned starting revision in parallel.
   One maps architecture and ownership; the other maps tests, edge cases, and
   acceptance checks.
2. The Sol coordinator reconciles their evidence into one implementation
   contract.
3. One Terra builder implements the change. More builders are allowed only
   when their components, worktrees, files, caches, ports, and other resources
   are provably separate.
4. The coordinator or one leased integrator assembles the accepted work.
5. Two independent validators inspect different risks against the same pinned
   integrated revision.
6. Findings return to the owning builder, affected gates rerun, and the
   coordinator reports the actual agents, artifacts, integrations, failures,
   skipped lanes, and remaining risks.

This is a pipeline with safe overlap, not five people editing the same file.
When a worker finishes, the scheduler can immediately fill the freed slot with
newly eligible work instead of waiting for an entire batch to finish.

### How does work wait for other work?

Every worker job is a node in a dependency graph. Its join rule says when it
may proceed:

- `all`: every required predecessor must pass;
- `any`: the first valid interchangeable result can satisfy the join, while
  remaining live work is still tracked and safely stopped or accounted for;
- `quorum:N`: at least N genuinely independent results are required before a
  separate synthesis or adjudication step.

A worker saying "done" is not enough. The coordinator expects a real commit,
patch, document, test result, or structured receipt, checks it against the
node's gate, and independently recomputes hashes for named local artifacts.
High-risk work receives independent review from someone who did not author it.

### What keeps the swarm from becoming chaos?

- Duplicate task fingerprints and launch nonces prevent accidental duplicate
  recorded dispatch.
- Only the coordinator writes the canonical ledger.
- Writers receive declared, exclusive resource scopes; conflicting scopes are
  serialized.
- Shared-checkout integration, publication, deployment, migrations, shared
  cleanup, and unknown side effects never run in parallel.
- Ambiguous launches or outcomes become `UNKNOWN` and freeze unsafe follow-on
  work instead of being silently retried.
- One-shot actions require fresh task-bound authorization, a verified target
  fence, a preparation phase, and one exact arm message.
- An intent/commit write-ahead journal lets the control plane distinguish and
  recover supported crash windows.
- Git and optional ignored-file drift checks detect changes from outside the
  recorded plan.

This makes Swarm a deterministic coordinator for its recorded control plane,
not a magical lock over the rest of the world. Databases, deployments,
payments, email, and other external systems still require real transactions,
idempotency keys, or target-side fencing. When proof is missing, Swarm narrows
the route, runs serially, or stops.

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

The normative protocol is in [SKILL.md](.agents/skills/gpt-5-6-swarm/SKILL.md). Its supporting references cover [concurrency and safety](.agents/skills/gpt-5-6-swarm/references/CONCURRENCY.md), [route templates](.agents/skills/gpt-5-6-swarm/references/ROUTES.md), [reporting](.agents/skills/gpt-5-6-swarm/references/REPORTING.md), and [deployment](.agents/skills/gpt-5-6-swarm/DEPLOYMENT.md).

## Deterministic enforcement evidence

The protocol's represented safety-critical control-plane invariants are enforced by a standard-library Python runtime tool, not just prose: a run-local ledger under `.swarm/runs/<run-id>/` (gitignored) with a legal-transition state machine, fingerprint deduplication, launch/arm/authorization nonce uniqueness, one-active-owner and re-resolved resource-scope rules, receipt-gated outcomes, local artifact-byte recomputation, generation compare-and-set, write-ahead recovery, protocol-reference compatibility, and fail-closed `UNKNOWN` handling.

```sh
python3 .agents/skills/gpt-5-6-swarm/scripts/swarm_ledger.py --help
python3 -m unittest discover -s tests   # offline, deterministic
python3 .agents/skills/gpt-5-6-swarm/scripts/swarm_ledger.py verify-reference-set
```

CI runs the offline suite across Python 3.9/3.11/3.13 on Ubuntu, Python 3.11 on Windows and macOS, plus a published branch-aware coverage gate for the ledger. See [the invariant-to-test map](docs/INVARIANTS.md), [operator runbook](docs/RUNBOOK.md), and [development roadmap](docs/ROADMAP.md).

See [ENFORCEMENT.md](.agents/skills/gpt-5-6-swarm/references/ENFORCEMENT.md) for the lifecycle, scope boundary, exit codes, recovery, and versioning contract; [SCHEDULING.md](.agents/skills/gpt-5-6-swarm/references/SCHEDULING.md) for bounded concurrency; [HOSTS.md](.agents/skills/gpt-5-6-swarm/references/HOSTS.md) for verified host capabilities versus gated experiments. The prompt-only workflow remains available when command execution or permission for control-plane state writes is absent.

## Safety model

Parallel eligibility is concentrated in fixed-snapshot reads, independent hypotheses, distinct review questions, and isolated worktrees with disjoint declared resources; the host and real resource fences still determine what can run safely.

Shared checkout mutation, integration, mutable databases and daemons, deployment, publication, cleanup, and one-shot actions remain serial and explicitly authorized. If the host cannot prove isolation, unique launch identity, or required resource ownership, Swarm narrows the route or stops. Repository and worker content is treated as untrusted data; embedded instructions cannot grant authority or modify the graph.

## Provenance and credit

This project is an independent derivative of [Forward Future's GPT-5.6 Relay](https://github.com/Forward-Future/gpt-5-6-relay), originally committed by Matthew Berman and distributed under the MIT License.

Swarm retains Relay's core ideas around visible model-specific Codex threads, host-gated model/effort routing, concrete handoffs, one writer per checkout, and serial deployment. It adds the parallel scheduler, concurrency controls, launch-state protocol, resource isolation, task-bound one-shot authority, local artifact-byte verification, write-ahead recovery, drift/rebinding defenses, route library, doctor report, and reconciliation rules.

The upstream copyright and MIT permission notice are preserved in [LICENSE](LICENSE). A detailed provenance record appears in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions are accepted under the repository's MIT License and must preserve third-party notices.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
