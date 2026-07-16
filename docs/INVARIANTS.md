# Invariant-to-test traceability

This map is the public evidence index for protocol `1.4.0`, schema `2`, and tool `0.4.0`. Test names are stable review handles; GitHub Actions is the execution record. Passing tests demonstrate the tools' recorded-control-plane behavior under the tested conditions. Named local artifact bytes are recomputed where specified; tests do not prove host behavior, command execution, undeclared writes, operator identity, external effects, or illustrative benchmark numbers.

| Invariant | Executable mechanism | Primary regression evidence |
| --- | --- | --- |
| Legal state transitions; terminal states immutable | `LEGAL_TRANSITIONS`, edge guards, `op_transition` | `TestIllegalTransitions.test_illegal_transition_rejected`, `test_terminal_states_are_terminal`, `test_unknown_cannot_be_un_unknowned` |
| Normalized task deduplication | `compute_fingerprint`, blocking-state scan | `TestFingerprintDedup` |
| Launch nonces unique across the run | nonce registries and `op_create_node` | `TestNonces.test_duplicate_launch_nonce`, `test_arm_nonce_cannot_reuse_launch_nonce_namespace` |
| One recorded dispatch per attempt | `op_record_dispatch` and transition guards | `TestAmbiguousCreation`, `TestCli.test_cli_end_to_end` |
| One-shot arm nonce is single-use | `op_record_arm_dispatch`, arm transition guards | `TestOneShot.test_one_shot_double_arm_rejected`, `test_ambiguous_arm_delivery_freezes_forever` |
| One-shot needs target/fresh-output fence evidence | `one_shot_fence` capability gate | `TestOneShot.test_one_shot_requires_declared_fence` |
| One-shot needs fresh task-bound operator evidence | `validate_one_shot_authorization`, authorization nonce registry | `TestOneShot.test_one_shot_requires_fresh_task_bound_authorization`, `test_authorization_nonce_is_single_use` |
| One active owner per node/thread | semantic validation and thread-lineage rules | `TestHostile.test_two_owners_rejected`, `test_two_owner_hand_edit_detected_by_validate` |
| Declared path scopes cannot overlap while held | normalized resource scopes and prefix conflict checks | `TestResources.test_conflicting_resource_scopes`, `TestHardening.test_case_alias_paths_conflict` |
| Every non-`PURE` node declares a resource | create and semantic validation gates | `TestHardening.test_non_pure_requires_resources` |
| Generation compare-and-set prevents stale writes | `mutate` generation check and lock directory | `TestGenerations`, `TestProcessConcurrency.test_real_process_generation_race` |
| Known terminal outcomes require consistent receipts | `validate_receipt` and state-edge receipt gates | `TestReceipts`, `TestHardening.test_out_of_scope_receipt_path_is_rejected` |
| Path-scoped success requires real matching bytes | `verify_receipt_artifacts`, `verify-artifacts`, transition gate | `TestReceipts.test_artifact_hashes_are_recomputed_not_self_attested`, `test_verify_artifacts_read_only_command_contract` |
| `UNKNOWN` remains immutable and freezes unsafe work | unresolved-unknown scans and reconciliation rules | `TestAmbiguousCreation`, `TestHardening.test_reconciliation_is_monotonic_and_outcome_compatible` |
| Future or mixed versions fail closed | protocol/schema checks plus packaged-reference scan | `TestVersioning`, `TestReferenceSet` |
| Host claims are explicit and surfaced | `capability_profile`, `init`, `show` | `TestCapabilities` and CLI profile assertions |
| Git HEAD/dirty drift is machine-detected | `capture_git_baseline`, `verify_git_baseline` | `TestGitBaseline` |
| Relevant ignored-file drift is optionally detected | bounded ignored-byte digest | `TestGitBaseline.test_ignored_content_digest_detects_invisible_drift` |
| Resource symlink swaps freeze launch | `verify_resource_bindings` at claim/launch | `TestResources.test_symlink_swap_cannot_rebind_claimed_resource` |
| Both ledger/journal crash windows recover safely | intent/commit WAL classification and repair | `TestAtomicPersistence`, `TestHardening.test_torn_journal_tail_is_safely_auto_repaired` |
| Resume advice is conservative and state-bound | `doctor` badge/manifest/resume token | `TestDoctor.test_doctor_reports_badge_artifacts_and_resume_token` |
| Status HTML is escaped, deterministic, offline, and exact-file atomic | `render_status_html`, `write_status_html`, `render-status` | `TestDoctor.test_status_html_is_escaped_deterministic_and_atomic`, `TestCli.test_cli_end_to_end` |
| Optional frozen contracts bind exact node/base/ownership fields | `swarm_contract.py`, `create-node --frozen-contract`, fingerprint `inputs_digest` | `TestFrozenContract`, `TestFrozenContractBinding` |
| Independent frozen write scopes cannot overlap | contract graph reachability and resource-prefix checks | `TestFrozenContract.test_resource_overlap_requires_dependency_order` |
| Benchmark speed uses gate-passing paired evidence and retains failures | `swarm_benchmark.py compare` | `TestSwarmBenchmark.test_examples_validate_and_report_replays`, `test_failures_and_missing_arms_remain_visible` |
| Issued peak is not relabeled observed; missing telemetry/usage stays unknown | benchmark trial validator and report coverage | `TestSwarmBenchmark.test_trial_evidence_truth_guards`, `test_known_usage_and_observed_telemetry_are_explicit` |
| Conservative concurrency defaults remain 4 read-only / 3 isolated / 1 shared, unknown host peak 3 | `SCHEDULING.md`, `ROUTES.md`, hygiene guard | `TestRepoHygiene.test_conservative_concurrency_defaults_are_preserved` |
| Marketplace has one canonical packaged skill with preserved credit | marketplace/plugin tree and hygiene guards | `TestRepoHygiene.test_marketplace_plugin_tree_is_single_source_and_valid`, `test_release_contract_consistency` |
| Specialist profiles cannot delegate, mutate the ledger, or silently pin routes | project `.codex/agents` profiles | `TestRepoHygiene.test_project_agent_profiles_are_narrow_and_portable` |
| Corrupt/adversarial JSON is never accepted as state | bounded no-follow JSON loader and structural validation | `TestCorruption`, `TestHardening` |
| Atomic replacement preserves prior canonical on write failure | same-directory temp, file fsync, `os.replace`, best-effort parent fsync | `TestRecovery.test_atomic_replace_failure_preserves_canonical`, `test_recovery_interrupted_write` |
| Runtime ledgers and secrets do not ship | repository hygiene tests and `.gitignore` | `TestRepoHygiene` |
| Protocol/docs/actions stay synchronized | repository-wide consistency and link checks | `TestRepoHygiene.test_release_contract_consistency`, `test_internal_markdown_links_resolve` |

## Reproduce

```sh
python3 -m unittest discover -s tests -v
python3 plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/scripts/swarm_ledger.py --help
python3 plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/scripts/swarm_ledger.py verify-reference-set
```

Coverage is a separate development gate, not a runtime dependency:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage combine
python3 -m coverage report
```

All shipped runtime tools remain Python-standard-library-only. `coverage` is used only to measure the tests in CI.
