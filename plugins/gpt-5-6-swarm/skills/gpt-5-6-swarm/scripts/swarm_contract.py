#!/usr/bin/env python3
"""Freeze and verify optional GPT-5.6 Swarm execution contracts.

The contract is an immutable, coordinator-owned plan. It supplements the live
ledger: it does not create threads, grant authority, or lock external writers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import PurePosixPath


CONTRACT_VERSION = 1
FORMAT = "gpt-5-6-swarm-frozen-contract"
MAX_BYTES = 512_000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
CLASSES = {
    "PURE", "ISOLATED", "KEYED_IDEMPOTENT", "NON_IDEMPOTENT",
    "EXCLUSIVE_UNKNOWN", "ONE_SHOT",
}
EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
CONTRACT_KEYS = {
    "contract_version", "run_id", "task_digest", "base_revision",
    "protected_paths", "nodes",
}
NODE_KEYS = {
    "node_id", "class", "model", "effort", "outcome", "gate",
    "dependencies", "join", "resources",
}


class ContractError(Exception):
    pass


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON key: " + str(key))
        result[key] = value
    return result


def _read_json(path):
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ContractError("cannot inspect input: " + str(exc))
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("input must be a regular, non-symlink file")
    if info.st_size > MAX_BYTES:
        raise ContractError("input exceeds 512 KB")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("invalid JSON input: " + str(exc))


def _text(value, label, maximum=4000):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContractError(label + " must be a non-empty bounded string")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise ContractError(label + " contains control characters")
    return value


def canonical_path(value, label="path"):
    _text(value, label, 1000)
    raw = value.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ContractError(label + " must be repository-relative")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ContractError(label + " is not canonical")
    normalized = "/".join(parts).rstrip("/")
    if normalized != raw.rstrip("/"):
        raise ContractError(label + " is not canonical")
    return normalized


def canonical_resource(value):
    _text(value, "resource", 1200)
    if ":" not in value:
        raise ContractError("resource must use type:id")
    kind, identity = value.split(":", 1)
    if not RESOURCE_TYPE_RE.fullmatch(kind):
        raise ContractError("resource type is invalid: " + kind)
    if kind == "path":
        identity = canonical_path(identity, "path resource")
    else:
        _text(identity, "resource id", 1000)
    canonical = kind + ":" + identity
    if canonical != value:
        raise ContractError("resource is not canonical: " + value)
    return canonical


def _join_valid(value, dependency_count):
    if not isinstance(value, str):
        return False
    if value in {"all", "any"}:
        return True
    match = re.fullmatch(r"quorum:([1-9][0-9]*)", value or "")
    return bool(match and int(match.group(1)) <= dependency_count)


def _path_overlap(left, right):
    return left == right or left.startswith(right + "/") or \
        right.startswith(left + "/")


def _resource_conflict(left, right):
    left_kind, left_id = left.split(":", 1)
    right_kind, right_id = right.split(":", 1)
    if left_kind != right_kind:
        return False
    if left_kind == "path":
        return _path_overlap(left_id, right_id)
    return left_id == right_id


def _dependency_closure(graph):
    """Return transitive dependencies in bounded polynomial time.

    Kahn ordering rejects cycles first; closure sets are then built once in
    dependency-first order. This avoids exponential path enumeration on dense
    but valid DAGs.
    """
    children = {node_id: [] for node_id in graph}
    remaining = {node_id: len(dependencies)
                 for node_id, dependencies in graph.items()}
    for node_id, dependencies in graph.items():
        for dependency in dependencies:
            children[dependency].append(node_id)
    ready = sorted(node_id for node_id, count in remaining.items()
                   if count == 0)
    order = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for child in sorted(children[node_id]):
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
                ready.sort()
    if len(order) != len(graph):
        raise ContractError("dependency cycle detected")
    closure = {}
    for node_id in order:
        reachable = set(graph[node_id])
        for dependency in graph[node_id]:
            reachable.update(closure[dependency])
        closure[node_id] = reachable
    return closure


def validate_contract(contract):
    if not isinstance(contract, dict) or set(contract) != CONTRACT_KEYS:
        raise ContractError("contract fields must be exactly: " +
                            ", ".join(sorted(CONTRACT_KEYS)))
    if isinstance(contract["contract_version"], bool) or \
            contract["contract_version"] != CONTRACT_VERSION:
        raise ContractError("unsupported contract_version")
    if not ID_RE.fullmatch(_text(contract["run_id"], "run_id", 128)):
        raise ContractError("run_id is invalid")
    _text(contract["task_digest"], "task_digest", 256)
    _text(contract["base_revision"], "base_revision", 256)
    protected = contract["protected_paths"]
    if not isinstance(protected, list):
        raise ContractError("protected_paths must be a unique array")
    for index, path in enumerate(protected):
        canonical_path(path, "protected_paths[{}]".format(index))
    if len(protected) != len(set(protected)):
        raise ContractError("protected_paths must be a unique array")
    if protected != sorted(protected):
        raise ContractError("protected_paths must be sorted")

    nodes = contract["nodes"]
    if not isinstance(nodes, list) or not nodes or len(nodes) > 128:
        raise ContractError("nodes must contain 1..128 entries")
    by_id = {}
    for index, node in enumerate(nodes):
        label = "nodes[{}]".format(index)
        if not isinstance(node, dict) or set(node) != NODE_KEYS:
            raise ContractError(label + " has unexpected or missing fields")
        node_id = _text(node["node_id"], label + ".node_id", 128)
        if not ID_RE.fullmatch(node_id) or node_id in by_id:
            raise ContractError(label + ".node_id is invalid or duplicate")
        if not isinstance(node["class"], str) or node["class"] not in CLASSES:
            raise ContractError(label + ".class is invalid")
        _text(node["model"], label + ".model", 128)
        if not isinstance(node["effort"], str) or node["effort"] not in EFFORTS:
            raise ContractError(label + ".effort is invalid")
        _text(node["outcome"], label + ".outcome")
        _text(node["gate"], label + ".gate")
        dependencies = node["dependencies"]
        if not isinstance(dependencies, list):
            raise ContractError(label + ".dependencies must be unique")
        for dependency in dependencies:
            if not isinstance(dependency, str) or not ID_RE.fullmatch(
                    _text(dependency, label + ".dependency", 128)):
                raise ContractError(label + ".dependency is invalid")
        if len(dependencies) != len(set(dependencies)):
            raise ContractError(label + ".dependencies must be unique")
        if dependencies != sorted(dependencies):
            raise ContractError(label + ".dependencies must be sorted")
        if not _join_valid(node["join"], len(dependencies)):
            raise ContractError(label + ".join is invalid")
        resources = node["resources"]
        if not isinstance(resources, list):
            raise ContractError(label + ".resources must be unique")
        for resource in resources:
            canonical_resource(resource)
        if len(resources) != len(set(resources)):
            raise ContractError(label + ".resources must be unique")
        if resources != sorted(resources):
            raise ContractError(label + ".resources must be sorted")
        if node["class"] == "PURE" and resources:
            raise ContractError("PURE nodes may not own mutation resources")
        if node["class"] != "PURE" and not resources:
            raise ContractError("non-PURE nodes require mutation resources")
        by_id[node_id] = node

    if [node["node_id"] for node in nodes] != sorted(by_id):
        raise ContractError("nodes must be sorted by node_id")
    graph = {node_id: list(node["dependencies"])
             for node_id, node in by_id.items()}
    for node_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in graph or dependency == node_id:
                raise ContractError(node_id + " has an invalid dependency")
    closure = _dependency_closure(graph)

    node_ids = sorted(by_id)
    for index, left_id in enumerate(node_ids):
        left = by_id[left_id]
        for right_id in node_ids[index + 1:]:
            right = by_id[right_id]
            ordered = left_id in closure[right_id] or \
                right_id in closure[left_id]
            if ordered:
                continue
            for left_resource in left["resources"]:
                for right_resource in right["resources"]:
                    if _resource_conflict(left_resource, right_resource):
                        raise ContractError(
                            "independent nodes {} and {} have overlapping "
                            "resources".format(left_id, right_id))
    for node_id, node in by_id.items():
        for resource in node["resources"]:
            if not resource.startswith("path:"):
                continue
            identity = resource.split(":", 1)[1]
            for protected_path in protected:
                if _path_overlap(identity, protected_path):
                    raise ContractError(
                        node_id + " overlaps protected path " + protected_path)
    return contract


def canonical_contract_bytes(contract):
    validate_contract(contract)
    return json.dumps(contract, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def freeze_contract(contract):
    payload = canonical_contract_bytes(contract)
    return {
        "format": FORMAT,
        "contract_sha256": hashlib.sha256(payload).hexdigest(),
        "contract": contract,
    }


def validate_frozen_contract(envelope):
    if not isinstance(envelope, dict) or set(envelope) != {
            "format", "contract_sha256", "contract"}:
        raise ContractError("frozen contract envelope fields are invalid")
    if envelope["format"] != FORMAT:
        raise ContractError("unsupported frozen contract format")
    expected = hashlib.sha256(
        canonical_contract_bytes(envelope["contract"])).hexdigest()
    if envelope["contract_sha256"] != expected:
        raise ContractError("contract digest mismatch")
    return envelope


def load_frozen_contract(path):
    return validate_frozen_contract(_read_json(path))


def assert_node_matches(envelope, *, node_id, klass, model, effort, outcome,
                        gate, base_revision, dependencies, join, resources,
                        run_id=None, task_digest=None):
    validate_frozen_contract(envelope)
    contract = envelope["contract"]
    if run_id is not None and contract["run_id"] != run_id:
        raise ContractError("run_id does not match contract")
    if task_digest is not None and contract["task_digest"] != task_digest:
        raise ContractError("task_digest does not match contract")
    if contract["base_revision"] != base_revision:
        raise ContractError("node base_revision does not match contract")
    matches = [node for node in contract["nodes"]
               if node["node_id"] == node_id]
    if len(matches) != 1:
        raise ContractError("node_id is absent from frozen contract")
    actual = {
        "node_id": node_id, "class": klass, "model": model,
        "effort": effort, "outcome": outcome, "gate": gate,
        "dependencies": list(dependencies), "join": join,
        "resources": list(resources),
    }
    if matches[0] != actual:
        raise ContractError("node arguments do not match frozen contract")
    return envelope["contract_sha256"]


def audit_paths(envelope, node_id, paths):
    validate_frozen_contract(envelope)
    nodes = {node["node_id"]: node for node in envelope["contract"]["nodes"]}
    if node_id not in nodes:
        raise ContractError("node_id is absent from frozen contract")
    allowed = [resource.split(":", 1)[1]
               for resource in nodes[node_id]["resources"]
               if resource.startswith("path:")]
    checked = []
    for raw in paths:
        path = canonical_path(raw, "changed path")
        if not any(path == scope or path.startswith(scope + "/")
                   for scope in allowed):
            raise ContractError(path + " is outside the node's write scope")
        checked.append(path)
    return {"node_id": node_id, "checked_paths": checked,
            "contract_sha256": envelope["contract_sha256"]}


def _write_json_atomic(path, payload):
    parent = os.path.abspath(os.path.dirname(path) or ".")
    os.makedirs(parent, exist_ok=True)
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ContractError("output must be a regular, non-symlink file")
    fd, temporary = tempfile.mkstemp(prefix=".swarm-contract-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Freeze and verify optional GPT-5.6 Swarm contracts.")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("draft")
    freeze.add_argument("--output")
    validate = sub.add_parser("validate")
    validate.add_argument("contract")
    audit = sub.add_parser("audit-paths")
    audit.add_argument("contract")
    audit.add_argument("--node-id", required=True)
    audit.add_argument("--path", action="append", default=[])
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if args.command == "freeze":
            envelope = freeze_contract(_read_json(args.draft))
            if args.output:
                _write_json_atomic(args.output, envelope)
            else:
                print(json.dumps(envelope, indent=2, sort_keys=True))
        elif args.command == "validate":
            envelope = load_frozen_contract(args.contract)
            print(json.dumps({
                "contract_sha256": envelope["contract_sha256"],
                "nodes": len(envelope["contract"]["nodes"]),
                "ok": True,
            }, indent=2, sort_keys=True))
        elif args.command == "audit-paths":
            print(json.dumps(audit_paths(
                load_frozen_contract(args.contract), args.node_id, args.path),
                indent=2, sort_keys=True))
        return 0
    except ContractError as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
