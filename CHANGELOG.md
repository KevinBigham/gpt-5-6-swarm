# Changelog

Versioning in this repository is three independent contracts, defined here
and in `references/ENFORCEMENT.md`. Nothing beyond what is written is
promised.

- **protocol_version** - the prose protocol (SKILL.md + references).
  MAJOR: invariant semantics change. MINOR: additive guidance or
  enforcement that weakens no existing invariant. PATCH: editorial.
- **schema_version** - the ledger document shape, a bare integer. The
  enforcement tool refuses versions outside its supported set (exit 6);
  there is no silent conversion.
- **tool version** - `swarm_ledger.py` build, recorded in every ledger it
  writes.

## protocol 1.1.0 / schema 1 / tool 0.1.0 - Phase 1 (2026-07-15)

- Added the deterministic enforcement core: run-local ledger under
  `.swarm/runs/<run-id>/` (gitignored), stdlib-only validator/mutator
  `scripts/swarm_ledger.py`, offline scenario test suite, and CI.
- Enforced: legal transitions, fingerprint dedup, nonce uniqueness,
  one-shot arm/spend discipline, one-active-owner, generation
  compare-and-set, receipt-gated known terminal outcomes, resource-scope conflicts and
  freezes, fail-closed UNKNOWN with explicit reconciliation, capability
  gating, version fail-closed, adversarial input rejection.
- Hardened atomic files, journals, and recovery against symlinks and
  non-regular paths; added terminal process-accounting receipts, init-race
  protection, torn-journal repair evidence, safe supersession, and strict
  structural validation for hostile or hand-edited documents.
- New references: ENFORCEMENT.md, SCHEDULING.md, HOSTS.md. ROUTES.md gains
  a conservative, explicitly unevaluated Luna long-context heuristic and a note on `max`
  effort. No existing safety doctrine was weakened or removed.
- The prompt-only workflow at protocol 1.0.0 remains fully supported; the
  enforcement layer engages only where the host can execute commands.

## protocol 1.0.0 - base commit ffe1786

- Initial published skill (prompt-law protocol; retroactively 1.0.0).
