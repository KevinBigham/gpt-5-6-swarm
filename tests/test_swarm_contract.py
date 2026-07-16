"""Offline adversarial tests for the optional frozen-contract gate."""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (REPO_ROOT / "plugins" / "gpt-5-6-swarm" / "skills" /
             "gpt-5-6-swarm" / "scripts" / "swarm_contract.py")
spec = importlib.util.spec_from_file_location("swarm_contract", TOOL_PATH)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


class TestFrozenContract(unittest.TestCase):
    def setUp(self):
        self.draft = json.loads((REPO_ROOT / "examples" /
            "contract-draft.example.json").read_text("utf-8"))

    def expect_error(self, message, value):
        with self.assertRaises(sc.ContractError) as ctx:
            sc.validate_contract(value)
        self.assertIn(message, str(ctx.exception))

    def test_examples_freeze_and_validate(self):
        envelope = sc.freeze_contract(self.draft)
        expected = json.loads((REPO_ROOT / "examples" /
            "frozen-contract.example.json").read_text("utf-8"))
        self.assertEqual(envelope, expected)
        self.assertEqual(sc.validate_frozen_contract(expected), expected)
        tampered = copy.deepcopy(expected)
        tampered["contract"]["task_digest"] = "changed"
        with self.assertRaises(sc.ContractError):
            sc.validate_frozen_contract(tampered)

    def test_cycles_and_dependencies_fail_closed(self):
        draft = copy.deepcopy(self.draft)
        draft["nodes"][1]["dependencies"] = ["verify"]
        self.expect_error("cycle", draft)
        draft = copy.deepcopy(self.draft)
        draft["nodes"][1]["dependencies"] = ["missing"]
        self.expect_error("invalid dependency", draft)
        draft = copy.deepcopy(self.draft)
        draft["nodes"][1]["join"] = "quorum:1"
        self.expect_error("join", draft)
        draft = copy.deepcopy(self.draft)
        draft["nodes"][0]["dependencies"] = ["verify", "scan"]
        self.expect_error("sorted", draft)

    def test_resource_overlap_requires_dependency_order(self):
        draft = copy.deepcopy(self.draft)
        second = copy.deepcopy(draft["nodes"][0])
        second["node_id"] = "builder-two"
        second["dependencies"] = ["scan"]
        draft["nodes"].insert(1, second)
        self.expect_error("overlapping resources", draft)
        draft["nodes"][1]["dependencies"] = ["build"]
        sc.validate_contract(draft)

    def test_scope_and_canonicalization_guards(self):
        draft = copy.deepcopy(self.draft)
        draft["nodes"][1]["resources"] = ["path:src"]
        self.expect_error("PURE", draft)
        draft = copy.deepcopy(self.draft)
        draft["nodes"][0]["resources"] = []
        self.expect_error("require mutation resources", draft)
        draft = copy.deepcopy(self.draft)
        draft["protected_paths"].append("src")
        draft["protected_paths"].sort()
        self.expect_error("protected path", draft)
        draft = copy.deepcopy(self.draft)
        draft["nodes"][0]["resources"] = ["path:src/../secrets"]
        self.expect_error("canonical", draft)

    def test_exact_node_binding_and_path_audit(self):
        envelope = sc.freeze_contract(self.draft)
        digest = sc.assert_node_matches(
            envelope, node_id="build", klass="ISOLATED",
            model="gpt-5.6-terra", effort="high",
            outcome="Implement the accepted design in an isolated worktree",
            gate="focused tests pass and a scoped artifact is returned",
            base_revision="example-revision", dependencies=["scan"],
            join="all", resources=["path:src"], run_id="example-run",
            task_digest="sha256:example-task-digest")
        self.assertEqual(digest, envelope["contract_sha256"])
        for field, value in (("run_id", "wrong-run"),
                             ("task_digest", "wrong-task")):
            kwargs = {
                "node_id": "build", "klass": "ISOLATED",
                "model": "gpt-5.6-terra", "effort": "high",
                "outcome": "Implement the accepted design in an isolated worktree",
                "gate": "focused tests pass and a scoped artifact is returned",
                "base_revision": "example-revision", "dependencies": ["scan"],
                "join": "all", "resources": ["path:src"],
                "run_id": "example-run",
                "task_digest": "sha256:example-task-digest",
            }
            kwargs[field] = value
            with self.assertRaises(sc.ContractError):
                sc.assert_node_matches(envelope, **kwargs)
        with self.assertRaises(sc.ContractError):
            sc.assert_node_matches(
                envelope, node_id="build", klass="ISOLATED",
                model="gpt-5.6-terra", effort="high", outcome="changed",
                gate="focused tests pass and a scoped artifact is returned",
                base_revision="example-revision", dependencies=["scan"],
                join="all", resources=["path:src"])
        report = sc.audit_paths(envelope, "build", ["src/a.py", "src/tests/x.py"])
        self.assertEqual(report["checked_paths"], ["src/a.py", "src/tests/x.py"])
        with self.assertRaises(sc.ContractError):
            sc.audit_paths(envelope, "build", ["docs/outside.md"])
        with self.assertRaises(sc.ContractError):
            sc.audit_paths(envelope, "build", ["src/../outside"])

    def test_shape_duplicate_key_and_input_file_guards(self):
        draft = copy.deepcopy(self.draft)
        draft["extra"] = True
        self.expect_error("fields", draft)
        with tempfile.TemporaryDirectory(prefix="contract-hostile-") as tmp:
            duplicate = Path(tmp, "duplicate.json")
            duplicate.write_text('{"contract_version":1,"contract_version":1}',
                                 encoding="utf-8")
            with self.assertRaises(sc.ContractError):
                sc._read_json(str(duplicate))
            symlink = Path(tmp, "link.json")
            try:
                symlink.symlink_to(duplicate)
            except OSError:
                return
            with self.assertRaises(sc.ContractError):
                sc._read_json(str(symlink))

    def test_hostile_validation_boundary_matrix(self):
        mutations = [
            ("unsupported contract_version",
             lambda value: value.update(contract_version=True)),
            ("run_id is invalid", lambda value: value.update(run_id="bad id")),
            ("bounded string", lambda value: value.update(task_digest="")),
            ("unique array", lambda value:
             value.update(protected_paths=[".git", ".git"])),
            ("must be sorted", lambda value:
             value.update(protected_paths=["z", ".git"])),
            ("1..128", lambda value: value.update(nodes=[])),
            ("unexpected or missing", lambda value:
             value["nodes"][0].update(extra=True)),
            ("invalid or duplicate", lambda value:
             value["nodes"].insert(1, copy.deepcopy(value["nodes"][0]))),
            ("class is invalid", lambda value:
             value["nodes"][0].update({"class": "MAGIC"})),
            ("effort is invalid", lambda value:
             value["nodes"][0].update(effort="ultra")),
            ("contains control characters", lambda value:
             value["nodes"][0].update(outcome="bad\x00text")),
            ("dependencies must be unique", lambda value:
             value["nodes"][0].update(dependencies=["scan", "scan"])),
            ("resources must be unique", lambda value:
             value["nodes"][0].update(resources=["path:src", "path:src"])),
            ("resources must be sorted", lambda value:
             value["nodes"][0].update(resources=["path:z", "path:a"])),
            ("nodes must be sorted", lambda value:
             value.update(nodes=list(reversed(value["nodes"])))),
        ]
        for message, mutate in mutations:
            with self.subTest(message=message):
                draft = copy.deepcopy(self.draft)
                mutate(draft)
                self.expect_error(message, draft)

        for path in ("/absolute", "C:/absolute", "a//b"):
            with self.subTest(path=path), self.assertRaises(sc.ContractError):
                sc.canonical_path(path)
        for resource in ("missing-colon", "Bad:path", "key:"):
            with self.subTest(resource=resource), \
                    self.assertRaises(sc.ContractError):
                sc.canonical_resource(resource)
        self.assertFalse(sc._resource_conflict("key:a", "path:a"))
        self.assertEqual(sc._dependency_closure({"a": [], "b": ["a"]}),
                         {"a": set(), "b": {"a"}})

        unhashable = [
            lambda value: value.update(protected_paths=[[]]),
            lambda value: value["nodes"][0].update({"class": []}),
            lambda value: value["nodes"][0].update(effort=[]),
            lambda value: value["nodes"][0].update(dependencies=[[]]),
            lambda value: value["nodes"][0].update(resources=[[]]),
            lambda value: value["nodes"][0].update(join=[]),
        ]
        for mutate in unhashable:
            draft = copy.deepcopy(self.draft)
            mutate(draft)
            with self.subTest(mutate=mutate), self.assertRaises(sc.ContractError):
                sc.validate_contract(draft)

        envelope = sc.freeze_contract(self.draft)
        for mutation, message in (
                (lambda value: value.update(extra=True), "envelope fields"),
                (lambda value: value.update(format="wrong"), "format")):
            hostile = copy.deepcopy(envelope)
            mutation(hostile)
            with self.subTest(message=message), self.assertRaises(sc.ContractError):
                sc.validate_frozen_contract(hostile)
        common = {
            "node_id": "build", "klass": "ISOLATED",
            "model": "gpt-5.6-terra", "effort": "high",
            "outcome": "Implement the accepted design in an isolated worktree",
            "gate": "focused tests pass and a scoped artifact is returned",
            "base_revision": "wrong", "dependencies": ["scan"],
            "join": "all", "resources": ["path:src"],
        }
        with self.assertRaisesRegex(sc.ContractError, "base_revision"):
            sc.assert_node_matches(envelope, **common)
        common.update(base_revision="example-revision", node_id="missing")
        with self.assertRaisesRegex(sc.ContractError, "node_id"):
            sc.assert_node_matches(envelope, **common)
        with self.assertRaisesRegex(sc.ContractError, "node_id"):
            sc.audit_paths(envelope, "missing", [])

    def test_input_file_failure_modes_and_stdout_freeze(self):
        with tempfile.TemporaryDirectory(prefix="contract-inputs-") as tmp:
            missing = Path(tmp, "missing.json")
            with self.assertRaisesRegex(sc.ContractError, "inspect input"):
                sc._read_json(str(missing))
            with self.assertRaisesRegex(sc.ContractError, "regular"):
                sc._read_json(tmp)
            invalid = Path(tmp, "invalid.json")
            invalid.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(sc.ContractError, "invalid JSON"):
                sc._read_json(str(invalid))
            oversized = Path(tmp, "oversized.json")
            oversized.write_bytes(b"x" * (sc.MAX_BYTES + 1))
            with self.assertRaisesRegex(sc.ContractError, "512 KB"):
                sc._read_json(str(oversized))
            draft = Path(tmp, "draft.json")
            draft.write_text(json.dumps(self.draft), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL_PATH), "freeze", str(draft)],
                cwd=tmp, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), sc.freeze_contract(self.draft))
            malformed = copy.deepcopy(self.draft)
            malformed["protected_paths"] = [[]]
            draft.write_text(json.dumps(malformed), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL_PATH), "freeze", str(draft)],
                cwd=tmp, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 4)
            self.assertNotIn("Traceback", result.stderr)

    def test_dense_128_node_dag_is_bounded_and_valid(self):
        node_ids = ["node-{:03d}".format(index) for index in range(128)]
        draft = {
            "contract_version": 1,
            "run_id": "dense-run",
            "task_digest": "dense-task",
            "base_revision": "dense-base",
            "protected_paths": [".git", ".swarm/runs"],
            "nodes": [],
        }
        for index, node_id in enumerate(node_ids):
            draft["nodes"].append({
                "node_id": node_id,
                "class": "PURE",
                "model": "gpt-5.6-luna",
                "effort": "low",
                "outcome": "inspect dense node {}".format(index),
                "gate": "return bounded evidence",
                "dependencies": node_ids[:index],
                "join": "all",
                "resources": [],
            })
        self.assertEqual(sc.validate_contract(draft), draft)

    def test_cli_freeze_validate_audit_and_atomic_output(self):
        with tempfile.TemporaryDirectory(prefix="contract-cli-") as tmp:
            draft = Path(tmp, "draft.json")
            frozen = Path(tmp, "frozen.json")
            draft.write_text(json.dumps(self.draft), encoding="utf-8")
            run = lambda *args: subprocess.run(
                [sys.executable, str(TOOL_PATH), *args], cwd=tmp,
                capture_output=True, text=True, timeout=30)
            result = run("freeze", str(draft), "--output", str(frozen))
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run("validate", str(frozen))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
            result = run("audit-paths", str(frozen), "--node-id", "build",
                         "--path", "src/module.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run("audit-paths", str(frozen), "--node-id", "build",
                         "--path", "README.md")
            self.assertEqual(result.returncode, 4)
            try:
                Path(tmp, "output-link.json").symlink_to(frozen)
            except OSError:
                return
            result = run("freeze", str(draft), "--output",
                         str(Path(tmp, "output-link.json")))
            self.assertEqual(result.returncode, 4)


if __name__ == "__main__":
    unittest.main()
