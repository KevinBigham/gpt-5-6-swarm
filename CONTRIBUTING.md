# Contributing

Contributions are welcome when they make the orchestration protocol safer, clearer, faster, or easier to verify.

## Ground rules

- Preserve the upstream attribution and MIT notice in `LICENSE` and `THIRD_PARTY_NOTICES.md`.
- Identify any newly incorporated third-party material, its source revision, and its license.
- Do not weaken fail-closed behavior merely to increase apparent concurrency.
- Keep worker roles bounded, artifacts checkable, and shared mutations serialized.
- Update the relevant route, concurrency, and reporting references together when changing a cross-cutting invariant.
- Keep every normative skill document on the exact protocol reference-set stamp; `verify-reference-set` and `init` fail closed on partial upgrades.
- Changes to `scripts/swarm_ledger.py` or any enforced invariant must ship with accompanying scenario tests (both an accepting and a rejecting case where applicable); run `python -m unittest discover -s tests` locally - the suite is offline and standard-library only.
- Keep ledger statement coverage at or above 85% with `python -m coverage run -m unittest discover -s tests`, `python -m coverage combine`, and `python -m coverage report` after installing `requirements-dev.txt`.

By contributing, you agree that your contribution is licensed under this repository's MIT License.

## Review expectations

Changes to launch identity, cancellation, one-shot behavior, resource ownership, or integration should include an adversarial review explaining how duplicate work, hidden writers, and ambiguous outcomes remain blocked.

Public claims must identify their layer: executable recorded-control-plane enforcement, prompt instruction, host-gated behavior, or real external fencing. Do not describe a declared ledger lease as a lock against untracked writers.
