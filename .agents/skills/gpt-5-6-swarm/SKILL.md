---
name: gpt-5-6-swarm
description: Fan out one task through safe parallel GPT-5.6 worker threads with isolated writes, evidence-based reconciliation, and checkable handoffs.
disable-model-invocation: true
---

# GPT-5.6 Swarm

Parallel where independent. Relay where dependent.

Turn the user's task into a coordinator-owned dependency graph, run every safe ready lane concurrently, and reconcile real artifacts through explicit gates. Use persistent, user-visible Codex threads. Do not replace them with hidden subprocess agents.

Invocation explicitly authorizes the visible child threads required by the route, including deliberate GPT-5.6 model and effort selection. It does not authorize destructive actions, external publication, deployment, purchases, messages, secrets access, or other side effects not already authorized by the user.

The Sol coordinator owns the outcome, graph, worker budget, resource ownership, integration, and final report. More chats are useful only when their work is independent and checkable; never create filler workers.

## Required references

Before launching workers, always read these files completely:

- [`references/ROUTES.md`](references/ROUTES.md) for worker budgets and task-specific graph templates.
- [`references/REPORTING.md`](references/REPORTING.md) for worker briefs, evidence handoffs, progress updates, and the final receipt.

For an all-`PURE`, foreground-only swarm, read the action-class, canonical-ledger/deduplication, canonical-preflight-receipt, worker-accounting, drift, and completion-check sections of [`references/CONCURRENCY.md`](references/CONCURRENCY.md). Read that file completely before any isolated or shared write, command-running validator, background process, external effect, or one-shot action.

If and only if deployment is authorized and included in the graph, also read [`DEPLOYMENT.md`](DEPLOYMENT.md) completely before assigning that node.

## Preflight

1. Restate the outcome, acceptance criteria, constraints, allowed mutations, forbidden actions, and external-side-effect authority. Infer ordinary implementation details; do not infer destructive or public authority.
2. Build the capability matrix: project/thread listing, create/read/message, unique launch discovery, cancel/interrupt, foreground-session completion, background-session liveness/stop, process inspection, worktree starting-state control, and resource locking/fencing. Project/thread listing, creation, reading, and messaging are the minimum for read-only fan-out. Missing stronger capabilities narrow the route as described below; never promise a control the host cannot provide.
3. Resolve the exact project ID for repository work. Use a projectless target only for general tasks.
4. Capture the starting state. For Git work, record branch, revision, dirty paths, untracked scope, and any user-owned changes that must remain untouched.
5. Select a mode and budgets from `ROUTES.md`. `workers` is the total worker-node child-thread ceiling and `parallel` is the peak simultaneous worker-node ceiling; both exclude an optional proxy coordinator child. The proxy consumes one host slot and is reported separately. These are ceilings, not quotas.
6. Classify every proposed node using `CONCURRENCY.md`. Unknown side effects default to exclusive execution.
7. Build the graph and resource-conflict map. Preflight passes only when every node has one owner, one useful deliverable, one gate, known dependencies, and safe resource ownership.
8. Show the kickoff line and compact route table from `REPORTING.md` before launching children.

## Coordinator selection

If the invoking thread is confirmed to be Sol at High or above, it is the coordinator. Otherwise create one Sol Extra High child whose prompt explicitly invokes `/gpt-5-6-swarm` and includes `Swarm role: root coordinator`, the original outcome and authority, project/environment, captured base, budgets, constraints, and forbidden actions. That role marker prevents recursive coordinator creation.

Coordinator creation uses the same launch-nonce protocol as every node. If its creation outcome is ambiguous and the nonce cannot identify exactly one thread, mark the run `UNKNOWN` and do not create another coordinator.

Only the root coordinator may:

- create, start, invalidate, replace, or cancel worker nodes;
- assign write scopes and shared resources;
- change graph state;
- grant and revoke the single integration lease;
- authorize a dependent node to start.

Children may propose new nodes but may not create them. Nested delegation and Ultra are prohibited in Swarm because descendants can launch before the root records their nonce, claim, resource scope, and budget. The root can create additional explicit worker nodes instead.

## Model roster

| Model | Use it for | Starting effort | Thread model ID |
| --- | --- | --- | --- |
| Luna | Snapshot-pinned reconnaissance, deterministic edits, focused checks, packaging, monitoring | Light or Medium | `gpt-5.6-luna` |
| Terra | Implementation, tests, refactors, bounded debugging, integration | High | `gpt-5.6-terra` |
| Sol | Graph design, architecture, hard diagnosis, adjudication, route repair, high-risk review | Extra High | `gpt-5.6-sol` |

Effort mappings are Light=`low`, Medium=`medium`, High=`high`, and Extra High=`xhigh`. Use only efforts supported by the selected host/model. Never silently lower effort. Swarm does not use Ultra because it can delegate outside the root scheduler. Substitute upward only: Luna to Terra, Terra to Sol. Stop if a genuinely Sol-level phase cannot run.

## Build the swarm graph

Create one canonical ledger before dispatch:

| Node | Dependencies/join | Model/effort | Class | Read/write ownership | Deliverable | Gate | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `scan.api` | none / all | Luna Light | PURE | read `src/api/**` | evidence | boundary map | planned |

Each node also records its base revision or immutable input digest, attempt number, thread ID, task fingerprint, resource leases, spawned process identities, and cleanup owner. The coordinator alone updates canonical status.

Use these states:

Execution states and normal transitions are:

Normal nodes use `PLANNED -> READY -> CLAIMED -> LAUNCHING -> RUNNING -> SUCCEEDED | FAILED | ABORTED | UNKNOWN`.

One-shot executors use `LAUNCHING -> PREPARING -> ARMED -> RUNNING`: `PREPARING` means the executor thread exists but is forbidden to act; `ARMED` means its identity/readiness and exact single-use arm nonce are recorded; only acknowledged delivery of that arm enters `RUNNING`.

Before dispatch, `PLANNED | READY -> CANCELED`; `CLAIMED -> CANCELED` only after releasing its claims. `LAUNCHING -> CANCELED` is allowed only when no create/follow-up call was issued. After a dispatch call was issued, resolve the nonce: an identified execution moves to `CANCELING`, authoritative proof of no delivery permits `CANCELED`, and ambiguity becomes `UNKNOWN`. For live work, `PREPARING | ARMED | RUNNING -> CANCELING -> CANCELED | ABORTED | UNKNOWN` is the cancellation path.

`SUCCEEDED`, `FAILED`, `CANCELED`, `ABORTED`, and `UNKNOWN` are terminal execution states. Separately track artifact disposition as `CURRENT`, `INVALIDATED`, `SUPERSEDED`, `REJECTED`, or `INTEGRATED`; changing artifact disposition never stops a live execution. `UNKNOWN` is fail-closed and cannot be retried automatically.

Split a task only when a lane has:

- a useful standalone artifact or evidence result;
- existing, immutable-enough inputs;
- a checkable gate;
- disjoint ownership or an explicit integration seam;
- enough work to exceed thread and handoff overhead.

Decompose by contracts, components, hypotheses, or review lenses—not arbitrary file counts. Settle cross-cutting interfaces before parallel builders begin. Tiny or tightly coupled tasks stay serial even when the skill was invoked.

## Schedule ready work

At each scheduling decision:

1. Recompute which nodes have satisfied dependencies and join conditions (`all`, `any`, or `quorum:N`).
2. Reject any node whose fingerprint matches a `CLAIMED`, `LAUNCHING`, `PREPARING`, `ARMED`, `RUNNING`, `CANCELING`, or `UNKNOWN` node. Reuse a passed artifact only after revalidating its base and assumptions.
3. Acquire all declared resource ownership in canonical order. If scopes overlap or ownership cannot be proven, serialize them.
4. Launch ready nodes up to the peak limit, reserving capacity for coordination and recovery. Prefer critical-path unlocks, uncertainty reduction, high-risk validation, then short deterministic tasks.
5. Generate an immutable launch nonce, include it in the initial prompt, atomically move the node to `LAUNCHING`, then create the thread. If creation returns normally, record the thread ID and move a normal node to `RUNNING` or a preparation-only one-shot executor to `PREPARING`. If creation times out or is ambiguous, use thread discovery to find the nonce. Exactly one match may be adopted; zero or multiple matches become `UNKNOWN` and prohibit relaunch.
6. Backfill a freed slot immediately when another independent node becomes ready. Do not wait for a whole wave when useful downstream work is safe to begin.
7. Fan in actual commits, diffs, documents, test output, or receipts. A prose summary is not an artifact.

While an attempt is still running, send ordinary clarifications to the same child thread without changing model or effort. After a terminal `FAILED` result, reuse that thread's context only through ledger attempt `N+1`: generate a new nonce, move the new attempt to `LAUNCHING`, and send one follow-up to the existing thread. Acknowledged delivery moves it to `RUNNING`. If delivery is ambiguous, read that thread for the nonce; exactly one matching turn may be adopted, while zero or multiple matches become `UNKNOWN` and prohibit resend. Record the new evidence separately.

Automatic retry is limited to `PURE`, `ISOLATED`, or safely keyed work after the prior outcome is known. An effort-only escalation uses the same attempt-`N+1` follow-up path with an explicitly recorded higher effort when supported. A model/responsibility change creates a superseding thread only after the previous execution is terminal. Never automatically retry `NON_IDEMPOTENT`, `EXCLUSIVE_UNKNOWN`, `ONE_SHOT`, or any ambiguous outcome. Sparse output, a delayed response, or a wrapper timeout never authorizes a duplicate launch.

If hard cancellation is unavailable, ask the worker to stop cooperatively and wait. A non-terminal worker becomes `UNKNOWN`; do not replace it or unlock conflicting work. If unique launch discovery is unavailable, create at most one node at a time and treat any ambiguous create response as `UNKNOWN`. If child tool restriction is unavailable, nested delegation remains forbidden by prompt and any unreported descendant blocks completion.

## Safe parallel work

Parallelize snapshot-pinned reads, independent hypotheses, independent review lenses, isolated builds, and tests whose resources are disjoint.

For parallel implementation:

- create one isolated worktree per writer at the recorded base revision;
- serialize worktree creation/removal, assign a unique exact ref when commits are allowed, and verify `HEAD == base_revision` before any inspection or mutation;
- assign exclusive, non-overlapping file/glob/component ownership;
- reserve lockfiles, generated outputs, root configuration, migrations, registries, shared databases, and other high-churn surfaces for the serial integrator;
- isolate temp directories, caches, virtual environments, ports, databases, daemons, generated assets, and other runtime resources; a file-only boundary is insufficient;
- require each writer to return a commit or precise patch plus checks and touched paths;
- integrate in topological order, testing each seam and then the combined result.

Record one integration target during preflight: the user's checkout only when its baseline and user changes can be preserved, otherwise a dedicated clean integration worktree. The root may perform integration itself or grant exactly one Terra integration node an exclusive lease; while that lease exists, the root performs no integration-target mutation. If reliable isolation, an exact-base boot, or ownership cannot be proven, use one serial writer. A dirty starting state cannot be reconstructed in a fresh worktree unless the user explicitly authorizes a working-tree start.

Independent review means different questions, not repeated busywork. Blind duplication is permitted only for an explicit `any` or `quorum:N` research/design node. Resolve disagreement by evidence and acceptance gates, never by worker vote.

## Exclusive and one-shot work

Never parallelize shared checkout mutation, Git integration/publication, mutable database or daemon work, migrations, external messages, deployment, rollback, shared cleanup, or an action with unknown side effects.

Scientific experiments, sealed-data scoring, irreversible actions, and commands that may become invalid when repeated are `ONE_SHOT`. Apply the complete barrier in `CONCURRENCY.md`, including two-stage arming: create a preparation-only executor, record and verify it as ready, then send one exact arm nonce. Never put the one-shot command in the executor's initial prompt and never resend an ambiguously delivered arm message.

If repository state, runtime files, processes, or external resources change from an unaccounted source, freeze all affected mutation nodes. Investigate read-only. Do not “work around” an unknown writer.

## Fan-in and gates

The coordinator checks every handoff against its node gate and actual artifact. High-risk artifacts require an independent reviewer who did not author them.

An integration node verifies:

- provenance and immutable input/base compatibility;
- write-scope compliance and preservation of user changes;
- each node's focused gate;
- cross-node seams and combined acceptance checks;
- no unaccounted worker, process, lease, artifact, or cleanup item.

If an upstream contract changes, invalidate affected descendants. Reuse only outputs whose inputs and assumptions remain valid. Release or publication may begin only from the integrated revision after its complete gate passes.

## Failure and escalation

- Incomplete evidence while running: clarify the same attempt. After terminal failure, create recorded attempt `N+1` in the same thread only for safely retryable classes.
- Insufficient reasoning depth: increase effort one level in recorded attempt `N+1` when the class is safely retryable.
- New ambiguity or hidden invariants: promote Luna to Terra or Terra to Sol.
- Conflicting artifacts or invalid decomposition: pause descendants and have Sol rebuild that subgraph.
- Worker stall: inspect thread/session state and recorded background processes; request cooperative stop; do not replace it until termination is proven. Without a cancel primitive, a non-terminal stall blocks the affected graph.
- Ambiguous one-shot/external outcome: mark `UNKNOWN`, preserve evidence, and stop.
- Capacity, cost, or quota pressure: shrink peak concurrency and report it; do not weaken gates.

## Completion

The run is not complete while any action is claimed, launching, preparing, armed, running, canceling, or unknown, or while any worker, observable background process, resource lease, output, or cleanup item is unaccounted for. A synchronous foreground command is accounted for by terminal-tool completion plus exit status; deliberately backgrounded work additionally requires a host-exposed session/process identity and liveness/stop controls, otherwise it is prohibited.

Lead the final response with the finished outcome. Include the actual—not planned—receipt: coordinator identity, total visible children, worker thread IDs, models/efforts, artifacts, gates, worker count, scheduler-issued peak concurrency, integrations, escalations, invalidations, skipped lanes, external effects, and remaining risks. Call concurrency “observed” only when the host provides authoritative active-thread telemetry. Child threads are user-owned records; do not archive them automatically.
