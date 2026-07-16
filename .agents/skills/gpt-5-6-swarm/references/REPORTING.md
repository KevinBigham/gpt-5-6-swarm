# Briefs, progress, and receipts

Read this reference completely before dispatch.

## Kickoff

Tell the user the chosen mode and ceilings before creating workers:

```text
Swarm: build · workers 5 · peak 3 · writes serial · coordinator current
```

Then show a compact graph table with node, dependencies, role, model/effort, ownership, deliverable, and gate. Clearly label it planned.

## Worker brief

Every child receives a self-contained prompt:

```markdown
Swarm role: <worker role; root coordinator only when applicable>
Run/node/launch nonce: <run ID / stable node ID / attempt / immutable nonce>
Outcome: <one concrete result>
Dependencies/join: <passed nodes and artifact references>
Starting revision/inputs: <immutable commits, paths, URLs, hashes, evidence>
Read scope: <allowed inspection>
Write/resource scope: <exclusive ownership, or none>
Integration-owned surfaces: <must only propose, not edit>
Class: <PURE | ISOLATED | KEYED_IDEMPOTENT | NON_IDEMPOTENT | EXCLUSIVE_UNKNOWN | ONE_SHOT>
Constraints: <invariants, authority, forbidden actions, worker/descendant budget>
Stop conditions: <scope drift, unexpected writer, failed prerequisite, ambiguity>
Acceptance: <checks proving this node is done>
Return: <artifact/evidence plus the handoff receipt below>
```

Point to canonical files and actual predecessor artifacts rather than pasting a long conversation. Give each independent lane a distinct question or deliverable.

## Worker handoff

Require evidence in this shape (YAML may be embedded in prose when necessary):

```yaml
run_id: ...
node_id: ...
attempt: 1
status: SUCCEEDED
thread_id: ...
model_effort: terra/high
base_revision: ...
artifact: <commit, patch, document, evidence, or receipt reference>
touched_paths: []
commands:
  - command: ...
    exit_code: 0
    result: ...
processes:
  spawned: []
  remaining_live: []
resources_released: []
artifact_hashes: {}
descendant_thread_ids: []
assumptions: []
unresolved_risks: []
cleanup_items: []
```

“Done” is not a gate. The coordinator verifies the actual artifact, process exit, resource reconciliation, and check output.

## Progress

Update on meaningful graph changes and at sensible intervals while work is active. Keep it scannable:

```text
2/5 complete · 2 running · 1 waiting — scouts agree on the boundary; implementation has started.
```

Report:

- nodes completed/running/waiting/blocked;
- actual current peak and any capacity reduction;
- a decision or disagreement that changes the route;
- safety pauses, invalidations, escalations, or unexpected external state.

Do not flood the user with raw worker transcripts. Do not call a created thread completed.

## Fan-in review

For each result, record:

- thread ID, model/effort, and actual terminal status;
- artifact identity and immutable base;
- scope compliance and checks;
- whether assumptions still hold;
- accepted, rejected, superseded, or invalidated disposition.

High-risk findings are reproduced independently. Conflicts are resolved by evidence and acceptance gates, not votes.

## Final response

Lead with the actual outcome, then provide the smallest useful receipt:

| Lane | Thread ID | Model/effort | Artifact | Gate/result |
| --- | --- | --- | --- | --- |

Also state:

- coordinator thread (current or proxy), total visible children, actual worker nodes created, and scheduler-issued peak concurrency; call peak “observed” only when the host exposes authoritative active-thread telemetry;
- integrated revision/output and combined checks;
- corrections, escalations, substitutions, invalidations, disagreements, and skipped lanes with reasons;
- authorized external effects and health evidence, if any;
- remaining blockers or risks;
- confirmation that workers, processes, leases, and cleanup are reconciled.

Never report the planned graph as the route actually run.
