# Optional frozen contracts

A frozen contract turns the coordinator's preflight graph into immutable JSON
before worker creation. It is optional: ordinary ledger use remains valid.
When enabled, `create-node --frozen-contract` refuses any node whose class,
route request, outcome, gate, dependencies, join, resources, or base revision
differs from the frozen plan. The contract SHA-256 becomes the node's
`inputs_digest`, so the existing task fingerprint binds the plan. The first
bound node also creates an immutable run sidecar under `.swarm/runs/<run-id>/`;
later node creation refuses a different contract or an unbound node. Contract
mode cannot be enabled after an unbound node already exists.

This is a coordinator consistency gate, not an external lock. It does not
prove the host selected a model, prevent an uncooperative writer, grant
authority, or make a one-shot effect safe.

## Workflow

```sh
S=plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/scripts
python3 "$S/swarm_contract.py" freeze \
  examples/contract-draft.example.json --output /tmp/frozen-contract.json
python3 "$S/swarm_contract.py" validate /tmp/frozen-contract.json

python3 "$S/swarm_ledger.py" create-node \
  --root . --run-id example-run --expect-generation 1 \
  --node-id scan --class PURE --model gpt-5.6-luna --effort low \
  --outcome "Map the implementation boundary" \
  --base-revision example-revision \
  --gate "evidence report identifies interfaces and risks" \
  --launch-nonce nonce-example-scan-1 \
  --frozen-contract /tmp/frozen-contract.json
```

`freeze` rejects duplicate keys, extra fields, noncanonical paths/resources,
cycles, invalid joins, `PURE` mutation scopes, mutation nodes without scopes,
protected-path ownership, and overlapping resources between independent nodes.
Dense graphs use bounded polynomial-time cycle/reachability checks. Overlap is
allowed only when dependency order serializes the owners.

After a worker returns a diff, audit each changed path against that node's
frozen path ownership:

```sh
python3 "$S/swarm_contract.py" audit-paths /tmp/frozen-contract.json \
  --node-id build --path src/api.py --path src/tests/test_api.py
```

The audit accepts only canonical repository-relative paths inside a declared
`path:` resource. The informational schema is
[frozen-contract.schema.json](../schema/frozen-contract.schema.json); the
executable Python validator is authoritative.
