# Frozen execution contracts

Protocol reference set: `1.4.0`.

Use a frozen contract when the task benefits from locking the preflight graph
before dispatch. It is optional and additive; the live enforcement ledger
remains canonical for state, identity, nonces, resources, receipts, and
reconciliation.

1. Author a draft with run/task/base identity, sorted protected paths, and
   sorted nodes. Each node records the exact class, requested model/effort,
   outcome, gate, dependencies, join, and resources.
2. Run `scripts/swarm_contract.py freeze DRAFT --output CONTRACT` before the
   first worker creation. Inspect and retain the printed SHA-256.
3. Pass `--frozen-contract CONTRACT` to every bound `create-node`. The ledger
   refuses mismatched fields and uses the contract digest as `inputs_digest`,
   binding it into the existing task fingerprint. The first node atomically
   selects one immutable run sidecar; all later nodes must supply the same
   contract, and contract mode cannot begin after an unbound node.
4. After a mutating worker returns, run `swarm_contract.py audit-paths` for all
   changed paths before accepting its receipt.

The validator uses bounded polynomial-time dependency closure and rejects
cycles, invalid joins, duplicate/extra fields,
noncanonical paths/resources, protected-path claims, `PURE` mutation scopes,
mutation nodes without scopes, and resource overlap between independent nodes.
Dependency-ordered nodes may overlap because their execution is serialized.

A contract is recorded consistency, not authority or a real fence. It cannot
prove host routing, stop an uncooperative process, lock shared Git state, or
make an external effect exactly once. One-shot and external work still require
all barriers in `CONCURRENCY.md`.
