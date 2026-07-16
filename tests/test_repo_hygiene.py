"""Repository hygiene and release-consistency checks for GPT-5.6 Swarm.

These run in CI and locally: no runtime ledgers committed, no secret-shaped
strings in tracked files, resolvable documentation links, synchronized release
contracts, and a valid Codex skill package.
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
        authorization_schema = json.loads((REPO_ROOT / "schema" /
            "one-shot-authorization.schema.json").read_text("utf-8"))
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
        authorization = json.loads((REPO_ROOT / "examples" /
            "one-shot-authorization.example.json").read_text("utf-8"))
        self.assertEqual(set(authorization_schema["required"]),
                         set(sl.REQUIRED_AUTHORIZATION_KEYS))
        self.assertEqual(set(authorization),
                         set(sl.REQUIRED_AUTHORIZATION_KEYS))

    def test_release_contract_consistency(self):
        import importlib.util
        tool = SKILL_DIR / "scripts" / "swarm_ledger.py"
        spec = importlib.util.spec_from_file_location("swarm_release", tool)
        sl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sl)
        current = {
            "protocol": sl.PROTOCOL_VERSION,
            "schema": sl.SCHEMA_VERSION,
            "tool": sl.TOOL_VERSION,
        }
        skill = (SKILL_DIR / "SKILL.md").read_text("utf-8")
        enforcement = (SKILL_DIR / "references" /
                       "ENFORCEMENT.md").read_text("utf-8")
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text("utf-8")
        example = json.loads((REPO_ROOT / "examples" /
                              "ledger.example.json").read_text("utf-8"))
        self.assertIn(f"Protocol version `{current['protocol']}`", skill)
        self.assertIn(f"currently `{current['protocol']}`", enforcement)
        self.assertIn(f"currently `{current['tool']}`", enforcement)
        self.assertIn(
            f"protocol {current['protocol']} / schema {current['schema']} / "
            f"tool {current['tool']}", changelog)
        self.assertEqual(example["protocol_version"], current["protocol"])
        self.assertEqual(example["schema_version"], current["schema"])
        self.assertEqual(example["tool_version"], current["tool"])
        for relative in sl.REFERENCE_SET_FILES:
            text = (SKILL_DIR / relative).read_text("utf-8")
            self.assertEqual(text.count(sl.REFERENCE_SET_STAMP), 1, relative)

        workflow = (REPO_ROOT / ".github" / "workflows" /
                    "ci.yml").read_text("utf-8")
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("cache-dependency-path: requirements-dev.txt", workflow)
        self.assertIn("python -m coverage report", workflow)
        coverage = (REPO_ROOT / ".coveragerc").read_text("utf-8")
        self.assertRegex(coverage, r"(?m)^fail_under\s*=\s*85$")
        requirements = (REPO_ROOT / "requirements-dev.txt").read_text(
            "utf-8")
        self.assertRegex(requirements, r"(?m)^coverage==[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual((REPO_ROOT / "LICENSE").read_bytes(),
                         (SKILL_DIR / "LICENSE").read_bytes())
        for notice in (REPO_ROOT / "THIRD_PARTY_NOTICES.md",
                       SKILL_DIR / "THIRD_PARTY_NOTICES.md"):
            text = notice.read_text("utf-8")
            self.assertIn("0dd1cc4359aef62f33168f8824333aceed05eac5", text)
            self.assertIn("Forward Future", text)
            self.assertIn("MIT", text)

    def test_internal_markdown_links_resolve(self):
        link_re = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
        offenders = []
        for path in self.files:
            if path.suffix.lower() != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            for raw in link_re.findall(text):
                target = raw.strip().split(maxsplit=1)[0].strip("<>")
                if not target or target.startswith(("#", "http://", "https://",
                                                    "mailto:")):
                    continue
                relative = target.split("#", 1)[0]
                resolved = (path.parent / relative).resolve()
                if not resolved.exists():
                    offenders.append(f"{path.relative_to(REPO_ROOT)} -> {target}")
        self.assertEqual(offenders, [], "broken internal Markdown links")


if __name__ == "__main__":
    unittest.main()
