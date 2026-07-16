"""Repository hygiene checks required by the Phase 1 acceptance criteria.

These run in CI and locally: no runtime ledgers committed, no secret-shaped
strings in tracked files, and the skill still packages as a valid Codex skill
(frontmatter with name + description).
"""

import re
import json
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".agents" / "skills" / "gpt-5-6-swarm"

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                    # AWS access key id
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                 # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),        # GitHub fine-grained
    re.compile(r"sk-[A-Za-z0-9]{32,}"),                 # API secret keys
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),        # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM keys
]

TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".toml",
                 ".ndjson", ""}


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    tracked = set(out.stdout.splitlines())
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    if untracked.returncode == 0:
        tracked.update(untracked.stdout.splitlines())
    return [REPO_ROOT / line for line in sorted(tracked) if line]


class TestRepoHygiene(unittest.TestCase):
    def setUp(self):
        self.files = tracked_files()
        if self.files is None:  # not a git checkout (e.g. tarball install)
            self.files = [p for p in REPO_ROOT.rglob("*")
                          if p.is_file() and ".git" not in p.parts]

    def test_no_runtime_ledgers_tracked(self):
        offenders = [str(p) for p in self.files
                     if ".swarm" in p.parts and "runs" in p.parts]
        self.assertEqual(offenders, [],
                         "runtime ledgers must never be committed")

    def test_gitignore_covers_runtime_state(self):
        content = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".swarm/runs/", content)

    def test_no_secret_shaped_strings(self):
        offenders = []
        for path in self.files:
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{path}: {pattern.pattern}")
        self.assertEqual(offenders, [], "secret-shaped strings found")

    def test_skill_frontmatter_valid(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "frontmatter missing")
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^name:\s*\S+")
        self.assertRegex(frontmatter, r"(?m)^description:\s*\S+")
        keys = {line.split(":", 1)[0].strip()
                for line in frontmatter.splitlines() if ":" in line}
        self.assertEqual(keys, {"name", "description"},
                         "SKILL.md supports only name and description")

    def test_skill_interface_metadata(self):
        metadata = (SKILL_DIR / "agents" / "openai.yaml").read_text(
            encoding="utf-8")
        self.assertIn('display_name: "GPT-5.6 Swarm"', metadata)
        self.assertIn("$gpt-5-6-swarm", metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_no_compiled_python_artifacts_tracked(self):
        offenders = [str(path) for path in self.files
                     if path.suffix == ".pyc" or "__pycache__" in path.parts]
        self.assertEqual(offenders, [],
                         "compiled Python artifacts must not ship")

    def test_examples_are_semantically_valid(self):
        import importlib.util
        tool = (SKILL_DIR / "scripts" / "swarm_ledger.py")
        spec = importlib.util.spec_from_file_location("swarm_ledger_h", tool)
        sl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sl)
        example = REPO_ROOT / "examples" / "ledger.example.json"
        doc = json.loads(example.read_text(encoding="utf-8"))
        findings = sl.validate_ledger(doc)
        violations = [str(f) for f in findings if f.severity == "VIOLATION"]
        self.assertEqual(violations, [], "example ledger must validate clean")
        receipt = json.loads((REPO_ROOT / "examples" /
                              "receipt.example.json").read_text("utf-8"))
        sl.validate_receipt(doc["nodes"]["impl#1"], receipt,
                            doc["run_id"], "SUCCEEDED")

    def test_schema_contract_matches_runtime_and_examples(self):
        import importlib.util
        tool = SKILL_DIR / "scripts" / "swarm_ledger.py"
        spec = importlib.util.spec_from_file_location("swarm_ledger_schema", tool)
        sl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sl)
        ledger_schema = json.loads((REPO_ROOT / "schema" /
                                    "ledger.schema.json").read_text("utf-8"))
        receipt_schema = json.loads((REPO_ROOT / "schema" /
                                     "receipt.schema.json").read_text("utf-8"))
        example = json.loads((REPO_ROOT / "examples" /
                              "ledger.example.json").read_text("utf-8"))
        node = next(iter(example["nodes"].values()))
        node_schema = ledger_schema["definitions"]["node"]
        self.assertEqual(set(ledger_schema["required"]), set(example))
        self.assertEqual(set(node_schema["required"]),
                         set(node) - {"model_warning"})
        self.assertEqual(set(node_schema["properties"]["state"]["enum"]),
                         sl.STATES)
        self.assertEqual(set(node_schema["properties"]["class"]["enum"]),
                         sl.CLASSES)
        self.assertEqual(set(node_schema["properties"]["effort"]["enum"]),
                         sl.EFFORTS)
        self.assertEqual(set(receipt_schema["required"]),
                         set(sl.REQUIRED_RECEIPT_KEYS))
        self.assertEqual(set(receipt_schema["properties"]["status"]["enum"]),
                         {"SUCCEEDED", "FAILED", "ABORTED", "CANCELED"})


if __name__ == "__main__":
    unittest.main()
