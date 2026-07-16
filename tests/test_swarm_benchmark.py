"""Deterministic tests for paired benchmark validation and comparison."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (REPO_ROOT / "plugins" / "gpt-5-6-swarm" / "skills" /
             "gpt-5-6-swarm" / "scripts" / "swarm_benchmark.py")
spec = importlib.util.spec_from_file_location("swarm_benchmark", TOOL_PATH)
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


class TestSwarmBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = json.loads((REPO_ROOT / "examples" /
            "benchmark-case.example.json").read_text("utf-8"))
        cls.serial = json.loads((REPO_ROOT / "examples" /
            "benchmark-serial-trial.example.json").read_text("utf-8"))
        cls.swarm = json.loads((REPO_ROOT / "examples" /
            "benchmark-swarm-trial.example.json").read_text("utf-8"))

    def test_examples_validate_and_report_replays(self):
        sb.validate_case(self.case)
        sb.validate_trial(self.serial, self.case)
        sb.validate_trial(self.swarm, self.case)
        actual = sb.compare_trials(self.case, [self.serial, self.swarm])
        expected = json.loads((REPO_ROOT / "examples" /
            "benchmark-report.example.json").read_text("utf-8"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual["median_paired_speedup"], 1.6)
        self.assertEqual(actual["observed_peak_coverage"],
                         {"known": 0, "total": 2})
        markdown = sb.render_markdown(actual)
        self.assertIn("1.600x", markdown)
        self.assertIn("not observed host concurrency", markdown)

    def test_plan_is_deterministic_and_balanced(self):
        first = sb.make_plan(self.case)
        second = sb.make_plan(copy.deepcopy(self.case))
        self.assertEqual(first, second)
        self.assertEqual(len(first["pairs"]), 1)
        expanded = copy.deepcopy(self.case)
        expanded["pairing"]["measured_pairs"] = 4
        expanded["pairing"]["warmups"] = 2
        orders = [item["order"] for item in sb.make_plan(expanded)["pairs"]]
        self.assertEqual(orders.count("AB"), orders.count("BA"))

    def test_failures_and_missing_arms_remain_visible(self):
        failed = copy.deepcopy(self.swarm)
        failed["terminal_status"] = "UNKNOWN"
        failed["acceptance_passed"] = False
        failed["unknowns"] = 1
        report = sb.compare_trials(self.case, [self.serial, failed])
        self.assertEqual(report["eligible_pairs"], [])
        self.assertEqual(report["ineligible_pairs"][0]["reason"],
                         "non-success terminal state")
        self.assertEqual(report["arm_counts"]["swarm"]["unknown"], 1)
        report = sb.compare_trials(self.case, [self.serial])
        self.assertEqual(report["ineligible_pairs"][0]["reason"],
                         "missing arm")

    def test_pair_mismatch_and_duplicate_guards(self):
        mismatch = copy.deepcopy(self.swarm)
        mismatch["host_profile_sha256"] = "0" * 64
        report = sb.compare_trials(self.case, [self.serial, mismatch])
        self.assertEqual(report["ineligible_pairs"][0]["reason"],
                         "host profile mismatch")
        duplicate = copy.deepcopy(self.serial)
        duplicate["trial_id"] = "another-serial"
        with self.assertRaises(sb.BenchmarkError):
            sb.compare_trials(self.case, [self.serial, duplicate, self.swarm])
        duplicate = copy.deepcopy(self.serial)
        with self.assertRaises(sb.BenchmarkError):
            sb.compare_trials(self.case, [self.serial, duplicate])

    def test_trial_evidence_truth_guards(self):
        cases = []
        value = copy.deepcopy(self.swarm)
        value["scheduler_issued_peak"] = 4
        cases.append(value)
        value = copy.deepcopy(self.swarm)
        value["telemetry_sha256"] = "0" * 64
        cases.append(value)
        value = copy.deepcopy(self.swarm)
        value["observed_peak"] = 3
        value["telemetry_sha256"] = None
        cases.append(value)
        value = copy.deepcopy(self.swarm)
        value["usage"]["source"] = "billing"
        cases.append(value)
        value = copy.deepcopy(self.swarm)
        value["terminal_status"] = "FAILED"
        cases.append(value)
        value = copy.deepcopy(self.swarm)
        value["case_sha256"] = "0" * 64
        cases.append(value)
        value = copy.deepcopy(self.swarm)
        value["requested_parallel"] = 2
        cases.append(value)
        for trial in cases:
            with self.subTest(trial=trial):
                with self.assertRaises(sb.BenchmarkError):
                    sb.validate_trial(trial, self.case)

    def test_case_shape_and_treatment_guards(self):
        invalid = []
        case = copy.deepcopy(self.case)
        case["extra"] = True
        invalid.append(case)
        case = copy.deepcopy(self.case)
        case["treatments"]["serial"]["requested_parallel"] = 2
        invalid.append(case)
        case = copy.deepcopy(self.case)
        case["treatments"]["swarm"]["requested_parallel"] = 1
        invalid.append(case)
        case = copy.deepcopy(self.case)
        case["runtime"]["routing_requirement"] = "claimed"
        invalid.append(case)
        case = copy.deepcopy(self.case)
        case["pairing"]["measured_pairs"] = 0
        invalid.append(case)
        for case in invalid:
            with self.assertRaises(sb.BenchmarkError):
                sb.validate_case(case)

    def test_known_usage_and_observed_telemetry_are_explicit(self):
        trial = copy.deepcopy(self.swarm)
        trial["observed_peak"] = 3
        trial["telemetry_sha256"] = "0" * 64
        trial["usage"] = {"input_tokens": 100, "output_tokens": 20,
                           "credits": 1, "source": "authoritative receipt"}
        sb.validate_trial(trial, self.case)
        report = sb.compare_trials(self.case, [self.serial, trial])
        self.assertEqual(report["observed_peak_coverage"]["known"], 1)
        self.assertFalse(report["usage_complete"])

    def test_cli_validation_plan_compare_and_hostile_input(self):
        run = lambda *args: subprocess.run(
            [sys.executable, str(TOOL_PATH), *args], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=30)
        case_path = str(REPO_ROOT / "examples" /
                        "benchmark-case.example.json")
        serial_path = str(REPO_ROOT / "examples" /
                          "benchmark-serial-trial.example.json")
        swarm_path = str(REPO_ROOT / "examples" /
                         "benchmark-swarm-trial.example.json")
        self.assertEqual(run("validate-case", case_path).returncode, 0)
        self.assertEqual(run("validate-trial", case_path,
                             serial_path).returncode, 0)
        plan = run("plan", case_path)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("pair-001", plan.stdout)
        report = run("compare", case_path, serial_path, swarm_path,
                     "--format", "markdown")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("Swarm benchmark report", report.stdout)
        with tempfile.TemporaryDirectory(prefix="benchmark-hostile-") as tmp:
            duplicate = Path(tmp, "duplicate.json")
            duplicate.write_text('{"case_id":"x","case_id":"y"}',
                                 encoding="utf-8")
            result = run("validate-case", str(duplicate))
            self.assertEqual(result.returncode, 4)


if __name__ == "__main__":
    unittest.main()
