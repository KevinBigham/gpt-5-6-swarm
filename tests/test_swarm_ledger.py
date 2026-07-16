"""Deterministic, offline scenario tests for scripts/swarm_ledger.py.

Every represented safety invariant maps to at least one test here, and each
enforced invariant has both an accepting and a rejecting side where applicable.
No network or live model calls are used.
"""

import importlib.util
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (REPO_ROOT / "plugins" / "gpt-5-6-swarm" / "skills" /
             "gpt-5-6-swarm" / "scripts" / "swarm_ledger.py")
CONTRACT_TOOL_PATH = TOOL_PATH.with_name("swarm_contract.py")

spec = importlib.util.spec_from_file_location("swarm_ledger", TOOL_PATH)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)
contract_spec = importlib.util.spec_from_file_location(
    "swarm_contract_for_ledger", CONTRACT_TOOL_PATH)
sc = importlib.util.module_from_spec(contract_spec)
contract_spec.loader.exec_module(sc)


class LedgerHarness(unittest.TestCase):
    """Shared helpers: a temp run with sane default host capabilities."""

    RUN = "test-run"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="swarm-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        sl.op_init(self.dir, self.RUN, "test", "digest-of-task",
                   ["thread_creation=true", "thread_listing=true",
                    "result_collection=true", "child_turn_read=true",
                    "unique_launch_discovery=true", "one_shot_fence=true",
                    "background_sessions=false"], "tester")
        self._nonce = 0

    # -- helpers ------------------------------------------------------------
    def gen(self):
        return sl.load_ledger(self.dir, self.RUN)["generation"]

    def nonce(self):
        self._nonce += 1
        return f"nonce-{self._nonce:04d}-abcdef"

    def create(self, node_id, klass="PURE", outcome=None, resources=(),
               model="gpt-5.6-luna", effort="low", gate="report delivered",
               base="rev-abc123", **kw):
        outcome = outcome or f"do the {node_id} work"
        resources = list(resources)
        launch_nonce = self.nonce()
        if klass == "ONE_SHOT" and "one_shot_authorization_file" not in kw:
            scope_ids = [f"{item['type']}:{item['id']}" for item in
                         sl.parse_resources(resources, self.dir)]
            fingerprint = sl.compute_fingerprint(
                outcome, base, "none", scope_ids, gate)
            kw["one_shot_authorization_file"] = self.authorization_for(
                node_id, fingerprint)
        return sl.op_create_node(
            self.dir, self.RUN, "tester", self.gen(),
            node_id=node_id, klass=klass, model=model, effort=effort,
            outcome=outcome, base_revision=base, inputs_digest="none",
            gate=gate, launch_nonce=launch_nonce,
            resources=resources, dependencies=kw.pop("deps", []),
            join=kw.pop("join", "all"), **kw)

    def authorization_for(self, node_id, fingerprint, **overrides):
        now = datetime.now(timezone.utc)
        authorization = {
            "authorization_version": 1,
            "operator_id": "test-operator",
            "run_id": self.RUN,
            "node_id": node_id,
            "task_fingerprint": fingerprint,
            "authorization_nonce": self.nonce(),
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        authorization.update(overrides)
        path = os.path.join(self.dir, f"authorization-{self._nonce}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(authorization, fh)
        return path

    def go(self, ref, target, **kw):
        return sl.op_transition(self.dir, self.RUN, "tester", self.gen(),
                                ref, target, **kw)

    def to_running(self, ref, thread=None):
        self.go(ref, "READY")
        self.go(ref, "CLAIMED")
        self.go(ref, "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(), ref)
        self.go(ref, "RUNNING", thread_id=thread or f"thread-{ref}")

    def receipt_for(self, ref, status="SUCCEEDED", **overrides):
        ledger = sl.load_ledger(self.dir, self.RUN)
        node = ledger["nodes"][ref]
        path_resources = [r["id"] for r in node["resources"]
                          if r["type"] == "path"]
        artifact_hashes = {}
        touched_paths = []
        artifact = f"reports/{node['node_id']}.md"
        if status == "SUCCEEDED" and path_resources:
            artifact = f"{path_resources[0]}/artifact-{node['node_id']}.txt"
            artifact_path = os.path.join(self.dir, *artifact.split("/"))
            os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
            payload = f"verified artifact for {ref}\n".encode()
            with open(artifact_path, "wb") as fh:
                fh.write(payload)
            artifact_hashes[artifact] = (
                "sha256:" + hashlib.sha256(payload).hexdigest())
            touched_paths = [artifact]
        receipt = {
            "run_id": self.RUN, "node_id": node["node_id"],
            "attempt": node["attempt"], "status": status,
            "thread_id": node["thread_id"],
            "model_effort": f"{node['model']}/{node['effort']}",
            "base_revision": node["fingerprint_inputs"]["base_revision"],
            "artifact": artifact,
            "touched_paths": touched_paths, "commands": [{"command": "pytest",
                                               "exit_code": 0}],
            "processes": {"spawned": [], "remaining_live": []},
            "resources_released": [f"{r['type']}:{r['id']}"
                                   for r in node["resources"]],
            "artifact_hashes": artifact_hashes, "descendant_thread_ids": [],
            "assumptions": [], "unresolved_risks": [], "cleanup_items": [],
        }
        receipt.update(overrides)
        path = os.path.join(self.dir, f"receipt-{ref.replace('#', '-')}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh)
        return path

    def succeed(self, ref):
        receipt = self.receipt_for(ref)
        with open(receipt, encoding="utf-8") as fh:
            hashes = json.load(fh)["artifact_hashes"]
        self.go(ref, "SUCCEEDED", receipt_file=receipt,
                verification_worktree=self.dir if hashes else None)

    def expect(self, exit_code, fn, *args, **kw):
        with self.assertRaises(sl.LedgerError) as ctx:
            fn(*args, **kw)
        self.assertEqual(ctx.exception.exit_code, exit_code,
                         f"expected exit {exit_code}, got "
                         f"{ctx.exception.exit_code}: {ctx.exception.message}")
        return ctx.exception


# ---------------------------------------------------------------------------
# Required scenario 1: duplicate logical task under different wording
# ---------------------------------------------------------------------------
class TestFingerprintDedup(LedgerHarness):
    def test_duplicate_task_different_wording(self):
        self.create("scan.api", outcome="Fix the Login Bug in auth module")
        err = self.expect(sl.EXIT_SEMANTIC, self.create, "scan.api2",
                          outcome="  fix   THE login bug in auth module ")
        self.assertIn("duplicate task fingerprint", err.message)

    def test_zero_width_smuggling_still_collides(self):
        a = sl.compute_fingerprint("deploy the app", "rev1", "none", [], "ok")
        b = sl.compute_fingerprint("de\u200bploy the\u2060 app", "rev1",
                                   "none", [], "ok")
        self.assertEqual(a, b)

    def test_scope_order_does_not_change_fingerprint(self):
        a = sl.compute_fingerprint("x", "r", "none",
                                   ["path:src/a", "path:src/b"], "g")
        b = sl.compute_fingerprint("x", "r", "none",
                                   ["path:src/b", "path:src/a"], "g")
        self.assertEqual(a, b)

    def test_different_gate_is_different_task(self):
        self.create("scan.api", gate="tests pass")
        self.create("scan.api.v2", gate="lint passes")  # no dedup error

    def test_supplied_fingerprint_crosscheck(self):
        self.expect(sl.EXIT_SEMANTIC, self.create, "forged",
                    supplied_fingerprint="0" * 64)

    def test_intentional_duplicate_requires_pure_group(self):
        self.create("probe", klass="PURE", outcome="research options",
                    dup_group="grp1")
        self.create("probe.b", klass="PURE", outcome="research options",
                    dup_group="grp1")  # allowed: any/quorum duplication
        self.expect(sl.EXIT_SEMANTIC, self.create, "probe.c",
                    outcome="research options")  # no group -> duplicate


# ---------------------------------------------------------------------------
# Required scenario 2: duplicate launch nonce (+ nonce namespaces)
# ---------------------------------------------------------------------------
class TestNonces(LedgerHarness):
    def test_duplicate_launch_nonce(self):
        sl.op_create_node(self.dir, self.RUN, "tester", self.gen(),
                          node_id="a", klass="PURE", model="gpt-5.6-luna",
                          effort="low", outcome="task a", base_revision="r",
                          inputs_digest="none", gate="g",
                          launch_nonce="shared-nonce-1", resources=[],
                          dependencies=[], join="all")
        err = self.expect(
            sl.EXIT_SEMANTIC, sl.op_create_node, self.dir, self.RUN,
            "tester", self.gen(), node_id="b", klass="PURE",
            model="gpt-5.6-luna", effort="low", outcome="task b",
            base_revision="r", inputs_digest="none", gate="g",
            launch_nonce="shared-nonce-1", resources=[], dependencies=[],
            join="all")
        self.assertIn("already issued", err.message)

    def test_arm_nonce_cannot_reuse_launch_nonce_namespace(self):
        self.create("shot", klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high")
        self.go("shot#1", "READY")
        self.go("shot#1", "CLAIMED")
        self.go("shot#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "shot#1")
        self.go("shot#1", "PREPARING", thread_id="thread-shot")
        taken = sl.load_ledger(self.dir, self.RUN)["nodes"]["shot#1"][
            "launch_nonce"]
        self.expect(sl.EXIT_SEMANTIC, self.go, "shot#1", "ARMED",
                    arm_nonce=taken, readiness_evidence="ready receipt")


# ---------------------------------------------------------------------------
# Required scenario 3: ambiguous thread creation stays UNKNOWN, no auto-retry
# ---------------------------------------------------------------------------
class TestAmbiguousCreation(LedgerHarness):
    def test_ambiguous_thread_creation_stays_unknown(self):
        self.create("recon")
        self.go("recon#1", "READY")
        self.go("recon#1", "CLAIMED")
        self.go("recon#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "recon#1")
        self.go("recon#1", "UNKNOWN",
                evidence="create call timed out; nonce discovery found 0 "
                         "matches and listing was incomplete")
        # relaunch of the same lineage is blocked while unreconciled
        self.expect(sl.EXIT_AMBIGUOUS, self.create, "recon")
        # reconciliation with proof lifts the block; attempt 2 is recorded
        sl.op_reconcile(self.dir, self.RUN, "tester", self.gen(), "recon#1",
                        evidence="thread listing complete: nonce absent",
                        outcome="no_delivery_proven")
        self.create("recon")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertIn("recon#2", ledger["nodes"])
        self.assertEqual(ledger["nodes"]["recon#1"]["state"], "UNKNOWN")

    def test_unknown_freezes_non_pure_launches(self):
        self.create("mut", klass="ISOLATED", resources=["path:src/x"],
                    model="gpt-5.6-terra", effort="high")
        self.go("mut#1", "READY")
        self.go("mut#1", "CLAIMED")
        self.go("mut#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "mut#1")
        self.go("mut#1", "UNKNOWN", evidence="ambiguous create")
        self.expect(sl.EXIT_AMBIGUOUS, self.create, "other",
                    klass="ISOLATED", resources=["path:src/y"],
                    model="gpt-5.6-terra", effort="high")
        self.create("scout")  # PURE work may proceed during the freeze


# ---------------------------------------------------------------------------
# Required scenario 4: stalled writer is never silently replaced
# ---------------------------------------------------------------------------
class TestStalledWriter(LedgerHarness):
    def test_stalled_writer_cannot_be_replaced(self):
        self.create("writer", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        # same lineage: previous attempt is not terminal
        err = self.expect(sl.EXIT_SEMANTIC, self.create, "writer",
                          klass="ISOLATED", resources=["path:src/api"],
                          model="gpt-5.6-terra", effort="high")
        self.assertIn("terminal", err.message)
        # different node, same fingerprint: duplicate
        self.expect(sl.EXIT_SEMANTIC, self.create, "writer.clone",
                    klass="ISOLATED", outcome="do the writer work",
                    resources=["path:src/api"], model="gpt-5.6-terra",
                    effort="high")
        # different node, overlapping scope: conflict at claim time
        self.create("squatter", klass="ISOLATED",
                    outcome="squat on the api path",
                    resources=["path:src/api/routes"],
                    model="gpt-5.6-terra", effort="high")
        self.go("squatter#1", "READY")
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "squatter#1", "CLAIMED")
        self.assertIn("conflict", err.message)


# ---------------------------------------------------------------------------
# Required scenario 5: stale generation compare-and-set
# ---------------------------------------------------------------------------
class TestGenerations(LedgerHarness):
    def test_stale_generation_rejected(self):
        stale = self.gen()
        self.create("a")
        err = self.expect(
            sl.EXIT_STALE, sl.op_create_node, self.dir, self.RUN, "tester",
            stale, node_id="b", klass="PURE", model="gpt-5.6-luna",
            effort="low", outcome="task b", base_revision="r",
            inputs_digest="none", gate="g", launch_nonce=self.nonce(),
            resources=[], dependencies=[], join="all")
        self.assertIn("stale generation", err.message)
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertNotIn("b#1", ledger["nodes"])  # nothing was written

    def test_generation_increments_by_one(self):
        before = self.gen()
        self.create("a")
        self.assertEqual(self.gen(), before + 1)


# ---------------------------------------------------------------------------
# Required scenario 6: illegal state transitions
# ---------------------------------------------------------------------------
class TestIllegalTransitions(LedgerHarness):
    def test_illegal_transition_rejected(self):
        self.create("a")
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "RUNNING")
        self.assertIn("illegal transition", err.message)

    def test_terminal_states_are_terminal(self):
        self.create("a")
        self.to_running("a#1")
        self.succeed("a#1")
        for target in ("RUNNING", "FAILED", "PLANNED"):
            self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", target)

    def test_unknown_cannot_be_un_unknowned(self):
        self.create("a")
        self.to_running("a#1")
        self.go("a#1", "UNKNOWN", evidence="reader crashed mid-flight")
        for target in ("RUNNING", "SUCCEEDED", "CANCELED"):
            self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", target)

    def test_launching_cancel_rules(self):
        self.create("a")
        self.go("a#1", "READY")
        self.go("a#1", "CLAIMED")
        self.go("a#1", "LAUNCHING")
        # before dispatch: CANCELING is wrong, CANCELED is right
        self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "CANCELING")
        self.go("a#1", "CANCELED")
        # after dispatch: CANCELED without proof is wrong
        self.create("b")
        self.go("b#1", "READY")
        self.go("b#1", "CLAIMED")
        self.go("b#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(), "b#1")
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "b#1", "CANCELED")
        self.assertIn("resolve the nonce", err.message)

    def test_claimed_cancel_requires_release(self):
        self.create("a", klass="ISOLATED", resources=["path:src/x"],
                    model="gpt-5.6-terra", effort="high")
        self.go("a#1", "READY")
        self.go("a#1", "CLAIMED")
        self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "CANCELED")
        sl.op_release_resources(self.dir, self.RUN, "tester", self.gen(),
                                "a#1", "canceling before launch")
        self.go("a#1", "CANCELED")

    def test_dependencies_gate_ready(self):
        self.create("dep")
        self.create("child", outcome="child work", deps=["dep"], join="all")
        self.expect(sl.EXIT_SEMANTIC, self.go, "child#1", "READY")
        self.to_running("dep#1")
        self.succeed("dep#1")
        self.go("child#1", "READY")

    def test_quorum_join(self):
        for name in ("q1", "q2", "q3"):
            self.create(name, outcome=f"probe {name}")
        self.create("agg", outcome="aggregate", deps=["q1", "q2", "q3"],
                    join="quorum:2")
        self.to_running("q1#1")
        self.succeed("q1#1")
        self.expect(sl.EXIT_SEMANTIC, self.go, "agg#1", "READY")
        self.to_running("q2#1")
        self.succeed("q2#1")
        self.go("agg#1", "READY")


# ---------------------------------------------------------------------------
# Required scenario 7 and 16: resource scopes (conflict + valid serialization)
# ---------------------------------------------------------------------------
class TestResources(LedgerHarness):
    def test_conflicting_resource_scopes(self):
        self.create("a", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.go("a#1", "READY")
        self.go("a#1", "CLAIMED")
        self.create("b", klass="ISOLATED", outcome="other work",
                    resources=["path:src/api/handlers"],
                    model="gpt-5.6-terra", effort="high")
        self.go("b#1", "READY")
        self.expect(sl.EXIT_SEMANTIC, self.go, "b#1", "CLAIMED")

    def test_write_serialization_valid_and_reclaim_after_release(self):
        self.create("a", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.create("b", klass="ISOLATED", outcome="web work",
                    resources=["path:src/web"], model="gpt-5.6-terra",
                    effort="high")
        self.to_running("a#1")
        self.to_running("b#1")  # disjoint scopes may run concurrently
        self.succeed("a#1")     # receipt reconciles and releases a's scope
        self.create("c", klass="ISOLATED", outcome="api follow-up",
                    resources=["path:src/api"], model="gpt-5.6-terra",
                    effort="high")
        self.go("c#1", "READY")
        self.go("c#1", "CLAIMED")  # freed scope is claimable again

    def test_resources_must_be_declared_in_canonical_order(self):
        self.expect(sl.EXIT_SEMANTIC, self.create, "x", klass="ISOLATED",
                    resources=["path:src/b", "path:src/a"],
                    model="gpt-5.6-terra", effort="high")

    def test_symlink_swap_cannot_rebind_claimed_resource(self):
        Path(self.dir, "scope-a").mkdir()
        Path(self.dir, "scope-b").mkdir()
        self.create("a", klass="ISOLATED", resources=["path:scope-a"])
        self.create("b", klass="ISOLATED", resources=["path:scope-b"])
        self.go("a#1", "READY")
        self.go("a#1", "CLAIMED")
        self.go("b#1", "READY")
        self.go("b#1", "CLAIMED")
        Path(self.dir, "scope-b").rmdir()
        os.symlink(Path(self.dir, "scope-a"), Path(self.dir, "scope-b"))
        err = self.expect(sl.EXIT_AMBIGUOUS, self.go,
                          "b#1", "LAUNCHING")
        self.assertIn("binding changed", err.message)

    def test_path_traversal_rejected(self):
        self.expect(sl.EXIT_SEMANTIC, self.create, "x",
                    resources=["path:src/../../etc"])

    def test_failed_node_resources_stay_frozen_until_released(self):
        self.create("a", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("a#1")
        self.go("a#1", "FAILED", evidence="tests failed; workspace dirty",
                receipt_file=self.receipt_for(
                    "a#1", status="FAILED", artifact="",
                    resources_released=[]))
        self.create("b", klass="ISOLATED", outcome="retake the scope",
                    resources=["path:src/api"], model="gpt-5.6-terra",
                    effort="high")
        self.go("b#1", "READY")
        self.expect(sl.EXIT_SEMANTIC, self.go, "b#1", "CLAIMED")
        sl.op_release_resources(self.dir, self.RUN, "tester", self.gen(),
                                "a#1", "workspace reset to rev-abc123")
        self.go("b#1", "CLAIMED")


# ---------------------------------------------------------------------------
# Required scenario 8: premature success without a receipt
# ---------------------------------------------------------------------------
class TestReceipts(LedgerHarness):
    def test_success_requires_receipt(self):
        self.create("a")
        self.to_running("a#1")
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "SUCCEEDED")
        self.assertIn("requires --receipt", err.message)

    def test_forged_receipts_rejected(self):
        self.create("a", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("a#1")
        cases = [
            {"attempt": 9},                       # wrong attempt
            {"thread_id": "thread-imposter"},     # wrong owner
            {"base_revision": "rev-zzz"},         # wrong pinned base
            {"model_effort": "gpt-5.6-terra/low"},  # wrong recorded effort
            {"artifact": ""},                     # no artifact
            {"processes": {"spawned": [],
                           "remaining_live": ["pid 4242"]}},  # live process
            {"descendant_thread_ids": ["t-child"]},  # nested delegation
            {"resources_released": []},           # unreconciled scope
            {"cleanup_items": [{"item": "temp dir"}]},  # ownerless cleanup
            {"status": "FAILED"},                 # status mismatch
        ]
        for overrides in cases:
            path = self.receipt_for("a#1", **overrides)
            self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "SUCCEEDED",
                        receipt_file=path)
        self.succeed("a#1")  # the honest receipt still passes

    def test_receipt_missing_key_is_shape_error(self):
        self.create("a")
        self.to_running("a#1")
        path = self.receipt_for("a#1")
        with open(path, encoding="utf-8") as fh:
            receipt = json.load(fh)
        del receipt["commands"]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh)
        self.expect(sl.EXIT_SHAPE, self.go, "a#1", "SUCCEEDED",
                    receipt_file=path)

    def test_receipt_scalar_hash_and_path_boundaries(self):
        self.create("writer", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        receipt_path = self.receipt_for("writer#1")
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        node = sl.load_ledger(self.dir, self.RUN)["nodes"]["writer#1"]

        def rejected(exit_code, change):
            changed = copy.deepcopy(receipt)
            change(changed)
            self.expect(exit_code, sl.validate_receipt, node, changed,
                        self.RUN, "SUCCEEDED")

        self.expect(sl.EXIT_SHAPE, sl.validate_receipt, node, [],
                    self.RUN, "SUCCEEDED")
        rejected(sl.EXIT_SHAPE, lambda d: d.__setitem__("extra", True))
        rejected(sl.EXIT_SHAPE, lambda d: d.__setitem__("run_id", 7))
        rejected(sl.EXIT_CORRUPT,
                 lambda d: d.__setitem__("artifact", "bad\x1b"))
        rejected(sl.EXIT_SHAPE, lambda d: d.__setitem__("attempt", True))
        rejected(sl.EXIT_SHAPE, lambda d: d.__setitem__("assumptions", [7]))
        rejected(sl.EXIT_SHAPE,
                 lambda d: d.__setitem__("artifact_hashes", []))
        rejected(sl.EXIT_SEMANTIC, lambda d: d.__setitem__(
            "artifact_hashes", {"src/api/x": "bad"}))
        rejected(sl.EXIT_SEMANTIC, lambda d: d.__setitem__(
            "artifact_hashes", {"src/api/./x": "sha256:" + "0" * 64}))
        rejected(sl.EXIT_SEMANTIC, lambda d: d.__setitem__("run_id", "other"))
        rejected(sl.EXIT_SEMANTIC,
                 lambda d: d.__setitem__("artifact_hashes", {}))
        rejected(sl.EXIT_SEMANTIC, lambda d: d.__setitem__(
            "artifact_hashes", {"other/x": "sha256:" + "0" * 64}))

        self.create("reader", outcome="read-only boundary")
        self.to_running("reader#1")
        pure_path = self.receipt_for("reader#1")
        pure = json.loads(Path(pure_path).read_text(encoding="utf-8"))
        pure["artifact_hashes"] = {
            "reports/x": "sha256:" + "0" * 64}
        pure_node = sl.load_ledger(self.dir, self.RUN)["nodes"]["reader#1"]
        self.expect(sl.EXIT_SEMANTIC, sl.validate_receipt,
                    pure_node, pure, self.RUN, "SUCCEEDED")

    def test_failure_requires_terminal_receipt_and_rejects_live_process(self):
        self.create("a")
        self.to_running("a#1")
        self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "FAILED",
                    evidence="known test failure")
        live = self.receipt_for(
            "a#1", status="FAILED", artifact="",
            processes={"spawned": ["pid 4242"],
                       "remaining_live": ["pid 4242"]})
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "FAILED",
                          evidence="known test failure", receipt_file=live)
        self.assertIn("live processes remaining", err.message)
        clean = self.receipt_for("a#1", status="FAILED", artifact="")
        self.go("a#1", "FAILED", evidence="process exited with status 1",
                receipt_file=clean)
        self.create("a")

    def test_receipt_collection_and_scope_shapes_fail_closed(self):
        self.create("writer", klass="ISOLATED",
                    resources=["cache:writer", "path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        cases = [
            (sl.EXIT_SHAPE, {"commands": "not-a-list"}),
            (sl.EXIT_SHAPE, {"commands": [{"command": "", "exit_code": 0}]}),
            (sl.EXIT_SHAPE, {"commands": [{"command": "test", "exit_code": 0,
                                            "extra": True}]}),
            (sl.EXIT_SHAPE, {"commands": [{"command": "test", "exit_code": 0,
                                            "result": 7}]}),
            (sl.EXIT_SHAPE, {"cleanup_items": "not-a-list"}),
            (sl.EXIT_SEMANTIC,
             {"resources_released": ["path:src/api", "path:src/api"]}),
            (sl.EXIT_SEMANTIC, {"resources_released": ["path:other"]}),
            (sl.EXIT_SEMANTIC, {"resources_released": ["path:src/api"]}),
            (sl.EXIT_SEMANTIC,
             {"touched_paths": ["src/api/x.py", "src/api/x.py"]}),
            (sl.EXIT_SEMANTIC, {"touched_paths": ["/tmp/escape"]}),
        ]
        for exit_code, overrides in cases:
            path = self.receipt_for("writer#1", **overrides)
            self.expect(exit_code, self.go, "writer#1", "SUCCEEDED",
                        receipt_file=path)

        self.create("reader")
        self.to_running("reader#1")
        path = self.receipt_for("reader#1", touched_paths=["src/read.txt"])
        self.expect(sl.EXIT_SEMANTIC, self.go, "reader#1", "SUCCEEDED",
                    receipt_file=path)

    def test_artifact_hashes_are_recomputed_not_self_attested(self):
        self.create("writer", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        receipt_path = self.receipt_for("writer#1")
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)
        artifact = next(iter(receipt["artifact_hashes"]))
        receipt["artifact_hashes"][artifact] = "sha256:" + "0" * 64
        with open(receipt_path, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh)
        err = self.expect(
            sl.EXIT_AMBIGUOUS, self.go, "writer#1", "SUCCEEDED",
            receipt_file=receipt_path, verification_worktree=self.dir)
        self.assertIn("artifact hash mismatch", err.message)
        self.assertEqual(sl.load_ledger(
            self.dir, self.RUN)["nodes"]["writer#1"]["state"], "RUNNING")

    def test_verify_artifacts_read_only_command_contract(self):
        self.create("writer", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        receipt_path = self.receipt_for("writer#1")
        before = self.gen()
        report = sl.op_verify_artifacts(
            self.dir, self.RUN, "writer#1", receipt_path, self.dir)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(self.gen(), before)

    def test_artifact_verification_rejects_missing_root_and_path_aliases(self):
        self.create("writer", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        receipt_path = self.receipt_for("writer#1")
        self.expect(sl.EXIT_SEMANTIC, self.go, "writer#1", "SUCCEEDED",
                    receipt_file=receipt_path)

        with open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)
        artifact = next(iter(receipt["artifact_hashes"]))
        absolute = copy.deepcopy(receipt)
        absolute["artifact_hashes"] = {
            "/tmp/out": next(iter(receipt["artifact_hashes"].values()))}
        absolute_path = Path(self.dir, "absolute-receipt.json")
        absolute_path.write_text(json.dumps(absolute), encoding="utf-8")
        self.expect(sl.EXIT_SEMANTIC, sl.op_verify_artifacts,
                    self.dir, self.RUN, "writer#1", str(absolute_path),
                    self.dir)

        target = Path(self.dir, *artifact.split("/"))
        external = Path(self.dir, "external-artifact.txt")
        external.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        self.expect(sl.EXIT_AMBIGUOUS, sl.op_verify_artifacts,
                    self.dir, self.RUN, "writer#1", receipt_path, self.dir)

        pure = copy.deepcopy(receipt)
        pure["artifact_hashes"] = {}
        self.expect(sl.EXIT_SEMANTIC, sl.verify_receipt_artifacts,
                    sl.load_ledger(self.dir, self.RUN)["nodes"]["writer#1"],
                    pure, self.dir)


# ---------------------------------------------------------------------------
# Required scenario 9: failure after an external mutation (guarded retry)
# ---------------------------------------------------------------------------
class TestGuardedRetry(LedgerHarness):
    def test_non_idempotent_retry_requires_authorization(self):
        self.create("deploy", klass="NON_IDEMPOTENT",
                    resources=["service:payments"], model="gpt-5.6-terra",
                    effort="high")
        self.to_running("deploy#1")
        self.go("deploy#1", "FAILED",
                evidence="deploy script exited 1 after the external call",
                receipt_file=self.receipt_for(
                    "deploy#1", status="FAILED", artifact="",
                    resources_released=[]))
        sl.op_release_resources(self.dir, self.RUN, "tester", self.gen(),
                                "deploy#1",
                                "service state audited: rollback confirmed")
        err = self.expect(sl.EXIT_SEMANTIC, self.create, "deploy",
                          klass="NON_IDEMPOTENT",
                          resources=["service:payments"],
                          model="gpt-5.6-terra", effort="high")
        self.assertIn("never retried automatically", err.message)
        self.create("deploy", klass="NON_IDEMPOTENT",
                    resources=["service:payments"], model="gpt-5.6-terra",
                    effort="high",
                    authorize_retry="user approved retry in chat @14:02")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertEqual(ledger["nodes"]["deploy#2"]["retry_authorization"],
                         "user approved retry in chat @14:02")


# ---------------------------------------------------------------------------
# Required scenarios 10 and 11: one-shot arming discipline
# ---------------------------------------------------------------------------
class TestOneShot(LedgerHarness):
    def arm(self, ref="shot#1"):
        self.create("shot", klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high")
        self.go(ref, "READY")
        self.go(ref, "CLAIMED")
        self.go(ref, "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(), ref)
        self.go(ref, "PREPARING", thread_id="thread-shot")
        self.go(ref, "ARMED", arm_nonce="arm-nonce-000001",
                readiness_evidence="readiness receipt verified")

    def test_one_shot_full_valid_flow(self):
        self.arm()
        sl.op_record_arm_dispatch(self.dir, self.RUN, "tester", self.gen(),
                                  "shot#1")
        self.go("shot#1", "RUNNING", arm_acknowledged=True)
        self.succeed("shot#1")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertTrue(ledger["nonces"]["arm"]["arm-nonce-000001"]["spent"])

    def test_one_shot_requires_declared_fence(self):
        bare = tempfile.mkdtemp(prefix="swarm-no-shot-fence-")
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        sl.op_init(bare, "bare-run", "test", "digest",
                   ["unique_launch_discovery=true"], "tester")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.op_create_node(
                bare, "bare-run", "tester", 1, node_id="shot",
                klass="ONE_SHOT", model="gpt-5.6-terra", effort="high",
                outcome="fire once", base_revision="r", inputs_digest="none",
                gate="g", launch_nonce="nonce-bare-shot-1",
                resources=["db:prod"], dependencies=[], join="all")
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_SEMANTIC)
        self.assertIn("one_shot_fence=true", ctx.exception.message)

    def test_one_shot_requires_fresh_task_bound_authorization(self):
        outcome = "fire the bounded action"
        fingerprint = sl.compute_fingerprint(
            outcome, "rev-abc123", "none", ["db:prod"],
            "report delivered")
        self.expect(sl.EXIT_SEMANTIC, sl.op_create_node,
                    self.dir, self.RUN, "tester", self.gen(),
                    node_id="missing", klass="ONE_SHOT",
                    model="gpt-5.6-terra", effort="high", outcome=outcome,
                    base_revision="rev-abc123", inputs_digest="none",
                    gate="report delivered", launch_nonce=self.nonce(),
                    resources=["db:prod"], dependencies=[], join="all")
        mismatched = self.authorization_for(
            "shot", "0" * 64)
        self.expect(sl.EXIT_SEMANTIC, self.create, "shot",
                    klass="ONE_SHOT", outcome=outcome,
                    resources=["db:prod"], model="gpt-5.6-terra",
                    effort="high", one_shot_authorization_file=mismatched)
        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        expired = self.authorization_for(
            "expired", fingerprint,
            issued_at=old.isoformat(),
            expires_at=(old + timedelta(minutes=10)).isoformat())
        self.expect(sl.EXIT_SEMANTIC, self.create, "expired",
                    klass="ONE_SHOT", outcome=outcome,
                    resources=["db:prod"], model="gpt-5.6-terra",
                    effort="high", one_shot_authorization_file=expired)

    def test_authorization_nonce_is_single_use(self):
        shared = "authority-shared-0001"
        first_outcome = "first authorized effect"
        first_fp = sl.compute_fingerprint(
            first_outcome, "rev-abc123", "none", ["db:prod"],
            "report delivered")
        first = self.authorization_for(
            "first", first_fp, authorization_nonce=shared)
        self.create("first", klass="ONE_SHOT", outcome=first_outcome,
                    resources=["db:prod"], model="gpt-5.6-terra",
                    effort="high", one_shot_authorization_file=first)
        second_outcome = "second authorized effect"
        second_fp = sl.compute_fingerprint(
            second_outcome, "rev-abc123", "none", ["db:other"],
            "report delivered")
        second = self.authorization_for(
            "second", second_fp, authorization_nonce=shared)
        self.expect(sl.EXIT_SEMANTIC, self.create, "second",
                    klass="ONE_SHOT", outcome=second_outcome,
                    resources=["db:other"], model="gpt-5.6-terra",
                    effort="high", one_shot_authorization_file=second)

    def test_authorization_validator_rejects_shape_and_time_edges(self):
        now = datetime.now(timezone.utc)
        fingerprint = "a" * 64
        base = {
            "authorization_version": 1,
            "operator_id": "operator",
            "run_id": self.RUN,
            "node_id": "shot",
            "task_fingerprint": fingerprint,
            "authorization_nonce": "authority-edge-0001",
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }

        def rejected(exit_code, **changes):
            value = copy.deepcopy(base)
            value.update(changes)
            self.expect(exit_code, sl.validate_one_shot_authorization,
                        value, run_id=self.RUN, node_id="shot",
                        fingerprint=fingerprint, now=now)

        self.expect(sl.EXIT_SHAPE, sl.validate_one_shot_authorization,
                    [], run_id=self.RUN, node_id="shot",
                    fingerprint=fingerprint, now=now)
        rejected(sl.EXIT_SHAPE, extra=True)
        rejected(sl.EXIT_VERSION, authorization_version=True)
        rejected(sl.EXIT_SHAPE, operator_id="bad id")
        rejected(sl.EXIT_SEMANTIC, run_id="other")
        rejected(sl.EXIT_SHAPE, authorization_nonce="short")
        rejected(sl.EXIT_SHAPE, issued_at=7)
        rejected(sl.EXIT_SEMANTIC, issued_at="not-a-time")
        rejected(sl.EXIT_SEMANTIC, issued_at="2026-07-16T10:00:00")
        rejected(
            sl.EXIT_SEMANTIC,
            expires_at=(now + timedelta(minutes=16)).isoformat())
        rejected(
            sl.EXIT_SEMANTIC,
            issued_at=(now + timedelta(minutes=6)).isoformat(),
            expires_at=(now + timedelta(minutes=10)).isoformat())

    def test_one_shot_double_arm_rejected(self):
        self.arm()
        sl.op_record_arm_dispatch(self.dir, self.RUN, "tester", self.gen(),
                                  "shot#1")
        err = self.expect(sl.EXIT_SEMANTIC, sl.op_record_arm_dispatch,
                          self.dir, self.RUN, "tester", self.gen(), "shot#1")
        self.assertIn("exactly once", err.message)
        self.expect(sl.EXIT_SEMANTIC, self.go, "shot#1", "ARMED",
                    arm_nonce="arm-nonce-000002",
                    readiness_evidence="again")  # ARMED -> ARMED is illegal

    def test_one_shot_spend_without_arming(self):
        self.create("shot", klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high")
        self.go("shot#1", "READY")
        self.go("shot#1", "CLAIMED")
        self.go("shot#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "shot#1")
        # one-shot may not skip PREPARING/ARMED
        self.expect(sl.EXIT_SEMANTIC, self.go, "shot#1", "RUNNING",
                    thread_id="thread-shot")
        self.go("shot#1", "PREPARING", thread_id="thread-shot")
        self.expect(sl.EXIT_SEMANTIC, self.go, "shot#1", "RUNNING")
        self.go("shot#1", "ARMED", arm_nonce="arm-nonce-000009",
                readiness_evidence="ready")
        # armed but the arm message was never sent
        self.expect(sl.EXIT_SEMANTIC, self.go, "shot#1", "RUNNING",
                    arm_acknowledged=True)
        sl.op_record_arm_dispatch(self.dir, self.RUN, "tester", self.gen(),
                                  "shot#1")
        # sent but not acknowledged: ambiguous delivery may not enter RUNNING
        self.expect(sl.EXIT_SEMANTIC, self.go, "shot#1", "RUNNING")

    def test_ambiguous_arm_delivery_freezes_forever(self):
        self.arm()
        sl.op_record_arm_dispatch(self.dir, self.RUN, "tester", self.gen(),
                                  "shot#1")
        self.go("shot#1", "UNKNOWN",
                evidence="arm send timed out; delivery unconfirmed")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertFalse(
            ledger["nonces"]["arm"]["arm-nonce-000001"]["spent"])
        # the executor lineage cannot be relaunched while unreconciled
        self.expect(sl.EXIT_AMBIGUOUS, self.create, "shot",
                    klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high",
                    authorize_retry="root says so")


# ---------------------------------------------------------------------------
# Required scenario 12: incomplete or corrupted ledger
# ---------------------------------------------------------------------------
class TestCorruption(LedgerHarness):
    def test_corrupted_ledger_detected(self):
        path = sl.ledger_path(self.dir, self.RUN)
        with open(path, "r+", encoding="utf-8") as fh:
            content = fh.read()
            fh.seek(0)
            fh.truncate()
            fh.write(content[: len(content) // 2])
        self.expect(sl.EXIT_CORRUPT, sl.load_ledger, self.dir, self.RUN)

    def test_duplicate_json_keys_rejected(self):
        path = sl.ledger_path(self.dir, self.RUN)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"schema_version": 1, "schema_version": 1}')
        self.expect(sl.EXIT_CORRUPT, sl.load_ledger, self.dir, self.RUN)

    def test_depth_bomb_rejected(self):
        path = sl.ledger_path(self.dir, self.RUN)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[" * 200 + "]" * 200)
        self.expect(sl.EXIT_CORRUPT, sl.load_ledger, self.dir, self.RUN)

    def test_depth_limit_cannot_be_reset_by_closed_sibling(self):
        path = sl.ledger_path(self.dir, self.RUN)
        # The leading empty object must not reduce the enclosing array's
        # counted depth. The later value reaches depth 65 and must fail.
        payload = "[{}," + "[" * 64 + "0" + "]" * 64 + "]"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        self.expect(sl.EXIT_CORRUPT, sl.load_ledger, self.dir, self.RUN)

    def test_oversized_receipt_rejected(self):
        self.create("a")
        self.to_running("a#1")
        path = os.path.join(self.dir, "big-receipt.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"pad": "' + "x" * (sl.RECEIPT_MAX_BYTES + 10) + '"}')
        self.expect(sl.EXIT_CORRUPT, self.go, "a#1", "SUCCEEDED",
                    receipt_file=path)


# ---------------------------------------------------------------------------
# Required scenario 13: unsupported future schema version fails closed
# ---------------------------------------------------------------------------
class TestVersioning(LedgerHarness):
    def test_future_schema_version_fails_closed(self):
        path = sl.ledger_path(self.dir, self.RUN)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        doc["schema_version"] = 99
        Path(path).write_text(json.dumps(doc), encoding="utf-8")
        err = self.expect(sl.EXIT_VERSION, sl.load_ledger, self.dir, self.RUN)
        self.assertIn("not supported", err.message)

    def test_missing_schema_version_is_shape_error(self):
        path = sl.ledger_path(self.dir, self.RUN)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        del doc["schema_version"]
        Path(path).write_text(json.dumps(doc), encoding="utf-8")
        self.expect(sl.EXIT_SHAPE, sl.load_ledger, self.dir, self.RUN)


# ---------------------------------------------------------------------------
# Required scenario 14: recovery from an interrupted atomic write
# ---------------------------------------------------------------------------
class TestRecovery(LedgerHarness):
    def test_recovery_interrupted_write(self):
        self.create("a")
        orphan = sl.ledger_path(self.dir, self.RUN) + ".tmp.4242"
        Path(orphan).write_text('{"partial": tru', encoding="utf-8")
        ledger, report = sl.op_recover(self.dir, self.RUN)
        self.assertTrue(any("tmp.4242" in f
                            for f in report["orphan_temp_files"]))
        ledger, report = sl.op_recover(self.dir, self.RUN,
                                       apply_changes=True)
        self.assertFalse(os.path.exists(orphan))
        # the canonical ledger was never affected
        _, findings = sl.op_validate(self.dir, self.RUN)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_OK)

    def test_recover_reports_in_flight_dispatch(self):
        self.create("a")
        self.go("a#1", "READY")
        self.go("a#1", "CLAIMED")
        self.go("a#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(), "a#1")
        _, report = sl.op_recover(self.dir, self.RUN)
        self.assertTrue(any("a#1" in line for line in report["in_flight"]))

    def test_external_writer_detected_and_reanchored(self):
        self.create("a")
        path = sl.ledger_path(self.dir, self.RUN)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        doc["updated_at"] = "2020-01-01T00:00:00+00:00"
        Path(path).write_text(json.dumps(doc, sort_keys=True, indent=2),
                              encoding="utf-8")
        self.expect(sl.EXIT_AMBIGUOUS, self.create, "b")
        _, findings = sl.op_validate(self.dir, self.RUN, check_journal=True)
        self.assertTrue(any(f.code == "A_EXTERNAL_WRITER" for f in findings))
        sl.op_recover(self.dir, self.RUN, accept_current=True,
                      evidence="hand edit was my own timestamp fix",
                      writer="tester")
        self.create("b")  # accepted and re-anchored

    def test_stale_lock_recovery_requires_standalone_evidence(self):
        token = sl.acquire_lock(self.dir, self.RUN, "test-stale-holder")
        self.assertTrue(token)
        self.expect(sl.EXIT_USAGE, sl.op_recover, self.dir, self.RUN,
                    apply_changes=True, clear_lock=True,
                    evidence="holder was never started")
        self.expect(sl.EXIT_USAGE, sl.op_recover, self.dir, self.RUN,
                    clear_lock=True)
        _, report = sl.op_recover(
            self.dir, self.RUN, clear_lock=True,
            evidence="test-created holder performed no mutation")
        self.assertIsNone(report["lock"])
        self.assertTrue(any("cleared stale lock" in action
                            for action in report["actions"]))
        self.expect(sl.EXIT_USAGE, sl.op_recover, self.dir, self.RUN,
                    clear_lock=True, evidence="already clear")


class TestDoctor(LedgerHarness):
    def test_doctor_reports_badge_artifacts_and_resume_token(self):
        self.create("reader")
        self.to_running("reader#1")
        self.succeed("reader#1")
        report = sl.op_doctor(self.dir, self.RUN)
        self.assertIn("RECORDED-CONSISTENCY ONLY", report["safety_badge"])
        self.assertIn("self-attested", report["capability_evidence"])
        self.assertTrue(report["resume"]["resumable"])
        self.assertRegex(report["resume"]["token"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["artifacts"][0]["verification"],
                         "unverified-pure-result")

        self.create("uncertain")
        self.go("uncertain#1", "READY")
        self.go("uncertain#1", "CLAIMED")
        self.go("uncertain#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "uncertain#1")
        report = sl.op_doctor(self.dir, self.RUN)
        self.assertFalse(report["resume"]["resumable"])
        self.assertIsNone(report["resume"]["token"])
        self.assertEqual(report["in_flight_ambiguity"][0]["kind"],
                         "dispatch-unresolved")

    def test_status_html_is_escaped_deterministic_and_atomic(self):
        self.create("reader", outcome="Inspect <script>alert(1)</script>",
                    gate="return A&B evidence")
        ledger = sl.load_ledger(self.dir, self.RUN)
        report = sl.op_doctor(self.dir, self.RUN)
        first = sl.render_status_html(ledger, report)
        second = sl.render_status_html(ledger, report)
        self.assertEqual(first, second)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", first)
        self.assertNotIn("<script>alert(1)</script>", first)
        self.assertIn("A&amp;B evidence", first)
        self.assertIn("does not observe live host threads", first)

        output = os.path.join(self.dir, "status.html")
        sl.write_status_html(output, first)
        with open(output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), first)
        self.expect(sl.EXIT_USAGE, sl.write_status_html,
                    os.path.join(self.dir, "missing", "status.html"), first)
        os.remove(sl.journal_path(self.dir, self.RUN))
        unsafe_report = sl.op_doctor(self.dir, self.RUN)
        unsafe_html = sl.render_status_html(
            sl.load_ledger(self.dir, self.RUN), unsafe_report)
        self.assertIn("JOURNAL AMBIGUITY", unsafe_html)
        self.assertIn("Status: <strong>absent</strong>", unsafe_html)
        self.assertNotIn("No recorded violations or ambiguity", unsafe_html)
        link = os.path.join(self.dir, "status-link.html")
        try:
            os.symlink(output, link)
        except OSError:
            return
        self.expect(sl.EXIT_CORRUPT, sl.write_status_html, link, first)


class TestFrozenContractBinding(LedgerHarness):
    def test_create_node_binds_exact_frozen_contract_digest(self):
        common = {
            "class": "PURE", "model": "gpt-5.6-luna", "effort": "low",
            "dependencies": [], "join": "all", "resources": [],
        }
        contract = {
            "contract_version": 1, "run_id": self.RUN,
            "task_digest": "digest-of-task", "base_revision": "rev-contract",
            "protected_paths": [".git"],
            "nodes": [
                dict(common, node_id="contract.other", outcome="other work",
                     gate="other gate"),
                dict(common, node_id="contract.scan", outcome="scan exactly",
                     gate="exact evidence"),
            ],
        }
        envelope = sc.freeze_contract(contract)
        path = os.path.join(self.dir, "frozen-contract.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle)
        self.create("contract.scan", outcome="scan exactly",
                    gate="exact evidence", base="rev-contract",
                    frozen_contract_file=path)
        node = sl.load_ledger(self.dir, self.RUN)["nodes"]["contract.scan#1"]
        self.assertEqual(node["fingerprint_inputs"]["inputs_digest"],
                         envelope["contract_sha256"])
        binding = sl.safe_load_json(
            sl.frozen_contract_binding_path(self.dir, self.RUN),
            sl.FROZEN_BINDING_MAX_BYTES)
        self.assertEqual(binding["contract_sha256"],
                         envelope["contract_sha256"])
        err = self.expect(
            sl.EXIT_SEMANTIC, self.create, "contract.other",
            outcome="other work", gate="changed gate", base="rev-contract",
            frozen_contract_file=path)
        self.assertIn("frozen contract refused node", err.message)

        mixed_contract = copy.deepcopy(contract)
        mixed_contract["nodes"][1]["outcome"] = "different scan plan"
        mixed_envelope = sc.freeze_contract(mixed_contract)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(mixed_envelope, handle)
        err = self.expect(
            sl.EXIT_SEMANTIC, self.create, "contract.other",
            outcome="other work", gate="other gate", base="rev-contract",
            frozen_contract_file=path)
        self.assertIn("differs from the contract already bound", err.message)

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle)
        self.create("contract.other", outcome="other work", gate="other gate",
                    base="rev-contract", frozen_contract_file=path)
        err = self.expect(sl.EXIT_SEMANTIC, self.create, "unbound.after",
                          outcome="unbound work")
        self.assertIn("every node must supply", err.message)

        wrong_run = copy.deepcopy(envelope)
        wrong_run["contract"]["run_id"] = "different-run"
        wrong_run = sc.freeze_contract(wrong_run["contract"])
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(wrong_run, handle)
        err = self.expect(
            sl.EXIT_SEMANTIC, self.create, "contract.other",
            outcome="other work", gate="other gate", base="rev-contract",
            frozen_contract_file=path)
        self.assertIn("run_id does not match contract", err.message)

        os.remove(sl.frozen_contract_binding_path(self.dir, self.RUN))
        err = self.expect(sl.EXIT_CORRUPT, self.create, "after.deletion",
                          outcome="must fail closed")
        self.assertIn("sidecar was removed", err.message)

    def test_contract_mode_cannot_start_after_an_unbound_node(self):
        self.create("plain.first", outcome="plain work")
        contract = {
            "contract_version": 1, "run_id": self.RUN,
            "task_digest": "digest-of-task", "base_revision": "rev-contract",
            "protected_paths": [".git"],
            "nodes": [{
                "node_id": "bound.late", "class": "PURE",
                "model": "gpt-5.6-luna", "effort": "low",
                "outcome": "late bound work", "gate": "report delivered",
                "dependencies": [], "join": "all", "resources": [],
            }],
        }
        path = os.path.join(self.dir, "late-contract.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(sc.freeze_contract(contract), handle)
        err = self.expect(
            sl.EXIT_SEMANTIC, self.create, "bound.late",
            outcome="late bound work", base="rev-contract",
            frozen_contract_file=path)
        self.assertIn("first node", err.message)

# ---------------------------------------------------------------------------
# Required scenario 15: valid PURE parallel work
# ---------------------------------------------------------------------------
class TestPureParallel(LedgerHarness):
    def test_pure_parallel_allowed(self):
        for name in ("scan.a", "scan.b", "scan.c"):
            self.create(name, outcome=f"survey {name}")
            self.to_running(f"{name}#1")
        _, findings = sl.op_validate(self.dir, self.RUN)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_OK)
        for name in ("scan.a", "scan.b", "scan.c"):
            self.succeed(f"{name}#1")
        _, findings = sl.op_validate(self.dir, self.RUN)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_OK)


# ---------------------------------------------------------------------------
# Hostile review: owners, capability gates, prohibited efforts, tampering
# ---------------------------------------------------------------------------
class TestHostile(LedgerHarness):
    def test_two_owners_rejected(self):
        self.create("a")
        self.create("b", outcome="different work entirely")
        self.to_running("a#1", thread="thread-shared")
        self.go("b#1", "READY")
        self.go("b#1", "CLAIMED")
        self.go("b#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(), "b#1")
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "b#1", "RUNNING",
                          thread_id="thread-shared")
        self.assertIn("already owns", err.message)

    def test_thread_reuse_within_lineage_is_legal(self):
        self.create("a")
        self.to_running("a#1", thread="thread-a")
        self.go("a#1", "FAILED", evidence="flaky test",
                receipt_file=self.receipt_for(
                    "a#1", status="FAILED", artifact=""))
        self.create("a")  # PURE retry needs no authorization
        self.go("a#2", "READY")
        self.go("a#2", "CLAIMED")
        self.go("a#2", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(), "a#2")
        self.go("a#2", "RUNNING", thread_id="thread-a")  # recorded N+1 reuse

    def test_owner_reassignment_rejected(self):
        self.create("a")
        self.to_running("a#1", thread="thread-a")
        self.go("a#1", "CANCELING")
        # CANCELING is legal; reassigning the owner mid-flight is not
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "CANCELED",
                          termination_evidence="stopped",
                          thread_id="thread-b",
                          receipt_file=self.receipt_for(
                              "a#1", status="CANCELED", artifact=""))
        self.assertIn("exactly one owner", err.message)

    def test_ultra_prohibited(self):
        err = self.expect(sl.EXIT_SEMANTIC, self.create, "x", effort="ultra")
        self.assertIn("prohibited", err.message)

    def test_capability_gate_guarded_classes(self):
        bare = tempfile.mkdtemp(prefix="swarm-bare-")
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        sl.op_init(bare, "bare-run", "test", "digest",
                   ["thread_listing=true"], "tester")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.op_create_node(
                bare, "bare-run", "tester", 1, node_id="shot",
                klass="ONE_SHOT", model="gpt-5.6-terra", effort="high",
                outcome="fire once", base_revision="r", inputs_digest="none",
                gate="g", launch_nonce="nonce-bare-0001",
                resources=["db:prod"],
                dependencies=[], join="all")
        self.assertIn("unique_launch_discovery", ctx.exception.message)

    def test_background_processes_gated_by_capability(self):
        self.create("a")
        self.to_running("a#1")
        path = self.receipt_for(
            "a#1", processes={"spawned": ["dev server"],
                              "remaining_live": []})
        err = self.expect(sl.EXIT_SEMANTIC, self.go, "a#1", "SUCCEEDED",
                          receipt_file=path)
        self.assertIn("background_sessions", err.message)

    def test_tampered_fingerprint_detected_by_validate(self):
        self.create("a")
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nodes"]["a#1"]["fingerprint_inputs"]["outcome"] = \
            "something else entirely"
        findings = sl.validate_ledger(ledger)
        self.assertTrue(any(f.code == "E_FINGERPRINT" for f in findings))

    def test_two_owner_hand_edit_detected_by_validate(self):
        self.create("a")
        self.create("b", outcome="different work entirely")
        self.to_running("a#1", thread="thread-x")
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nodes"]["b#1"]["thread_id"] = "thread-x"
        findings = sl.validate_ledger(ledger)
        self.assertTrue(any(f.code == "E_TWO_OWNERS" for f in findings))

    def test_control_characters_rejected(self):
        self.expect(sl.EXIT_CORRUPT, self.create, "x",
                    outcome="innocent\x00payload")

    def test_lock_conflict(self):
        token = sl.acquire_lock(self.dir, self.RUN, "other-writer")
        try:
            err = self.expect(sl.EXIT_STALE, self.create, "a")
            self.assertIn("lock is held", err.message)
        finally:
            sl.release_lock(self.dir, self.RUN, token=token)
        self.create("a")

    def test_journal_deletion_detected(self):
        """Hostile finding: deleting the journal must not silently disable
        the external-writer tripwire."""
        self.create("a")
        os.remove(sl.journal_path(self.dir, self.RUN))
        self.expect(sl.EXIT_AMBIGUOUS, self.create, "b")
        _, findings = sl.op_validate(self.dir, self.RUN, check_journal=True)
        self.assertTrue(any(f.code == "A_EXTERNAL_WRITER" for f in findings))
        sl.op_recover(self.dir, self.RUN, accept_current=True,
                      evidence="journal loss investigated; content verified "
                               "against chat mirror", writer="tester")
        self.create("b")

    def test_supersede_succeeded_artifact(self):
        self.create("a")
        self.to_running("a#1")
        self.succeed("a#1")
        err = self.expect(sl.EXIT_SEMANTIC, self.create, "a")
        self.assertIn("SUCCEEDED with a CURRENT artifact", err.message)
        self.create("a", supersedes="a#1")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertEqual(ledger["nodes"]["a#1"]["artifact_disposition"],
                         "CURRENT")
        self.assertIn("a#2", ledger["nodes"])
        self.to_running("a#2")
        self.succeed("a#2")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertEqual(ledger["nodes"]["a#1"]["artifact_disposition"],
                         "SUPERSEDED")


# ---------------------------------------------------------------------------
# Post-review hardening: reproduced audit findings become permanent regressions
# ---------------------------------------------------------------------------
class TestHardening(LedgerHarness):
    def test_non_pure_requires_resources(self):
        self.expect(sl.EXIT_SEMANTIC, self.create, "writer",
                    klass="ISOLATED", resources=[],
                    model="gpt-5.6-terra", effort="high")

    def test_claim_release_returns_to_ready_and_cannot_launch(self):
        self.create("writer", klass="ISOLATED", resources=["path:src"],
                    model="gpt-5.6-terra", effort="high")
        self.go("writer#1", "READY")
        self.go("writer#1", "CLAIMED")
        sl.op_release_resources(self.dir, self.RUN, "tester", self.gen(),
                                "writer#1", "cancel before launch")
        node = sl.load_ledger(self.dir, self.RUN)["nodes"]["writer#1"]
        self.assertEqual(node["state"], "READY")
        self.assertFalse(node["holds_resources"])
        self.expect(sl.EXIT_SEMANTIC, self.go, "writer#1", "LAUNCHING")

    def test_predictable_temp_symlink_cannot_overwrite_victim(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        victim = os.path.join(self.dir, "victim.txt")
        Path(victim).write_text("untouched", encoding="utf-8")
        planted = sl.ledger_path(self.dir, self.RUN) + f".tmp.{os.getpid()}"
        try:
            os.symlink(victim, planted)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        self.create("a")
        self.assertEqual(Path(victim).read_text(encoding="utf-8"), "untouched")
        self.assertFalse(os.path.islink(sl.ledger_path(self.dir, self.RUN)))

    def test_symlink_receipt_is_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        self.create("a")
        self.to_running("a#1")
        real = self.receipt_for("a#1")
        link = os.path.join(self.dir, "receipt-link.json")
        try:
            os.symlink(real, link)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        self.expect(sl.EXIT_CORRUPT, self.go, "a#1", "SUCCEEDED",
                    receipt_file=link)

    def test_force_clear_refuses_symlink_lock_directory(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        external = tempfile.mkdtemp(prefix="swarm-external-lock-")
        self.addCleanup(shutil.rmtree, external, ignore_errors=True)
        owner = Path(external, "owner.json")
        owner.write_text('{"do_not_delete": true}', encoding="utf-8")
        lpath = sl.lock_dir(self.dir, self.RUN)
        try:
            os.symlink(external, lpath)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        self.expect(sl.EXIT_CORRUPT, sl.op_recover,
                    self.dir, self.RUN, clear_lock=True,
                    evidence="claimed stale", writer="tester")
        self.assertTrue(owner.exists())

    def test_future_protocol_version_fails_closed(self):
        path = sl.ledger_path(self.dir, self.RUN)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        doc["protocol_version"] = "99.0.0"
        Path(path).write_text(json.dumps(doc), encoding="utf-8")
        self.expect(sl.EXIT_VERSION, sl.load_ledger, self.dir, self.RUN)

    def test_schema_boolean_and_malformed_nonces_are_shape_errors(self):
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["schema_version"] = True
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_SHAPE)

    def test_schema2_new_field_shape_guards(self):
        self.create("reader")
        baseline = sl.load_ledger(self.dir, self.RUN)

        def shape_error(change):
            changed = copy.deepcopy(baseline)
            change(changed)
            findings = sl.validate_ledger_shape(changed)
            self.assertTrue(findings, change)

        mutations = [
            lambda d: d["nonces"].pop("authorization"),
            lambda d: d["nonces"].__setitem__("authorization", []),
            lambda d: d["nonces"].__setitem__(
                "authorization", {"bad": "reader#1"}),
            lambda d: d["nodes"]["reader#1"].pop("artifact_verification"),
            lambda d: d["nodes"]["reader#1"].pop("one_shot_authorization"),
            lambda d: d["nodes"]["reader#1"].__setitem__(
                "artifact_verification", {}),
            lambda d: d["nodes"]["reader#1"].__setitem__(
                "one_shot_authorization", {}),
            lambda d: d["nodes"]["reader#1"].__setitem__("thread_id", 7),
            lambda d: d["nodes"]["reader#1"].__setitem__(
                "fingerprint", "bad"),
            lambda d: d["nodes"]["reader#1"].__setitem__("arm", {}),
            lambda d: d["nodes"]["reader#1"].__setitem__("resources", {}),
            lambda d: d["nodes"]["reader#1"].__setitem__(
                "dependencies", [7]),
            lambda d: d["nodes"]["reader#1"].__setitem__("join", 7),
            lambda d: d["nodes"]["reader#1"].__setitem__("receipt", []),
            lambda d: d["nodes"]["reader#1"].__setitem__(
                "receipt_sha256", "bad"),
        ]
        for mutation in mutations:
            shape_error(mutation)

    def test_schema2_authorization_and_verification_tamper_guards(self):
        self.create("shot", klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high")
        shot = sl.load_ledger(self.dir, self.RUN)
        changed = copy.deepcopy(shot)
        changed["nodes"]["shot#1"]["one_shot_authorization"][
            "source_sha256"] = "0" * 64
        self.assertTrue(any(f.code == "E_AUTHORIZATION"
                            for f in sl.validate_ledger(changed)))
        changed = copy.deepcopy(shot)
        changed["nonces"]["authorization"] = {}
        self.assertTrue(any(f.code == "E_AUTHORIZATION"
                            for f in sl.validate_ledger(changed)))
        changed = copy.deepcopy(shot)
        changed["nodes"]["shot#1"]["one_shot_authorization"][
            "authorization_version"] = True
        self.assertEqual(sl.exit_code_for(sl.validate_ledger(changed)),
                         sl.EXIT_SHAPE)
        changed = copy.deepcopy(shot)
        recorded = changed["nodes"]["shot#1"]["one_shot_authorization"]
        recorded["issued_at"] = "not-a-time"
        original = {field: recorded.get(field)
                    for field in sl.REQUIRED_AUTHORIZATION_KEYS}
        recorded["source_sha256"] = hashlib.sha256(json.dumps(
            original, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        self.assertTrue(any(f.code == "E_AUTHORIZATION"
                            for f in sl.validate_ledger(changed)))

        other = copy.deepcopy(shot)
        auth = other["nodes"]["shot#1"]["one_shot_authorization"]
        other["nodes"]["shot#1"]["one_shot_authorization"] = None
        other["nonces"]["authorization"] = {}
        other["nodes"]["shot#1"]["class"] = "ISOLATED"
        other["nodes"]["shot#1"]["one_shot"] = False
        other["nodes"]["shot#1"]["one_shot_authorization"] = auth
        self.assertTrue(any(f.code == "E_AUTHORIZATION"
                            for f in sl.validate_ledger(other)))

        self.create("writer", klass="ISOLATED", outcome="write artifact",
                    resources=["path:src/api"], model="gpt-5.6-terra",
                    effort="high")
        self.to_running("writer#1")
        self.succeed("writer#1")
        succeeded = sl.load_ledger(self.dir, self.RUN)
        changed = copy.deepcopy(succeeded)
        changed["nodes"]["writer#1"]["artifact_verification"][
            "receipt_sha256"] = "0" * 64
        self.assertTrue(any(f.code == "E_ARTIFACT_VERIFY"
                            for f in sl.validate_ledger(changed)))

    def test_nested_extra_keys_and_history_note_types_are_rejected(self):
        self.create("a")
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nodes"]["a#1"]["history"][0]["note"] = 7
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_SHAPE)
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nonces"]["arm"]["arm-nonce-alias"] = {
            "node": "a#1", "spent": False, "extra": True}
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_SHAPE)

    def test_malformed_and_alias_nonce_registry_entries_are_rejected(self):
        self.create("a")
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nonces"]["launch"]["x"] = "a#1"
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_SEMANTIC)
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nonces"]["launch"]["alias-nonce-0001"] = "a#1"
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_SEMANTIC)

    def test_receipt_process_object_rejects_extra_keys(self):
        self.create("a")
        self.to_running("a#1")
        receipt = self.receipt_for("a#1")
        doc = json.loads(Path(receipt).read_text(encoding="utf-8"))
        doc["processes"]["extra"] = []
        Path(receipt).write_text(json.dumps(doc), encoding="utf-8")
        self.expect(sl.EXIT_SHAPE, self.go, "a#1", "SUCCEEDED",
                    receipt_file=receipt)
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nonces"] = []
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_SHAPE)

    def test_hand_edit_coherence_checks(self):
        self.create("a")
        self.to_running("a#1")
        self.succeed("a#1")
        baseline = sl.load_ledger(self.dir, self.RUN)

        changed = copy.deepcopy(baseline)
        changed["nodes"]["a#1"]["node_id"] = "other"
        self.assertTrue(any(f.code == "E_NODE_KEY"
                            for f in sl.validate_ledger(changed)))

        changed = copy.deepcopy(baseline)
        changed["nodes"]["a#1"]["history"][2]["from"] = "RUNNING"
        self.assertTrue(any(f.code == "E_HISTORY"
                            for f in sl.validate_ledger(changed)))

        changed = copy.deepcopy(baseline)
        changed["nodes"]["a#1"]["dispatch_issued"] = False
        self.assertTrue(any(f.code == "E_DISPATCH"
                            for f in sl.validate_ledger(changed)))

        changed = copy.deepcopy(baseline)
        changed["nodes"]["a#1"]["receipt_sha256"] = "0" * 64
        self.assertTrue(any(f.code == "E_RECEIPT"
                            for f in sl.validate_ledger(changed)))

    def test_out_of_scope_receipt_path_is_rejected(self):
        self.create("writer", klass="ISOLATED", resources=["path:src/api"],
                    model="gpt-5.6-terra", effort="high")
        self.to_running("writer#1")
        receipt = self.receipt_for("writer#1",
                                   touched_paths=["tests/escape.py"])
        self.expect(sl.EXIT_SEMANTIC, self.go, "writer#1", "SUCCEEDED",
                    receipt_file=receipt)

    def test_duplicate_dependencies_cannot_fake_quorum(self):
        self.create("one")
        self.expect(sl.EXIT_SEMANTIC, self.create, "aggregate",
                    outcome="aggregate evidence", deps=["one", "one"],
                    join="quorum:2")
        self.expect(sl.EXIT_SEMANTIC, self.create, "missing",
                    outcome="missing dependency", deps=["ghost"])
        self.expect(sl.EXIT_SEMANTIC, self.create, "impossible",
                    outcome="impossible quorum", deps=["one"],
                    join="quorum:2")

    def test_dependency_cycle_hand_edit_is_rejected(self):
        self.create("a")
        self.create("b", outcome="work b", deps=["a"])
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["nodes"]["a#1"]["dependencies"] = ["b"]
        findings = sl.validate_ledger(ledger)
        self.assertTrue(any(f.code == "E_DEPENDENCY" and "cycle" in f.message
                            for f in findings))

    def test_case_alias_paths_conflict(self):
        self.create("a", klass="ISOLATED", resources=["path:src/Foo"],
                    model="gpt-5.6-terra", effort="high")
        self.go("a#1", "READY")
        self.go("a#1", "CLAIMED")
        self.create("b", klass="ISOLATED", outcome="different work",
                    resources=["path:src/foo"], model="gpt-5.6-terra",
                    effort="high")
        self.go("b#1", "READY")
        self.expect(sl.EXIT_SEMANTIC, self.go, "b#1", "CLAIMED")

    def test_completed_one_shot_lineage_cannot_retry(self):
        self.create("shot", klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high")
        self.go("shot#1", "READY")
        self.go("shot#1", "CLAIMED")
        self.go("shot#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "shot#1")
        self.go("shot#1", "PREPARING", thread_id="thread-shot-hardening")
        self.go("shot#1", "ARMED", arm_nonce="arm-hardening-0001",
                readiness_evidence="ready")
        sl.op_record_arm_dispatch(self.dir, self.RUN, "tester", self.gen(),
                                  "shot#1")
        self.go("shot#1", "RUNNING", arm_acknowledged=True)
        self.succeed("shot#1")
        self.expect(sl.EXIT_SEMANTIC, self.create, "shot",
                    klass="ONE_SHOT", resources=["db:prod"],
                    model="gpt-5.6-terra", effort="high",
                    authorize_retry="explicit but unsafe")

    def test_reconciliation_is_monotonic_and_outcome_compatible(self):
        self.create("a")
        self.to_running("a#1")
        self.go("a#1", "UNKNOWN", evidence="lost terminal response")
        self.expect(sl.EXIT_SEMANTIC, sl.op_reconcile,
                    self.dir, self.RUN, "tester", self.gen(), "a#1",
                    "listing found no launch", "no_delivery_proven")

        self.create("b")
        self.go("b#1", "READY")
        self.go("b#1", "CLAIMED")
        self.go("b#1", "LAUNCHING")
        sl.op_record_dispatch(self.dir, self.RUN, "tester", self.gen(),
                              "b#1")
        self.go("b#1", "UNKNOWN", evidence="ambiguous create")
        sl.op_reconcile(self.dir, self.RUN, "tester", self.gen(), "b#1",
                        "complete listing proves no delivery",
                        "no_delivery_proven")
        self.expect(sl.EXIT_SEMANTIC, sl.op_reconcile,
                    self.dir, self.RUN, "tester", self.gen(), "b#1",
                    "proof withdrawn", "unresolved")

    def test_supersession_failure_preserves_current_artifact(self):
        self.create("a")
        self.to_running("a#1")
        self.succeed("a#1")
        self.create("a", supersedes="a#1")
        self.to_running("a#2")
        self.go("a#2", "FAILED", evidence="replacement failed",
                receipt_file=self.receipt_for(
                    "a#2", status="FAILED", artifact=""))
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.assertEqual(ledger["nodes"]["a#1"]["artifact_disposition"],
                         "CURRENT")

    def test_concurrent_initialization_has_one_winner(self):
        target = tempfile.mkdtemp(prefix="swarm-init-race-")
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        barrier = threading.Barrier(2)
        original = sl.ensure_runtime_directory

        def synchronized(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(timeout=5)
            return result

        def initialize(writer):
            try:
                sl.op_init(target, "race", "test", writer, [], writer)
                return "ok"
            except sl.LedgerError as exc:
                return exc.exit_code

        sl.ensure_runtime_directory = synchronized
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(initialize, ("one", "two")))
        finally:
            sl.ensure_runtime_directory = original
        self.assertEqual(results.count("ok"), 1, results)
        journal = Path(sl.journal_path(target, "race")).read_text(
            encoding="utf-8")
        self.assertEqual(journal.count('"action": "init"'), 1)

    def test_recovery_mutation_respects_active_lock(self):
        token = sl.acquire_lock(self.dir, self.RUN, "active-writer")
        try:
            self.expect(sl.EXIT_STALE, sl.op_recover,
                        self.dir, self.RUN, accept_current=True,
                        evidence="should not race", writer="tester")
        finally:
            sl.release_lock(self.dir, self.RUN, token=token)

    def test_recovery_refuses_to_anchor_invalid_ledger(self):
        path = sl.ledger_path(self.dir, self.RUN)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        doc["nonces"] = []
        Path(path).write_text(json.dumps(doc), encoding="utf-8")
        self.expect(sl.EXIT_SHAPE, sl.op_recover,
                    self.dir, self.RUN, accept_current=True,
                    evidence="invalid state must not be trusted", writer="tester")

    def test_torn_journal_tail_is_safely_auto_repaired(self):
        self.create("a")
        with open(sl.journal_path(self.dir, self.RUN), "ab") as handle:
            handle.write(b'{"partial":')
        _, findings = sl.op_validate(self.dir, self.RUN, check_journal=True)
        self.assertTrue(any(f.code == "W_JOURNAL_RECOVERABLE"
                            for f in findings))
        _, report = sl.op_recover(self.dir, self.RUN, apply_changes=True,
                                  writer="tester")
        self.assertEqual(report["journal"], "anchored")
        self.create("b")

    def test_existing_control_character_is_rejected(self):
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["task"]["description_digest"] = "safe\x1b[31m"
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_CORRUPT)


class TestReferenceSet(unittest.TestCase):
    def test_current_reference_set_passes(self):
        report = sl.verify_reference_set()
        self.assertEqual(report["protocol_version"], sl.PROTOCOL_VERSION)
        self.assertEqual(set(report["checked"]), set(sl.REFERENCE_SET_FILES))

    def test_mixed_reference_set_fails_closed(self):
        source = TOOL_PATH.parents[1]
        target_root = tempfile.mkdtemp(prefix="swarm-mixed-reference-")
        self.addCleanup(shutil.rmtree, target_root, ignore_errors=True)
        target = Path(target_root, "gpt-5-6-swarm")
        shutil.copytree(source, target)
        route = target / "references" / "ROUTES.md"
        route.write_text(route.read_text(encoding="utf-8").replace(
            sl.REFERENCE_SET_STAMP, "Protocol reference set: `1.1.0`."),
            encoding="utf-8")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.verify_reference_set(str(target))
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_VERSION)
        self.assertIn("mixed reference sets", ctx.exception.message)

    def test_missing_reference_fails_as_version_error(self):
        source = TOOL_PATH.parents[1]
        target_root = tempfile.mkdtemp(prefix="swarm-missing-reference-")
        self.addCleanup(shutil.rmtree, target_root, ignore_errors=True)
        target = Path(target_root, "gpt-5-6-swarm")
        shutil.copytree(source, target)
        (target / "references" / "HOSTS.md").unlink()
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.verify_reference_set(str(target))
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_VERSION)
        self.assertIn("missing or unsafe", ctx.exception.message)

    def test_duplicate_reference_stamp_fails_closed(self):
        source = TOOL_PATH.parents[1]
        target_root = tempfile.mkdtemp(prefix="swarm-duplicate-reference-")
        self.addCleanup(shutil.rmtree, target_root, ignore_errors=True)
        target = Path(target_root, "gpt-5-6-swarm")
        shutil.copytree(source, target)
        route = target / "references" / "ROUTES.md"
        route.write_text(
            route.read_text(encoding="utf-8") + "\n" +
            sl.REFERENCE_SET_STAMP + "\n", encoding="utf-8")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.verify_reference_set(str(target))
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_VERSION)
        self.assertIn("exactly one exact stamp", ctx.exception.message)


class TestCapabilities(unittest.TestCase):
    def test_profiles_are_honest_and_feature_specific(self):
        serial = sl.capability_profile({})
        self.assertEqual(serial["tier"], "serial")
        self.assertIn("thread_creation", serial["missing_minimum"])
        read_only = sl.capability_profile({
            "thread_creation": True, "thread_listing": True,
            "result_collection": True})
        self.assertEqual(read_only["tier"], "ledger-assisted-read-only")
        self.assertIn("model-specific-routing", read_only["disabled"])
        integrated = sl.capability_profile({
            key: True for key in sl.HOST_INTEGRATED_CAPABILITIES})
        self.assertEqual(integrated["tier"], "host-integrated")
        self.assertNotIn("model-specific-routing", integrated["disabled"])


class TestGitBaseline(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="swarm-git-baseline-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        commands = [
            ["git", "init", "-q"],
            ["git", "config", "user.name", "Swarm Tests"],
            ["git", "config", "user.email", "swarm@example.invalid"],
        ]
        for command in commands:
            subprocess.run(command, cwd=self.dir, check=True,
                           capture_output=True)
        Path(self.dir, "tracked.txt").write_text("baseline\n", "utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.dir,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"],
                       cwd=self.dir, check=True, capture_output=True)

    def test_capture_and_verify_detects_dirty_drift(self):
        baseline = sl.capture_git_baseline(self.dir)
        self.assertFalse(baseline["dirty"])
        self.assertEqual(sl.verify_git_baseline(
            self.dir, baseline["revision"], baseline["dirty_digest"]),
            baseline)
        Path(self.dir, "tracked.txt").write_text("drift\n", "utf-8")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.verify_git_baseline(
                self.dir, baseline["revision"], baseline["dirty_digest"])
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_AMBIGUOUS)
        self.assertIn("dirty-state", ctx.exception.message)

    def test_ignored_content_digest_detects_invisible_drift(self):
        Path(self.dir, ".gitignore").write_text("ignored.bin\n", "utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.dir,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "ignore fixture"],
                       cwd=self.dir, check=True, capture_output=True)
        Path(self.dir, "ignored.bin").write_bytes(b"first")
        baseline = sl.capture_git_baseline(self.dir, include_ignored=True)
        self.assertIn("ignored_digest", baseline)
        self.assertEqual(baseline["ignored_file_count"], 1)
        Path(self.dir, "ignored.bin").write_bytes(b"second")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.verify_git_baseline(
                self.dir, baseline["revision"], baseline["dirty_digest"],
                baseline["ignored_digest"])
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_AMBIGUOUS)
        self.assertIn("ignored-content", ctx.exception.message)

    def test_ignored_digest_enforces_cost_bounds_and_hashes_symlinks(self):
        Path(self.dir, ".gitignore").write_text(
            "ignored.bin\nignored-link\n", "utf-8")
        Path(self.dir, "ignored.bin").write_bytes(b"large")
        original_file = sl.IGNORED_BASELINE_MAX_FILE_BYTES
        original_total = sl.IGNORED_BASELINE_MAX_TOTAL_BYTES
        try:
            sl.IGNORED_BASELINE_MAX_FILE_BYTES = 1
            with self.assertRaises(sl.LedgerError) as ctx:
                sl.capture_git_baseline(self.dir, include_ignored=True)
            self.assertEqual(ctx.exception.exit_code, sl.EXIT_USAGE)
            sl.IGNORED_BASELINE_MAX_FILE_BYTES = original_file
            sl.IGNORED_BASELINE_MAX_TOTAL_BYTES = 1
            with self.assertRaises(sl.LedgerError) as ctx:
                sl.capture_git_baseline(self.dir, include_ignored=True)
            self.assertEqual(ctx.exception.exit_code, sl.EXIT_USAGE)
        finally:
            sl.IGNORED_BASELINE_MAX_FILE_BYTES = original_file
            sl.IGNORED_BASELINE_MAX_TOTAL_BYTES = original_total
        try:
            os.symlink("tracked.txt", Path(self.dir, "ignored-link"))
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        report = sl.capture_git_baseline(self.dir, include_ignored=True)
        self.assertEqual(report["ignored_file_count"], 2)


class TestProcessConcurrency(unittest.TestCase):
    def test_real_process_generation_race(self):
        workdir = tempfile.mkdtemp(prefix="swarm-process-race-")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        init = subprocess.run(
            [sys.executable, str(TOOL_PATH), "init", "--root", workdir,
             "--run-id", "race", "--task-type", "test",
             "--task-digest", "digest"], capture_output=True, text=True,
            timeout=60)
        self.assertEqual(init.returncode, 0, init.stderr)
        processes = []
        for number in range(8):
            processes.append(subprocess.Popen(
                [sys.executable, str(TOOL_PATH), "create-node",
                 "--root", workdir, "--run-id", "race",
                 "--expect-generation", "1", "--node-id", f"node{number}",
                 "--class", "PURE", "--model", "gpt-5.6-luna",
                 "--effort", "low", "--outcome", f"work {number}",
                 "--base-revision", "rev1", "--gate", "report",
                 "--launch-nonce", f"nonce-race-{number:04d}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        results = [process.communicate(timeout=60) + (process.returncode,)
                   for process in processes]
        codes = [result[2] for result in results]
        self.assertEqual(codes.count(0), 1, results)
        self.assertTrue(all(code in {0, sl.EXIT_STALE} for code in codes),
                        results)
        ledger = sl.load_ledger(workdir, "race")
        self.assertEqual(ledger["generation"], 2)
        self.assertEqual(len(ledger["nodes"]), 1)


class TestAtomicPersistence(LedgerHarness):
    def test_atomic_replace_failure_preserves_canonical(self):
        path = Path(sl.ledger_path(self.dir, self.RUN))
        before = path.read_bytes()
        original = sl.os.replace

        def fail_replace(_source, _target):
            raise OSError("injected replace failure")

        sl.os.replace = fail_replace
        try:
            with self.assertRaises(OSError):
                self.create("not-written")
        finally:
            sl.os.replace = original
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(any(path.parent.glob("ledger.json.tmp.*")))
        self.assertNotIn("not-written#1", sl.load_ledger(
            self.dir, self.RUN)["nodes"])
        _, report = sl.op_recover(self.dir, self.RUN)
        self.assertIn("recoverable-abort", report["journal"])
        self.create("after-abort")
        self.assertIn("after-abort#1", sl.load_ledger(
            self.dir, self.RUN)["nodes"])

    def test_commit_append_failure_is_completed_on_next_mutation(self):
        original = sl.journal_append
        calls = 0

        def fail_commit(root, run_id, entry):
            nonlocal calls
            calls += 1
            if entry.get("phase") == "commit":
                raise OSError("injected commit append failure")
            return original(root, run_id, entry)

        sl.journal_append = fail_commit
        try:
            with self.assertRaises(OSError):
                self.create("committed-without-anchor")
        finally:
            sl.journal_append = original
        self.assertEqual(calls, 2)
        self.assertIn("committed-without-anchor#1", sl.load_ledger(
            self.dir, self.RUN)["nodes"])
        _, report = sl.op_recover(self.dir, self.RUN)
        self.assertIn("recoverable-commit", report["journal"])
        self.create("after-commit")
        self.assertEqual(sl.classify_journal(
            self.dir, self.RUN, sl.load_ledger(
                self.dir, self.RUN))["status"], "anchored")

    def test_wal_classifier_refuses_malformed_and_unknown_records(self):
        ledger = sl.load_ledger(self.dir, self.RUN)
        current = sl._file_sha256(sl.ledger_path(self.dir, self.RUN))
        sl.journal_append(self.dir, self.RUN, {
            "phase": "intent", "generation": 2,
            "snapshot_sha256": current})
        report = sl.classify_journal(self.dir, self.RUN, ledger)
        self.assertEqual(report["status"], "mismatch")
        self.assertIn("malformed", report["reason"])

        sl.journal_append(self.dir, self.RUN, {
            "phase": "mystery", "generation": 1,
            "snapshot_sha256": current})
        report = sl.classify_journal(self.dir, self.RUN, ledger)
        self.assertIn("unsupported", report["reason"])

        sl.journal_append(self.dir, self.RUN, {
            "phase": "intent", "base_generation": 1, "generation": 2,
            "snapshot_sha256": "1" * 64,
            "intended_snapshot_sha256": "2" * 64})
        report = sl.classify_journal(self.dir, self.RUN, ledger)
        self.assertIn("neither side", report["reason"])
        sl.repair_recoverable_journal(
            self.dir, self.RUN, ledger, report, "tester")


class TestBoundaryErrors(LedgerHarness):
    def test_input_and_capability_boundaries(self):
        finding = sl.Finding("VIOLATION", "E_TEST", "node#1", "bad")
        self.assertEqual(finding.as_dict()["code"], "E_TEST")
        for value in ("missing-colon", ":missing-type", "path:",
                      "path:/absolute", "path:C:\\absolute", "path:."):
            self.expect((sl.EXIT_SEMANTIC if "absolute" in value else
                         sl.EXIT_USAGE), sl.canon_scope_entry, value)
        self.expect(sl.EXIT_USAGE, sl.compute_fingerprint,
                    "outcome", "rev", "not-a-digest", [], "gate")
        self.expect(sl.EXIT_USAGE, sl._check_text, "", "empty")
        self.expect(sl.EXIT_SEMANTIC, sl._check_text,
                    "x" * (sl.MAX_TEXT_FIELD + 1), "long")
        ledger = sl.load_ledger(self.dir, self.RUN)
        self.expect(sl.EXIT_USAGE, sl.get_node, ledger, "missing-attempt")
        self.expect(sl.EXIT_USAGE, sl.get_node, ledger, "missing#1")
        self.expect(sl.EXIT_USAGE, sl.run_dir, self.dir, "bad run id")
        self.expect(sl.EXIT_USAGE, sl.ensure_runtime_directory,
                    self.dir, "bad run id")
        missing_root = tempfile.mkdtemp(prefix="swarm-missing-run-")
        self.addCleanup(shutil.rmtree, missing_root, ignore_errors=True)
        self.expect(sl.EXIT_USAGE, sl.ensure_runtime_directory,
                    missing_root, "missing")
        self.assertEqual(sl.capability_profile({
            "thread_creation": True, "thread_listing": True,
            "result_collection": True,
            "worktree_control": True})["tier"], "ledger-assisted")
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.capture_git_baseline(os.path.join(self.dir, "not-a-repo"))
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_USAGE)

    def test_init_and_create_argument_boundaries(self):
        invalid_capabilities = [
            ["missing-equals"], ["bad key=true"],
            ["same=true", "same=false"], ["value=maybe"],
        ]
        for index, capabilities in enumerate(invalid_capabilities):
            target = tempfile.mkdtemp(prefix="swarm-invalid-cap-")
            self.addCleanup(shutil.rmtree, target, ignore_errors=True)
            with self.assertRaises(sl.LedgerError) as ctx:
                sl.op_init(target, f"invalid-{index}", "test", "digest",
                           capabilities, "tester")
            self.assertEqual(ctx.exception.exit_code, sl.EXIT_USAGE)

        self.expect(sl.EXIT_USAGE, self.create, "bad id")
        self.expect(sl.EXIT_USAGE, self.create, "bad-class", klass="NOPE")
        self.expect(sl.EXIT_USAGE, self.create, "bad-effort", effort="minimal")
        self.expect(sl.EXIT_SEMANTIC, self.create, "pure-resource",
                    resources=["path:src"])
        self.expect(sl.EXIT_SEMANTIC, self.create, "dup-writer",
                    klass="ISOLATED", resources=["path:src"],
                    model="gpt-5.6-terra", effort="high", dup_group="g")
        self.expect(sl.EXIT_USAGE, self.create, "bad-join", join="quorum:0")

    def test_json_and_baseline_failure_edges(self):
        payload = Path(self.dir, "non-standard.json")
        payload.write_text('{"value": NaN}', encoding="utf-8")
        self.expect(sl.EXIT_CORRUPT, sl.safe_load_json, str(payload), 1000)
        missing = Path(self.dir, "missing.json")
        self.expect(sl.EXIT_CORRUPT, sl.safe_load_json, str(missing), 1000)
        baseline = sl.capture_git_baseline(str(REPO_ROOT))
        with self.assertRaises(sl.LedgerError) as ctx:
            sl.verify_git_baseline(str(REPO_ROOT), "0" * 40,
                                   baseline["dirty_digest"])
        self.assertEqual(ctx.exception.exit_code, sl.EXIT_AMBIGUOUS)
        self.assertIn("revision expected", ctx.exception.message)

    def test_operation_guard_boundaries(self):
        self.expect(sl.EXIT_USAGE, sl.op_init, self.dir, self.RUN,
                    "test", "digest", [], "tester")
        self.expect(sl.EXIT_USAGE, self.create, "reader-auth",
                    one_shot_authorization_file=__file__)
        self.create("reader")
        self.expect(sl.EXIT_USAGE, self.go, "reader#1", "NOT_A_STATE")
        self.expect(sl.EXIT_SEMANTIC, sl.op_record_dispatch,
                    self.dir, self.RUN, "tester", self.gen(), "reader#1")
        self.expect(sl.EXIT_SEMANTIC, sl.op_record_arm_dispatch,
                    self.dir, self.RUN, "tester", self.gen(), "reader#1")
        self.expect(sl.EXIT_SEMANTIC, sl.op_release_resources,
                    self.dir, self.RUN, "tester", self.gen(), "reader#1",
                    "nothing held")
        self.expect(sl.EXIT_USAGE, sl.op_reconcile,
                    self.dir, self.RUN, "tester", self.gen(), "reader#1",
                    "evidence", "bad-outcome")
        self.expect(sl.EXIT_USAGE, sl.op_set_disposition,
                    self.dir, self.RUN, "tester", self.gen(), "reader#1",
                    "bad-disposition", "evidence")


# ---------------------------------------------------------------------------
# CLI smoke tests (subprocess): exit codes are the machine contract
# ---------------------------------------------------------------------------
class TestCli(unittest.TestCase):
    def run_cli(self, *argv, cwd, env=None):
        return subprocess.run(
            [sys.executable, str(TOOL_PATH), *argv],
            cwd=cwd, capture_output=True, text=True, timeout=60, env=env)

    def test_cli_end_to_end(self):
        workdir = tempfile.mkdtemp(prefix="swarm-cli-")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        run = ["--root", workdir, "--run-id", "cli-run"]
        # global flags are accepted both before and after the subcommand
        out = self.run_cli("--root", workdir, "init", "--run-id",
                           "cli-global", "--task-type", "probe",
                           "--task-digest", "d", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("init", *run, "--task-type", "demo",
                           "--task-digest", "digest",
                           "--capability", "thread_creation=true",
                           "--capability", "thread_listing=true",
                           "--capability", "result_collection=true",
                           "--capability", "unique_launch_discovery=true",
                           "--capability", "one_shot_fence=true",
                           cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("capability tier", out.stdout)
        out = self.run_cli(
            "create-node", *run, "--expect-generation", "1",
            "--node-id", "scan", "--class", "PURE",
            "--model", "gpt-5.6-luna", "--effort", "low",
            "--outcome", "survey the repo", "--base-revision", "rev1",
            "--gate", "report delivered",
            "--launch-nonce", "nonce-cli-0001", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("validate", *run, cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("transition", *run, "--expect-generation", "2",
                           "scan#1", "RUNNING", cwd=workdir)
        self.assertEqual(out.returncode, 4)  # illegal transition
        out = self.run_cli("transition", *run, "--expect-generation", "99",
                           "scan#1", "READY", cwd=workdir)
        self.assertEqual(out.returncode, 5)  # stale generation
        out = self.run_cli("validate", "--json", *run, cwd=workdir)
        self.assertEqual(out.returncode, 0)
        parsed = json.loads(out.stdout)
        self.assertTrue(parsed["ok"])

        for generation, target in ((2, "READY"), (3, "CLAIMED"),
                                   (4, "LAUNCHING")):
            out = self.run_cli("transition", *run, "--expect-generation",
                               str(generation), "scan#1", target,
                               cwd=workdir)
            self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("record-dispatch", *run,
                           "--expect-generation", "5", "scan#1",
                           cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("transition", *run, "--expect-generation", "6",
                           "scan#1", "RUNNING", "--thread-id",
                           "thread-cli-scan", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        receipt = {
            "run_id": "cli-run", "node_id": "scan", "attempt": 1,
            "status": "SUCCEEDED", "thread_id": "thread-cli-scan",
            "model_effort": "gpt-5.6-luna/low", "base_revision": "rev1",
            "artifact": "reports/scan.md", "touched_paths": [],
            "commands": [], "processes": {"spawned": [],
                                                "remaining_live": []},
            "resources_released": [], "artifact_hashes": {},
            "descendant_thread_ids": [], "assumptions": [],
            "unresolved_risks": [], "cleanup_items": [],
        }
        receipt_path = Path(workdir, "receipt.json")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        out = self.run_cli("transition", *run, "--expect-generation", "7",
                           "scan#1", "SUCCEEDED", "--receipt",
                           str(receipt_path), cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("set-disposition", *run, "--expect-generation",
                           "8", "scan#1", "--disposition", "INTEGRATED",
                           "--evidence", "accepted by CLI test", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_cli("show", *run, cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("capability tier", out.stdout)
        out = self.run_cli("doctor", *run, cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("RECORDED-CONSISTENCY ONLY", out.stdout)
        out = self.run_cli("render-status", *run, cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("<!doctype html>", out.stdout)
        status_path = os.path.join(workdir, "status.html")
        out = self.run_cli("render-status", *run, "--output", status_path,
                           cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(Path(status_path).read_text("utf-8").startswith(
            "<!doctype html>"))
        out = self.run_cli("recover", *run, cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_cli_reference_and_git_baseline_commands(self):
        out = self.run_cli("verify-reference-set", cwd=REPO_ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads(out.stdout)
        self.assertEqual(report["protocol_version"], sl.PROTOCOL_VERSION)
        out = self.run_cli("capture-baseline", "--worktree", str(REPO_ROOT),
                           cwd=REPO_ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        baseline = json.loads(out.stdout)
        out = self.run_cli(
            "verify-baseline", "--worktree", str(REPO_ROOT),
            "--expected-revision", baseline["revision"],
            "--expected-dirty-digest", baseline["dirty_digest"],
            cwd=REPO_ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)

        fixture = tempfile.mkdtemp(prefix="swarm-cli-ignored-")
        self.addCleanup(shutil.rmtree, fixture, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
        subprocess.run(["git", "config", "user.name", "Swarm Tests"],
                       cwd=fixture, check=True)
        subprocess.run(["git", "config", "user.email",
                        "swarm@example.invalid"], cwd=fixture, check=True)
        Path(fixture, ".gitignore").write_text(
            ".coverage*\nignored.bin\n", encoding="utf-8")
        Path(fixture, "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=fixture, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"],
                       cwd=fixture, check=True)
        Path(fixture, "ignored.bin").write_bytes(b"stable")
        clean_env = dict(os.environ)
        clean_env.pop("COVERAGE_PROCESS_START", None)
        clean_env.pop("COVERAGE_FILE", None)
        out = self.run_cli(
            "capture-baseline", "--worktree", fixture, "--include-ignored",
            cwd=fixture, env=clean_env)
        self.assertEqual(out.returncode, 0, out.stderr)
        ignored = json.loads(out.stdout)
        out = self.run_cli(
            "verify-baseline", "--worktree", fixture,
            "--expected-revision", ignored["revision"],
            "--expected-dirty-digest", ignored["dirty_digest"],
            "--expected-ignored-digest", ignored["ignored_digest"],
            cwd=fixture, env=clean_env)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_cli_one_shot_release_and_reconcile_commands(self):
        workdir = tempfile.mkdtemp(prefix="swarm-cli-guarded-")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        run = ["--root", workdir, "--run-id", "guarded"]
        out = self.run_cli(
            "init", *run, "--task-type", "demo", "--task-digest", "digest",
            "--capability", "unique_launch_discovery=true",
            "--capability", "one_shot_fence=true", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)

        fp = sl.compute_fingerprint(
            "run once", "rev1", "none", ["db:sealed"], "sealed output")
        now = datetime.now(timezone.utc)
        authorization_path = Path(workdir, "authorization.json")
        authorization_path.write_text(json.dumps({
            "authorization_version": 1,
            "operator_id": "cli-operator",
            "run_id": "guarded",
            "node_id": "shot",
            "task_fingerprint": fp,
            "authorization_nonce": "authority-cli-shot-1",
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }), encoding="utf-8")

        def generation():
            return str(sl.load_ledger(workdir, "guarded")["generation"])

        def transition(ref, target, *extra):
            result = self.run_cli(
                "transition", *run, "--expect-generation", generation(),
                ref, target, *extra, cwd=workdir)
            self.assertEqual(result.returncode, 0, result.stderr)

        out = self.run_cli(
            "create-node", *run, "--expect-generation", generation(),
            "--node-id", "shot", "--class", "ONE_SHOT", "--model",
            "gpt-5.6-terra", "--effort", "high", "--outcome", "run once",
            "--base-revision", "rev1", "--gate", "sealed output",
            "--launch-nonce", "nonce-cli-shot-1", "--resource", "db:sealed",
            "--one-shot-authorization", str(authorization_path),
            cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        transition("shot#1", "READY")
        transition("shot#1", "CLAIMED")
        transition("shot#1", "LAUNCHING")
        out = self.run_cli("record-dispatch", *run, "--expect-generation",
                           generation(), "shot#1", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        transition("shot#1", "PREPARING", "--thread-id", "thread-shot-cli")
        transition("shot#1", "ARMED", "--arm-nonce", "arm-cli-shot-0001",
                   "--readiness-evidence", "fresh target verified")
        out = self.run_cli(
            "record-arm-dispatch", *run, "--expect-generation", generation(),
            "shot#1", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        transition("shot#1", "RUNNING", "--arm-acknowledged")

        out = self.run_cli(
            "create-node", *run, "--expect-generation", generation(),
            "--node-id", "writer", "--class", "ISOLATED", "--model",
            "gpt-5.6-terra", "--effort", "high", "--outcome", "prepare",
            "--base-revision", "rev1", "--gate", "ready",
            "--launch-nonce", "nonce-cli-writer-1", "--resource", "path:src",
            cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        transition("writer#1", "READY")
        transition("writer#1", "CLAIMED")
        out = self.run_cli(
            "release-resources", *run, "--expect-generation", generation(),
            "writer#1", "--evidence", "canceled before launch", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)

        out = self.run_cli(
            "create-node", *run, "--expect-generation", generation(),
            "--node-id", "uncertain", "--class", "PURE", "--model",
            "gpt-5.6-luna", "--effort", "low", "--outcome", "inspect",
            "--base-revision", "rev1", "--gate", "report",
            "--launch-nonce", "nonce-cli-unknown-1", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        transition("uncertain#1", "READY")
        transition("uncertain#1", "CLAIMED")
        transition("uncertain#1", "LAUNCHING")
        out = self.run_cli("record-dispatch", *run, "--expect-generation",
                           generation(), "uncertain#1", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        transition("uncertain#1", "UNKNOWN", "--evidence",
                   "complete listing found no delivery")
        out = self.run_cli(
            "reconcile", *run, "--expect-generation", generation(),
            "uncertain#1", "--evidence", "nonce absent in complete listing",
            "--outcome", "no_delivery_proven", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_fingerprint_command_is_deterministic(self):
        workdir = tempfile.mkdtemp(prefix="swarm-cli-")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        args = ["fingerprint", "--outcome", "Fix login bug",
                "--base-revision", "rev1", "--gate", "tests pass",
                "--write-scope", "path:src/auth"]
        first = self.run_cli(*args, cwd=workdir)
        second = self.run_cli(*args, cwd=workdir)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.returncode, 0)

    def test_help_is_success(self):
        workdir = tempfile.mkdtemp(prefix="swarm-cli-")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        out = self.run_cli("--help", cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Deterministic enforcement core", out.stdout)


if __name__ == "__main__":
    unittest.main()
