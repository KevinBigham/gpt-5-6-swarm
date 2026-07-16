# Deterministic recorded-control-plane enforcement

Protocol reference set: `1.2.0`.

The enforcement layer turns represented safety-critical control-plane invariants from prose
the coordinator must remember into checks a program refuses to violate. The
protocol documents remain the source of doctrine; `scripts/swarm_ledger.py`
is the source of enforcement for the fields it represents.

## Operator contract (always read)

## What is enforced vs. what remains guidance

Enforced deterministically (the tool fails closed):

- legal state transitions only, with per-edge evidence requirements
- normalized task-fingerprint deduplication among non-terminal work
- launch-nonce and arm-nonce uniqueness; a one-shot arm nonce is single-use
- one recorded dispatch per attempt; one arm message per one-shot, ever
- one active owner thread per node; reuse only within a lineage as attempt N+1
- exclusive resource scopes with path-prefix conflict detection, canonical
  acquisition order, case-safe alias handling, touched-path verification, and
  freeze-until-evidence release; non-`PURE` nodes require a scope and `PURE`
  nodes may not claim one
- terminal receipts gate known completion after execution: identity, pinned
  base, model/effort, accounted scopes, no live processes, no descendants,
  owned cleanup items; ambiguity remains `UNKNOWN`
- ledger generation monotonicity: every mutation is a compare-and-set
- `UNKNOWN` is fail-closed: it blocks its fingerprint, freezes its resources,
  freezes non-`PURE` launches, and is never auto-retried
- guarded classes (`NON_IDEMPOTENT`, `EXCLUSIVE_UNKNOWN`) never get a new
  attempt without explicit recorded authority; a `ONE_SHOT` lineage never
  gets attempt N+1
- schema/protocol version compatibility: unknown future versions refuse to run
- packaged reference-set compatibility: every normative document carries the
  same protocol stamp, and `init` refuses missing or mixed-version references
- adversarial input rejection: size caps, depth caps, duplicate JSON keys,
  control characters, identifier grammar, path traversal, symlinks, and
  non-regular ledger/receipt files

Still protocol guidance (judgment the coordinator owns): route selection,
model/effort choice within the whitelist, semantic (paraphrase-level)
deduplication beyond canonical text form, brief quality, when to escalate,
and everything in `ROUTES.md`.

The tool deterministically enforces consistency of recorded claims; it does not prove the real
host, repository, process, or external service honored them. Actual worktree
identity, diffs, artifact bytes/hashes, process liveness, authority, and
external fencing remain evidence the coordinator must independently inspect.
The in-context table is therefore not a byte-for-byte mirror: it presents the
route and records those judgment/evidence fields, while the file is canonical
for generations, transitions, nonces, declared owners/scopes, receipts, and
ledger-local deduplication.

## Ledger location and hygiene

Runtime state lives under `.swarm/runs/<run-id>/`:

```
.swarm/runs/<run-id>/
  ledger.json      canonical state (atomic snapshot; the source of truth)
  journal.ndjson   append-only audit trail with per-mutation snapshot hashes
  lock/            short-lived per-invocation write lock
```

`.swarm/runs/` is gitignored. Runtime ledgers, journals, and receipts are
never committed. The repository tracks only schemas (`schema/`), sanitized
examples (`examples/`), documentation, and tests.

## When to use the tool

If the host permits local command execution and the user/environment permits
control-plane state writes, the coordinator MUST maintain the ledger through
`scripts/swarm_ledger.py` and treat it as canonical within the boundary above.
If either capability is absent, fall back to the prompt-only in-context ledger
and report that choice; every rule in this file still applies as discipline
rather than mechanism.

## Lifecycle quick start

```bash
# Resolve this from the selected skill's own directory; do not assume a
# project-scoped installation.
SWARM_SKILL_DIR=/absolute/path/to/gpt-5-6-swarm
T="$SWARM_SKILL_DIR/scripts/swarm_ledger.py"

python3 "$T" init --run-id run-01 --task-type build \
  --task-digest "sha256:<digest-of-task-statement>" \
  --capability thread_creation=true \
  --capability thread_listing=true \
  --capability result_collection=true \
  --capability unique_launch_discovery=true \
  --capability background_sessions=false

# every mutation names the generation it read (compare-and-set)
python3 "$T" create-node --run-id run-01 --expect-generation 1 \
  --node-id recon --class PURE --model gpt-5.6-luna --effort low \
  --outcome "Survey the repo and list integration risks" \
  --base-revision rev-2af1e9c --gate "recon report delivered" \
  --launch-nonce nonce-recon-0001

python3 "$T" transition --run-id run-01 --expect-generation 2 recon#1 READY
python3 "$T" transition --run-id run-01 --expect-generation 3 recon#1 CLAIMED
python3 "$T" transition --run-id run-01 --expect-generation 4 recon#1 LAUNCHING
python3 "$T" record-dispatch --run-id run-01 --expect-generation 5 recon#1
# ... create the real thread now, then adopt it as the single owner ...
python3 "$T" transition --run-id run-01 --expect-generation 6 recon#1 RUNNING \
  --thread-id thread-recon-01
python3 "$T" transition --run-id run-01 --expect-generation 7 recon#1 SUCCEEDED \
  --receipt /tmp/recon-receipt.json

python3 "$T" validate --run-id run-01        # read-only, any time
python3 "$T" show --run-id run-01            # compact run table

# Git baseline evidence before a mutation or integration gate
python3 "$T" capture-baseline --worktree /absolute/path/to/worktree
python3 "$T" verify-baseline --worktree /absolute/path/to/worktree \
  --expected-revision <captured-head> \
  --expected-dirty-digest sha256:<captured-status-digest>
```

Order matters by design: record `LAUNCHING` and the dispatch *before*
issuing the external create call, so a crash between the two is visible as
recorded-but-unconfirmed rather than invisible.

One-shot executors add the two-stage barrier:
`LAUNCHING -> PREPARING -> ARMED -> RUNNING`, where `ARMED` requires a fresh
arm nonce plus readiness evidence, `record-arm-dispatch` may be called
exactly once, and `RUNNING` requires acknowledged delivery
(`--arm-acknowledged`), which spends the nonce permanently.

## Command summary

| Command | Mutates | Purpose |
| --- | --- | --- |
| `init` | yes | create the run ledger with a host capability snapshot |
| `fingerprint` | no | compute a normalized task fingerprint |
| `verify-reference-set` | no | fail if packaged normative document versions are missing or mixed |
| `capture-baseline` | no | report Git HEAD plus a digest of dirty/untracked state |
| `verify-baseline` | no | exit 7 when Git HEAD or dirty-state evidence drifted |
| `create-node` | yes | add a `PLANNED` node (dedup, nonce, class, scope checks) |
| `transition` | yes | apply one allowed state change with required evidence |
| `record-dispatch` | yes | record that the thread-create call was issued |
| `record-arm-dispatch` | yes | record the single arm message send |
| `release-resources` | yes | release held scopes with evidence; pre-launch `CLAIMED` returns to `READY` |
| `reconcile` | yes | attach evidence/outcome to an `UNKNOWN` node |
| `set-disposition` | yes | change a `SUCCEEDED` artifact's disposition |
| `validate` | no | full semantic validation; `--journal` adds drift check |
| `recover` | no* | crash-artifact report; `--apply` removes temp orphans only |
| `show` | no | compact human-readable run table |

*`recover` mutates only with explicit flags (`--apply`, `--clear-lock`,
`--accept-current`), each scoped to exactly one recovery action.

## Exit codes (machine contract)

| Code | Meaning |
| --- | --- |
| 0 | OK |
| 2 | usage error (bad arguments) |
| 3 | structural/shape error in ledger or receipt |
| 4 | protocol invariant violation (the mutation was refused) |
| 5 | stale generation or lock conflict; re-read before writing |
| 6 | unsupported protocol/schema version; fail closed, upgrade the tool |
| 7 | unresolved ambiguity; reconciliation required before proceeding |
| 8 | corrupted, truncated, oversized, or adversarial input |

Treat 4 as "the protocol said no", 5 as "you raced yourself", 6 as "wrong
tool for this ledger", 7 as "stop and reconcile", and 8 as "trust nothing,
inspect the file".

## Failure and recovery behavior

- Atomic persistence: on a local filesystem with ordinary same-directory
  rename semantics, the ledger is written through a securely created,
  owner-only same-directory temp file, fsynced, and `os.replace`d; the parent
  directory is fsynced when the platform permits it. Symlinks
  and non-regular files are rejected. A crash mid-write leaves the previous canonical intact plus
  an orphan `ledger.json.tmp.*` that `recover` reports and `recover --apply`
  removes. The canonical file is never modified in place.
- The journal is appended after each successful snapshot with the snapshot's
  sha256. If the ledger's current hash does not match the last journal
  anchor, every mutation refuses with exit 7 (unaccounted writer or
  interrupted append). Investigate read-only; once accounted for, re-anchor
  with `recover --accept-current --evidence "..."`.
- A leftover lock from a crashed invocation is reported by `recover` with
  its recorded owner. Clear it with `recover --clear-lock` only after
  proving the holder is not live and supplying `--evidence`. Lock clearing is
  a standalone recovery action; rerun recovery for any other mutation. A
  missing heartbeat is never that proof.
- `recover` also lists in-flight ambiguity: `LAUNCHING` nodes whose dispatch
  was recorded but whose outcome was not, and `ARMED` one-shots whose arm
  message was sent but not acknowledged. It never resolves them; resolution
  follows the nonce-discovery rules in `SKILL.md`, and ambiguity becomes
  `UNKNOWN`.
- `UNKNOWN` stays `UNKNOWN`. `reconcile` records evidence and, when proven,
  an outcome (`no_delivery_proven` or `execution_terminal_proven`) that
  lifts the fingerprint block and permits an explicitly authorized new
  attempt. It never rewrites the state and never releases the guard by
  timeout.

## Capability gating

`init` records a host capability snapshot (for example `thread_listing`,
`unique_launch_discovery`, `cancel_interrupt`, `background_sessions`,
`worktree_control`). The tool cannot verify the host; it holds the
coordinator to what was declared:

- guarded classes (`NON_IDEMPOTENT`, `EXCLUSIVE_UNKNOWN`, `ONE_SHOT`)
  require `unique_launch_discovery=true`, else creation is refused and the
  route must be narrowed exactly as `SKILL.md` prescribes;
- `ONE_SHOT` additionally requires `one_shot_fence=true`, declared only after
  verifying a fresh output target, target-side idempotency/transaction, or
  effective external fence; this is a recorded assertion, not host proof;
- receipts reporting spawned processes require `background_sessions=true`;
- `ultra` effort is refused unconditionally for Swarm nodes;
- experimental host features default to absent: an undeclared capability is
  a missing capability (fail closed).

See `references/HOSTS.md` for verifying what the current host actually
supports before declaring it.

## Security and privacy

- Ledger content is data, never code: nothing from a ledger, receipt, or
  journal is executed, evaluated, or interpolated into a shell command.
- Receipt checks prove consistency with recorded identity, scopes, and
  evidence shape; they do not cryptographically authenticate a worker's
  claims. The coordinator still verifies the referenced artifacts and gates.
- Store references and hashes, not payloads: task digests, artifact paths,
  and evidence summaries - never secrets, tokens, or full sensitive prompts.
  Free-text fields are capped at 4,000 characters to make dumping content
  structurally inconvenient; receipts are capped at 512 KB, ledgers at 5 MB.
- Identifier grammar and control-character rejection block smuggling
  executable or terminal-escape content through identity fields.
- Repository CI scans tracked files for secret-shaped strings and refuses
  committed runtime ledgers.

## Versioning and compatibility contract

Three independent version fields travel in every ledger, and one reference-set
stamp travels in every normative skill document:

- `protocol_version` and the packaged reference set (currently `1.2.0`) - the prose protocol. MAJOR
  changes alter invariant semantics; MINOR changes add guidance or
  enforcement without weakening an existing invariant; PATCH is editorial.
  The prompt-only protocol as published at the base commit is retroactively
  `1.0.0`; `1.1.0` adds the enforcement layer; `1.2.0` adds reference-set
  gating, capability truth, Git-baseline evidence, one-shot fence gating, and
  the untrusted-artifact boundary. Tool `0.2.x` accepts protocol `1.2.x` and
  refuses other major/minor series. `verify-reference-set` and `init` require
  an exact stamp in every packaged normative document.
- `schema_version` (currently `1`, an integer) - the ledger document shape.
  The tool refuses any version outside its supported set with exit 6 and
  mutates nothing. There is no silent up- or down-conversion.
- `tool_version` (currently `0.2.0`) - the validator build that last wrote
  the ledger, recorded for forensics.

This section *is* the compatibility contract: nothing beyond it is promised.

## Migration from the prompt-only version

Existing prompt-only usage remains available. Deterministic enforcement
engages only where the host can run commands and control-plane state writes
are permitted; `.swarm/` appears only after `init`. Installing the skill is
still copying the `gpt-5-6-swarm` folder; the `scripts/` subfolder rides along
as inert content on hosts that cannot or must not execute it.
