#!/usr/bin/env python3
"""Offline validation and comparison for paired Swarm benchmark evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import stat
import statistics
import sys


SCHEMA_VERSION = 1
MAX_BYTES = 512_000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CASE_KEYS = {
    "benchmark_schema_version", "case_id", "family_id", "scale", "track",
    "runtime", "task", "gate", "graph_sha256", "treatments", "pairing",
}
TRIAL_KEYS = {
    "benchmark_schema_version", "case_sha256", "trial_id", "pair_id",
    "arm", "replicate", "order", "warmup", "host_profile_sha256",
    "routing_status", "duration_ns", "timing_source", "terminal_status",
    "acceptance_passed", "gate_sha256", "artifact_sha256",
    "requested_parallel", "scheduler_issued_peak", "observed_peak",
    "telemetry_sha256", "usage", "unknowns", "retries",
    "exclusion_reason", "evidence",
}
EVIDENCE_KEYS = {"ledger", "journal", "doctor", "gate_log", "timing"}
USAGE_KEYS = {"input_tokens", "output_tokens", "credits", "source"}


class BenchmarkError(Exception):
    pass


def _duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError("duplicate JSON key: " + str(key))
        result[key] = value
    return result


def load_json(path):
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BenchmarkError("cannot inspect input: " + str(exc))
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BenchmarkError("input must be a regular, non-symlink file")
    if info.st_size > MAX_BYTES:
        raise BenchmarkError("input exceeds 512 KB")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_duplicate_keys)
    except BenchmarkError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("invalid JSON input: " + str(exc))


def _exact_dict(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise BenchmarkError(label + " has unexpected or missing fields")
    return value


def _string(value, label, maximum=1000):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise BenchmarkError(label + " must be a bounded non-empty string")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise BenchmarkError(label + " contains control characters")
    return value


def _identifier(value, label):
    if not ID_RE.fullmatch(_string(value, label, 128)):
        raise BenchmarkError(label + " is invalid")
    return value


def _digest(value, label, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise BenchmarkError(label + " must be a raw lowercase SHA-256")
    return value


def _int(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BenchmarkError(label + " must be an integer >= " + str(minimum))
    return value


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def case_digest(case):
    validate_case(case)
    return hashlib.sha256(canonical_bytes(case)).hexdigest()


def validate_case(case):
    _exact_dict(case, CASE_KEYS, "case")
    if isinstance(case["benchmark_schema_version"], bool) or \
            case["benchmark_schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError("unsupported benchmark_schema_version")
    _identifier(case["case_id"], "case_id")
    _identifier(case["family_id"], "family_id")
    scale = _exact_dict(case["scale"], {"value", "unit"}, "scale")
    _int(scale["value"], "scale.value", 1)
    _string(scale["unit"], "scale.unit", 64)
    if case["track"] not in {"fixed_graph_scheduling", "end_to_end_workflow"}:
        raise BenchmarkError("track is invalid")
    runtime = _exact_dict(
        case["runtime"],
        {"skill_sha256", "protocol_version", "tool_version",
         "routing_requirement"}, "runtime")
    _digest(runtime["skill_sha256"], "runtime.skill_sha256")
    _string(runtime["protocol_version"], "runtime.protocol_version", 64)
    _string(runtime["tool_version"], "runtime.tool_version", 64)
    if runtime["routing_requirement"] not in {"pinned", "host_selected"}:
        raise BenchmarkError("runtime.routing_requirement is invalid")
    task = _exact_dict(case["task"], {
        "base_revision", "prompt_sha256", "fixture_sha256"}, "task")
    _string(task["base_revision"], "task.base_revision", 256)
    _digest(task["prompt_sha256"], "task.prompt_sha256")
    _digest(task["fixture_sha256"], "task.fixture_sha256")
    gate = _exact_dict(case["gate"], {
        "evaluator_sha256", "scoring", "timeout_seconds"}, "gate")
    _digest(gate["evaluator_sha256"], "gate.evaluator_sha256")
    if gate["scoring"] not in {"deterministic", "blind_review"}:
        raise BenchmarkError("gate.scoring is invalid")
    _int(gate["timeout_seconds"], "gate.timeout_seconds", 1)
    _digest(case["graph_sha256"], "graph_sha256")
    treatments = _exact_dict(case["treatments"], {"serial", "swarm"},
                             "treatments")
    for arm in ("serial", "swarm"):
        treatment = _exact_dict(treatments[arm], {"requested_parallel"},
                                "treatments." + arm)
        _int(treatment["requested_parallel"],
             "treatments." + arm + ".requested_parallel", 1)
    if treatments["serial"]["requested_parallel"] != 1:
        raise BenchmarkError("serial requested_parallel must equal 1")
    if case["track"] == "fixed_graph_scheduling" and \
            treatments["swarm"]["requested_parallel"] <= 1:
        raise BenchmarkError("fixed-graph swarm requested_parallel must exceed 1")
    pairing = _exact_dict(case["pairing"], {
        "measured_pairs", "warmups", "order_seed",
        "preregistered_exclusions"}, "pairing")
    _int(pairing["measured_pairs"], "pairing.measured_pairs", 1)
    _int(pairing["warmups"], "pairing.warmups", 0)
    _int(pairing["order_seed"], "pairing.order_seed", 0)
    exclusions = pairing["preregistered_exclusions"]
    if not isinstance(exclusions, list) or not all(
            isinstance(item, str) and item for item in exclusions):
        raise BenchmarkError("preregistered_exclusions must be strings")
    return case


def validate_trial(trial, case=None):
    _exact_dict(trial, TRIAL_KEYS, "trial")
    if isinstance(trial["benchmark_schema_version"], bool) or \
            trial["benchmark_schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError("unsupported trial schema version")
    _digest(trial["case_sha256"], "case_sha256")
    _identifier(trial["trial_id"], "trial_id")
    _identifier(trial["pair_id"], "pair_id")
    if trial["arm"] not in {"serial", "swarm"}:
        raise BenchmarkError("arm is invalid")
    _int(trial["replicate"], "replicate", 1)
    if trial["order"] not in {"AB", "BA"}:
        raise BenchmarkError("order must be AB or BA")
    if not isinstance(trial["warmup"], bool):
        raise BenchmarkError("warmup must be boolean")
    _digest(trial["host_profile_sha256"], "host_profile_sha256")
    if trial["routing_status"] not in {"pinned", "host_selected"}:
        raise BenchmarkError("routing_status is invalid")
    _int(trial["duration_ns"], "duration_ns", 1)
    _string(trial["timing_source"], "timing_source", 256)
    if trial["terminal_status"] not in {
            "SUCCEEDED", "FAILED", "ABORTED", "CANCELED", "UNKNOWN"}:
        raise BenchmarkError("terminal_status is invalid")
    if not isinstance(trial["acceptance_passed"], bool):
        raise BenchmarkError("acceptance_passed must be boolean")
    _digest(trial["gate_sha256"], "gate_sha256")
    _digest(trial["artifact_sha256"], "artifact_sha256", nullable=True)
    requested = _int(trial["requested_parallel"], "requested_parallel", 1)
    issued = _int(trial["scheduler_issued_peak"],
                  "scheduler_issued_peak", 0)
    if issued > requested:
        raise BenchmarkError("scheduler_issued_peak exceeds requested_parallel")
    observed = trial["observed_peak"]
    if observed is not None:
        _int(observed, "observed_peak", 1)
        _digest(trial["telemetry_sha256"], "telemetry_sha256")
    elif trial["telemetry_sha256"] is not None:
        raise BenchmarkError("telemetry digest requires observed_peak")
    usage = _exact_dict(trial["usage"], USAGE_KEYS, "usage")
    known_usage = False
    for key in ("input_tokens", "output_tokens", "credits"):
        if usage[key] is not None:
            _int(usage[key], "usage." + key, 0)
            known_usage = True
    if known_usage:
        _string(usage["source"], "usage.source", 256)
    elif usage["source"] is not None:
        raise BenchmarkError("usage.source requires a known usage value")
    _int(trial["unknowns"], "unknowns", 0)
    _int(trial["retries"], "retries", 0)
    if trial["exclusion_reason"] is not None:
        _string(trial["exclusion_reason"], "exclusion_reason")
    evidence = _exact_dict(trial["evidence"], EVIDENCE_KEYS, "evidence")
    for key, digest in evidence.items():
        _digest(digest, "evidence." + key)
    if trial["acceptance_passed"] and trial["terminal_status"] != "SUCCEEDED":
        raise BenchmarkError("only SUCCEEDED trials may pass acceptance")
    if case is not None:
        validate_case(case)
        if trial["case_sha256"] != case_digest(case):
            raise BenchmarkError("trial is bound to a different case")
        expected = case["treatments"][trial["arm"]]["requested_parallel"]
        if trial["requested_parallel"] != expected:
            raise BenchmarkError("trial treatment does not match case")
        if trial["gate_sha256"] != case["gate"]["evaluator_sha256"]:
            raise BenchmarkError("trial gate does not match case")
        requirement = case["runtime"]["routing_requirement"]
        if trial["routing_status"] != requirement:
            raise BenchmarkError("trial routing does not match case")
    return trial


def make_plan(case):
    validate_case(case)
    pairing = case["pairing"]
    total = pairing["warmups"] + pairing["measured_pairs"]
    orders = ["AB" if index % 2 == 0 else "BA" for index in range(total)]
    random.Random(pairing["order_seed"]).shuffle(orders)
    return {
        "case_sha256": case_digest(case),
        "pairs": [{
            "pair_id": "pair-{:03d}".format(index + 1),
            "replicate": index + 1,
            "order": order,
            "warmup": index < pairing["warmups"],
        } for index, order in enumerate(orders)],
    }


def compare_trials(case, trials):
    validate_case(case)
    seen = set()
    groups = {}
    arm_counts = {arm: {"total": 0, "passed": 0, "unknown": 0}
                  for arm in ("serial", "swarm")}
    for trial in trials:
        validate_trial(trial, case)
        if trial["trial_id"] in seen:
            raise BenchmarkError("duplicate trial_id: " + trial["trial_id"])
        seen.add(trial["trial_id"])
        counts = arm_counts[trial["arm"]]
        counts["total"] += 1
        counts["passed"] += int(trial["acceptance_passed"])
        counts["unknown"] += int(trial["terminal_status"] == "UNKNOWN")
        group = groups.setdefault((trial["pair_id"], trial["replicate"]), {})
        if trial["arm"] in group:
            raise BenchmarkError("duplicate arm in pair: " +
                                 trial["pair_id"] + "/" + trial["arm"])
        group[trial["arm"]] = trial

    eligible = []
    ineligible = []
    for key in sorted(groups):
        pair = groups[key]
        reason = None
        if set(pair) != {"serial", "swarm"}:
            reason = "missing arm"
        elif pair["serial"]["warmup"] or pair["swarm"]["warmup"]:
            reason = "warmup"
        else:
            serial, swarm = pair["serial"], pair["swarm"]
            if serial["host_profile_sha256"] != swarm["host_profile_sha256"]:
                reason = "host profile mismatch"
            elif serial["routing_status"] != swarm["routing_status"]:
                reason = "routing mismatch"
            elif serial["gate_sha256"] != swarm["gate_sha256"]:
                reason = "gate mismatch"
            elif serial["terminal_status"] != "SUCCEEDED" or \
                    swarm["terminal_status"] != "SUCCEEDED":
                reason = "non-success terminal state"
            elif not serial["acceptance_passed"] or \
                    not swarm["acceptance_passed"]:
                reason = "acceptance failure"
            elif serial["exclusion_reason"] or swarm["exclusion_reason"]:
                reason = "preregistered exclusion"
        if reason:
            ineligible.append({"pair_id": key[0], "replicate": key[1],
                               "reason": reason})
            continue
        serial, swarm = pair["serial"], pair["swarm"]
        eligible.append({
            "pair_id": key[0], "replicate": key[1],
            "serial_duration_ns": serial["duration_ns"],
            "swarm_duration_ns": swarm["duration_ns"],
            "savings_ns": serial["duration_ns"] - swarm["duration_ns"],
            "speedup": serial["duration_ns"] / swarm["duration_ns"],
        })

    ratios = [item["speedup"] for item in eligible]
    savings = [item["savings_ns"] for item in eligible]
    observed = [trial for trial in trials if trial["observed_peak"] is not None]
    usage_complete = all(
        trial["usage"]["input_tokens"] is not None and
        trial["usage"]["output_tokens"] is not None
        for trial in trials) if trials else False
    return {
        "benchmark_report_version": 1,
        "case_sha256": case_digest(case),
        "track": case["track"],
        "planned_measured_pairs": case["pairing"]["measured_pairs"],
        "eligible_pairs": eligible,
        "ineligible_pairs": ineligible,
        "arm_counts": arm_counts,
        "median_paired_speedup": statistics.median(ratios) if ratios else None,
        "median_paired_savings_ns": statistics.median(savings) if savings else None,
        "wins_ties_losses": {
            "wins": sum(value > 1 for value in ratios),
            "ties": sum(value == 1 for value in ratios),
            "losses": sum(value < 1 for value in ratios),
        },
        "observed_peak_coverage": {
            "known": len(observed), "total": len(trials)},
        "usage_complete": usage_complete,
        "break_even_status": "insufficient_evidence",
        "warnings": [
            "Scheduler-issued peak is not observed host concurrency.",
            "Break-even claims require a preregistered scale series and uncertainty analysis.",
        ],
    }


def render_markdown(report):
    speedup = report["median_paired_speedup"]
    speedup_text = "UNKNOWN" if speedup is None else "{:.3f}x".format(speedup)
    lines = [
        "# Swarm benchmark report", "",
        "- Track: `{}`".format(report["track"]),
        "- Valid pairs: `{}/{}`".format(
            len(report["eligible_pairs"]), report["planned_measured_pairs"]),
        "- Median paired speedup: `{}`".format(speedup_text),
        "- Break-even status: `{}`".format(report["break_even_status"]),
        "", "| Pair | Serial ns | Swarm ns | Speedup |", "|---|---:|---:|---:|",
    ]
    for item in report["eligible_pairs"]:
        lines.append("| {} | {} | {} | {:.3f}x |".format(
            item["pair_id"], item["serial_duration_ns"],
            item["swarm_duration_ns"], item["speedup"]))
    if not report["eligible_pairs"]:
        lines.append("| — | — | — | UNKNOWN |")
    lines.extend(["", "## Warnings", ""] +
                 ["- " + warning for warning in report["warnings"]])
    return "\n".join(lines) + "\n"


def build_parser():
    parser = argparse.ArgumentParser(
        description="Validate and compare paired Swarm benchmark evidence.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_case_parser = sub.add_parser("validate-case")
    validate_case_parser.add_argument("case")
    validate_trial_parser = sub.add_parser("validate-trial")
    validate_trial_parser.add_argument("case")
    validate_trial_parser.add_argument("trial")
    plan = sub.add_parser("plan")
    plan.add_argument("case")
    compare = sub.add_parser("compare")
    compare.add_argument("case")
    compare.add_argument("trials", nargs="+")
    compare.add_argument("--format", choices=("json", "markdown"),
                         default="json")
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        case = load_json(args.case)
        if args.command == "validate-case":
            validate_case(case)
            result = {"ok": True, "case_sha256": case_digest(case)}
        elif args.command == "validate-trial":
            trial = load_json(args.trial)
            validate_trial(trial, case)
            result = {"ok": True, "trial_id": trial["trial_id"]}
        elif args.command == "plan":
            result = make_plan(case)
        else:
            result = compare_trials(case, [load_json(path)
                                           for path in args.trials])
            if args.format == "markdown":
                print(render_markdown(result), end="")
                return 0
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except BenchmarkError as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
