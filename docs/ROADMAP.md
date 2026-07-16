# Development roadmap

This roadmap converts the 2026-07 independent adversarial audit into testable releases. Findings are accepted only when reproduced against source; the audit's inability to retrieve raw GitHub files was an auditor-environment limitation, not missing repository source.

## v0.2.0 - public experimental release

- Scope every public claim as executable, prompt-instructed, host-gated, or externally fenced.
- Publish CI, an 85% ledger coverage gate, and an invariant-to-test map.
- Add protocol reference-set version stamps and fail-closed compatibility checks.
- Surface capability tier and disabled features from explicit declarations.
- Add Git HEAD/dirty-state capture and drift verification.
- Require an explicit fresh-output/target fence before one-shot creation.
- Add an untrusted-artifact boundary and non-expert `UNKNOWN` runbook.
- Exercise real process generation races and atomic-replace failure recovery.

Release gate: all platform jobs and coverage pass; skill validation passes; public release tag and evidence links exist.

## v0.3.0 - Gen-2 evidence binding

- Bind local path-scoped receipts to coordinator-recomputed artifact hashes.
- Require fresh, task-bound, single-use operator records before one-shot creation.
- Add intent/commit WAL recovery, ignored-file drift hashing, path rebinding
  defense, case-normalized resources, and a safety-focused `doctor` command.
- Add a compact coordinator kernel and progressive reference routing.
- Expand adversarial branch-aware coverage around authorization, artifacts,
  drift, symlink swaps, and both crash windows.

Release gate: path-scoped success verifies real bytes; WAL crash cases recover
without manual re-anchoring; every new invariant has accepting/rejecting tests.

## v0.4.0 - trustworthy host adapter

- Bind spawn, turn-read, nonce discovery, cancellation, and lifecycle evidence
  through a tested host adapter once the host exposes a trustworthy
  coordinator nonce/prompt at child start and a way to refuse mismatches.
- Add mocked-host end-to-end tests for lost create responses, orphan adoption,
  cancellation failure, and lost arm acknowledgment.
- Add property-based scope tests without making property-test tooling a
  runtime dependency.

Release gate: every host claim is emitted by a tested adapter. Current
`SubagentStart` metadata is insufficient because it does not expose the launch
prompt/nonce and cannot veto a start; v0.3 therefore makes no such claim.

## v1.0 - validated public beta exit

- Publish benchmark methodology and break-even data against a serial coordinator.
- Validate crash recovery under controlled kill injection and document supported filesystem semantics.
- Validate Windows and a representative network-filesystem policy (supported with evidence or explicitly refused).
- Complete an independent source-level audit against a tagged commit.

Release gate: no safety claim depends on unpublished evidence; remaining prompt-only and host-dependent limits are explicit in the release notes.

Production-sensitive use remains out of scope until the actual host supports authoritative routing/discovery/cancel and every external target supplies effective fencing or idempotency.
