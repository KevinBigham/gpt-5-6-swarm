# Invariant-to-test traceability

This map is the public evidence index for protocol `1.2.0`, schema `1`, and tool `0.2.0`. Test names are stable review handles; GitHub Actions is the execution record. Passing tests demonstrate the tool's recorded-control-plane behavior under the tested conditions. They do not prove that a host, worker, filesystem, or external service honored a recorded claim.

| Invariant | Executable mechanism | Primary regression evidence |
| --- | --- | --- |
| Legal state transitions; terminal states immutable | `LEGAL_TRANSITIONS`, edge guards, `op_transition` | `TestIllegalTransitions.test_illegal_transition_rejected`, `test_terminal_states_are_terminal`, `test_unknown_cannot_be_un_unknowned` |
| Normalized task deduplication | `compute_fingerprint`, blocking-state scan | `TestFingerprintDedup` |
| Launch nonces unique across the run | nonce registries and `op_create_node` | `TestNonces.test_duplicate_launch_nonce`, `test_arm_nonce_cannot_reuse_launch_nonce_namespace` |
| One recorded dispatch per attempt | `op_record_dispatch` and transition guards | `TestAmbiguousCreation`, `TestCli.test_cli_mutation_lifecycle` |
| One-shot arm nonce is single-use | `op_record_arm_dispatch`, arm transition guards | `TestOneShot.test_one_shot_double_arm_rejected`, `test_ambiguous_arm_delivery_freezes_forever` |
| One-shot needs target/fresh-output fence evidence | `one_shot_fence` capability gate | `TestOneShot.test_one_shot_requires_declared_fence` |
| One active owner per node/thread | semantic validation and thread-lineage rules | `TestHostile.test_two_owners_rejected`, `test_two_owner_hand_edit_detected_by_validate` |
| Declared path scopes cannot overlap while held | normalized resource scopes and prefix conflict checks | `TestResources.test_conflicting_resource_scopes`, `TestHardening.test_case_alias_paths_conflict` |
| Every non-`PURE` node declares a resource | create and semantic validation gates | `TestHardening.test_non_pure_requires_resources` |
| Generation compare-and-set prevents stale writes | `mutate` generation check and lock directory | `TestGenerations`, `TestProcessConcurrency.test_real_process_generation_race` |
| Known terminal outcomes require consistent receipts | `validate_receipt` and state-edge receipt gates | `TestReceipts`, `TestHardening.test_out_of_scope_receipt_path_is_rejected` |
| `UNKNOWN` remains immutable and freezes unsafe work | unresolved-unknown scans and reconciliation rules | `TestAmbiguousCreation`, `TestHardening.test_reconciliation_is_monotonic_and_outcome_compatible` |
| Future or mixed versions fail closed | protocol/schema checks plus packaged-reference scan | `TestVersioning`, `TestReferenceSet` |
| Host claims are explicit and surfaced | `capability_profile`, `init`, `show` | `TestCapabilities` and CLI profile assertions |
| Git HEAD/dirty drift is machine-detected | `capture_git_baseline`, `verify_git_baseline` | `TestGitBaseline` |
| Corrupt/adversarial JSON is never accepted as state | bounded no-follow JSON loader and structural validation | `TestCorruption`, `TestHardening` |
| Atomic replacement preserves prior canonical on write failure | same-directory temp, file fsync, `os.replace`, best-effort parent fsync | `TestRecovery.test_atomic_replace_failure_preserves_canonical`, `test_recovery_interrupted_write` |
| Runtime ledgers and secrets do not ship | repository hygiene tests and `.gitignore` | `TestRepoHygiene` |
| Protocol/docs/actions stay synchronized | repository-wide consistency and link checks | `TestRepoHygiene.test_release_contract_consistency`, `test_internal_markdown_links_resolve` |

## Reproduce

```sh
python3 -m unittest discover -s tests -v
python3 .agents/skills/gpt-5-6-swarm/scripts/swarm_ledger.py --help
python3 .agents/skills/gpt-5-6-swarm/scripts/swarm_ledger.py verify-reference-set
```

Coverage is a separate development gate, not a runtime dependency:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage combine
python3 -m coverage report
```

The ledger remains Python-standard-library-only. `coverage` is used only to measure the tests in CI.

