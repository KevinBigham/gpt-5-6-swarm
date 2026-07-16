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

## v0.3.0 - host adapter and artifact binding

- Bind receipts to coordinator-recomputed artifact hashes under an explicit artifact root.
- Add a host adapter/probe interface for spawn, turn-read, nonce discovery, cancellation, and lifecycle hooks.
- Add mocked-host end-to-end tests for lost create responses, orphan adoption, cancellation failure, and lost arm acknowledgment.
- Add property-based scope tests without making property-test tooling a runtime dependency.

Release gate: every host claim is emitted by a tested adapter; receipt acceptance can verify real bytes for local artifacts.

## v1.0 - validated public beta exit

- Publish benchmark methodology and break-even data against a serial coordinator.
- Validate crash recovery under controlled kill injection and document supported filesystem semantics.
- Validate Windows and a representative network-filesystem policy (supported with evidence or explicitly refused).
- Complete an independent source-level audit against a tagged commit.

Release gate: no safety claim depends on unpublished evidence; remaining prompt-only and host-dependent limits are explicit in the release notes.

Production-sensitive use remains out of scope until the actual host supports authoritative routing/discovery/cancel and every external target supplies effective fencing or idempotency.
