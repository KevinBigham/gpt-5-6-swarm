"""Draft 2020-12 validation for shipped benchmark and contract examples.

The runtime remains standard-library-only. CI's development-dependency job
installs jsonschema and executes these tests; dependency-free matrix jobs skip
them while the executable Python validators remain authoritative.
"""

import copy
import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, ValidationError
except ImportError:  # dependency-free runtime/platform matrix
    Draft202012Validator = None
    ValidationError = Exception


REPO_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(Draft202012Validator is None,
                 "jsonschema is a pinned development-only dependency")
class TestPublishedSchemas(unittest.TestCase):
    def load(self, relative):
        return json.loads((REPO_ROOT / relative).read_text("utf-8"))

    def validator(self, relative):
        schema = self.load(relative)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_all_examples_validate(self):
        cases = [
            ("schema/frozen-contract.schema.json",
             "examples/frozen-contract.example.json"),
            ("schema/benchmark-case.schema.json",
             "examples/benchmark-case.example.json"),
            ("schema/benchmark-trial.schema.json",
             "examples/benchmark-serial-trial.example.json"),
            ("schema/benchmark-trial.schema.json",
             "examples/benchmark-swarm-trial.example.json"),
            ("schema/benchmark-report.schema.json",
             "examples/benchmark-report.example.json"),
        ]
        for schema, example in cases:
            with self.subTest(schema=schema, example=example):
                self.validator(schema).validate(self.load(example))

    def test_report_required_and_extra_properties_are_enforced(self):
        validator = self.validator("schema/benchmark-report.schema.json")
        report = self.load("examples/benchmark-report.example.json")
        missing = copy.deepcopy(report)
        missing.pop("evidence_status")
        with self.assertRaises(ValidationError):
            validator.validate(missing)
        extra = copy.deepcopy(report)
        extra["headline_speed_claim"] = "unsupported"
        with self.assertRaises(ValidationError):
            validator.validate(extra)

    def test_cross_field_constraints_reject_hostile_examples(self):
        case_validator = self.validator("schema/benchmark-case.schema.json")
        case = self.load("examples/benchmark-case.example.json")
        hostile_case = copy.deepcopy(case)
        hostile_case["treatments"]["serial"]["requested_parallel"] = 2
        with self.assertRaises(ValidationError):
            case_validator.validate(hostile_case)

        trial_validator = self.validator("schema/benchmark-trial.schema.json")
        trial = self.load("examples/benchmark-serial-trial.example.json")
        failed_pass = copy.deepcopy(trial)
        failed_pass["terminal_status"] = "FAILED"
        with self.assertRaises(ValidationError):
            trial_validator.validate(failed_pass)
        telemetry_without_observation = copy.deepcopy(trial)
        telemetry_without_observation["telemetry_sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            trial_validator.validate(telemetry_without_observation)


if __name__ == "__main__":
    unittest.main()
