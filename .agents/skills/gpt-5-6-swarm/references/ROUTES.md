# Routes and worker budgets

Protocol reference set: `1.3.0`.

Use this reference when preflight needs a route, budget, or graph template. The templates are starting points, not quotas.

`workers` means the total worker-node child-thread ceiling. `parallel` means the peak simultaneous worker-node ceiling. Both exclude an optional proxy coordinator child, which consumes one host slot and is reported separately. Reserve coordinator/recovery capacity and use:

`scheduler peak = min(ready independent lanes, host capacity after coordinator/recovery reserve, requested parallel)`

If host capacity is unknown, default to peak 3. Auto mode normally caps peak at 4. Explicit larger requests still obey platform/resource safety. Never create filler work merely to hit a number.

| Mode | Auto worker ceiling | Auto peak | Typical shape |
| --- | ---: | ---: | --- |
| Mechanical/sequential | 2 | 1 | executor -> verifier |
| Normal build/fix | 5 | 3 | 2 scouts -> 1 writer -> 2 validators |
| Research/review | 6 | 3-4 | independent lenses -> challenger -> synthesis |
| Broad disjoint build | 8 | 3-4 | scouts -> isolated builders -> integrator -> validators |
| Incident/diagnosis | 5 | 3 | evidence/hypotheses -> fixer -> verifier/monitor |
| Content/design | 6 | 3-4 | distinct options -> critics/checker -> editor |

## Normal build or fix

1. Run two read-only scouts concurrently:
   - code boundaries, invariants, and ownership;
   - tests, edge cases, and acceptance strategy.
2. Sol resolves their evidence into a contract.
3. Use one Terra writer unless the contract proves disjoint slices. Disjoint writers use isolated worktrees and exclusive scopes.
4. Serialize integration.
5. Run inspection-only reviewers concurrently against a pinned integrated revision. Command-running validators need isolated validation worktrees plus unique temp/cache/port/database scopes; otherwise run them serially.
6. Fix findings through the owning writer, reintegrate, and rerun affected gates.

## Research

Run independent, snapshot-pinned lanes for:

- primary evidence/source analysis;
- competing explanation or alternative approach;
- counterexamples, limitations, and disconfirmation;
- factual/methodological verification.

Use `quorum:N` only when genuine independent confirmation is useful. The coordinator synthesizes cited evidence and explains disagreements; majority vote is not a gate.

## Review

Choose distinct lenses such as:

- correctness and invariants;
- tests, error paths, and edge cases;
- security, privacy, reliability, and performance;
- API, UX, documentation, and compatibility.

A verifier reproduces high-severity findings. Deduplicate by root cause and rank by evidence/impact, not number of workers reporting it. Reviewers do not edit unless given a separate isolated remediation node.

## Diagnosis or incident

1. Snapshot current evidence.
2. Assign up to three hypothesis owners, each required to reproduce or falsify its hypothesis.
3. Sol selects the cause based on evidence.
4. One Terra writer fixes it and adds a regression check.
5. An independent verifier tests the integrated result.
6. Monitoring/deployment is a separate serial, authorized node.

Do not let evidence collectors mutate the failing system unless their node is reclassified and serialized.

## Broad disjoint build

1. Scouts establish interfaces and shared integration-owned files.
2. Sol passes a contract gate before builders start.
3. Up to three Terra builders work in isolated worktrees on disjoint components.
4. The root grants exactly one integrator the exclusive integration-target lease. That integrator applies artifacts in dependency order and owns lockfiles, generated files, migrations, and cross-cutting configuration.
5. Run focused validation lenses in parallel on the integrated revision.
6. Run the complete combined acceptance gate serially.

If component boundaries are not stable, collapse to one writer.

## Content or product design

Run deliberately different option lanes, not near-identical drafts. Add an audience critic and factual/constraint checker. One editor synthesizes the strongest supported elements into the final artifact and records what was rejected and why.

## One-shot research or release science

Parallelism is limited to snapshot-pinned eligibility and adversarial review. The experiment itself, sealed scoring, adjudication, receipt creation, publication, and cleanup are exclusive serial nodes under the one-shot barrier. A passing prerequisite receipt unlocks the next node; a failure or ambiguous result blocks descendants.

## Join semantics

- `all`: every predecessor must pass.
- `any`: the first valid result may satisfy the logical join; remaining artifacts are invalidated, but live executions remain running/canceling until cooperatively stopped or terminal. No conflicting downstream mutation starts while they remain live.
- `quorum:N`: at least N independent valid results are required, followed by a synthesis/adjudication node.

Use `any` and `quorum` only when lanes are intentionally interchangeable or independently confirmatory. Normal implementation dependencies use `all`.

## Luna long-context routing heuristic

Very large recon inputs can dilute targeted retrieval, and this repository does not yet ship a Luna evaluation baseline. Treat that uncertainty as a reason to measure and route cautiously, not as a threshold or prohibition:

- Estimate each recon lane's input before dispatch. When the input is large enough to risk retrieval dilution, split it into bounded, snapshot-pinned slices, one Luna scout per slice, each briefed with explicit questions and required evidence. Record the chosen slice size as an experimental run parameter until local evals justify a default.
- The cross-slice synthesis node is Terra, not Luna.
- If any Luna scout reports low-confidence retrieval, missing evidence, or contradictory quotes, escalate that lane to Terra as a recorded attempt `N+1` (substitute upward, never silently).
- Whole-corpus "read everything and summarize" lanes over large inputs start on Terra directly; Luna stays on targeted, sliced, or small-context recon where it is fast and cheap.

Peak concurrency for sliced recon still obeys `references/SCHEDULING.md` and the host thread ceiling.
