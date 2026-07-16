"""Deterministic, offline scenario tests for scripts/swarm_ledger.py.

Every scenario required by the Phase 1 brief maps to at least one test here,
and each enforced invariant has both an accepting and a rejecting side where
applicable. No network, no live model calls, no timing dependence.
"""

import importlib.util
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (REPO_ROOT / ".agents" / "skills" / "gpt-5-6-swarm" / "scripts" /
             "swarm_ledger.py")

spec = importlib.util.spec_from_file_location("swarm_ledger", TOOL_PATH)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)


class LedgerHarness(unittest.TestCase):
    """Shared helpers: a temp run with sane default host capabilities."""

    RUN = "test-run"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="swarm-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        sl.op_init(self.dir, self.RUN, "test", "digest-of-task",
                   ["thread_listing=true", "unique_launch_discovery=true",
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
        return sl.op_create_node(
            self.dir, self.RUN, "tester", self.gen(),
            node_id=node_id, klass=klass, model=model, effort=effort,
            outcome=outcome, base_revision=base, inputs_digest="none",
            gate=gate, launch_nonce=self.nonce(),
            resources=list(resources), dependencies=kw.pop("deps", []),
            join=kw.pop("join", "all"), **kw)

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
        receipt = {
            "run_id": self.RUN, "node_id": node["node_id"],
            "attempt": node["attempt"], "status": status,
            "thread_id": node["thread_id"],
            "model_effort": f"{node['model']}/{node['effort']}",
            "base_revision": node["fingerprint_inputs"]["base_revision"],
            "artifact": f"reports/{node['node_id']}.md",
            "touched_paths": [], "commands": [{"command": "pytest",
                                               "exit_code": 0}],
            "processes": {"spawned": [], "remaining_live": []},
            "resources_released": [f"{r['type']}:{r['id']}"
                                   for r in node["resources"]],
            "artifact_hashes": {}, "descendant_thread_ids": [],
            "assumptions": [], "unresolved_risks": [], "cleanup_items": [],
        }
        receipt.update(overrides)
        path = os.path.join(self.dir, f"receipt-{ref.replace('#', '-')}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh)
        return path

    def succeed(self, ref):
        self.go(ref, "SUCCEEDED", receipt_file=self.receipt_for(ref))

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

    def test_torn_journal_is_repaired_only_with_evidence(self):
        self.create("a")
        with open(sl.journal_path(self.dir, self.RUN), "ab") as handle:
            handle.write(b'{"partial":')
        _, findings = sl.op_validate(self.dir, self.RUN, check_journal=True)
        self.assertTrue(any(f.code == "A_JOURNAL_CORRUPT" for f in findings))
        sl.op_recover(self.dir, self.RUN, accept_current=True,
                      evidence="torn tail inspected; ledger matches chat mirror",
                      writer="tester")
        self.create("b")

    def test_existing_control_character_is_rejected(self):
        ledger = sl.load_ledger(self.dir, self.RUN)
        ledger["task"]["description_digest"] = "safe\x1b[31m"
        findings = sl.validate_ledger(ledger)
        self.assertEqual(sl.exit_code_for(findings), sl.EXIT_CORRUPT)


# ---------------------------------------------------------------------------
# CLI smoke tests (subprocess): exit codes are the machine contract
# ---------------------------------------------------------------------------
class TestCli(unittest.TestCase):
    def run_cli(self, *argv, cwd):
        return subprocess.run(
            [sys.executable, str(TOOL_PATH), *argv],
            cwd=cwd, capture_output=True, text=True, timeout=60)

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
                           "--capability", "unique_launch_discovery=true",
                           cwd=workdir)
        self.assertEqual(out.returncode, 0, out.stderr)
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
