#!/usr/bin/env python3
"""swarm_ledger.py - deterministic enforcement core for GPT-5.6 Swarm.

Turns the safety-critical invariants of the Swarm protocol (SKILL.md and
references/CONCURRENCY.md) into executable, offline-testable checks:

  * launch ledger with a legal-transition state machine
  * normalized task-fingerprint deduplication
  * launch-nonce and arm-nonce uniqueness (one-shot double-spend prevention)
  * one-active-owner and exclusive-resource-scope rules
  * receipt-gated known terminal outcomes after execution
  * ledger-generation monotonicity (compare-and-set on every mutation)
  * fail-closed UNKNOWN handling with explicit reconciliation
  * atomic persistence with crash recovery reporting
  * fail-closed protocol-reference compatibility checks
  * machine-readable host-capability and Git-baseline reporting

Design principles:

  * Python standard library only.
  * Single coordinator writer; short-lived per-invocation lock plus a
    generation compare-and-set guard against concurrent/stale writers.
  * Read-only by default. Every mutation is an explicit subcommand that
    validates pre-state, applies exactly one allowed change, validates
    post-state, and persists atomically (temp file + fsync + os.replace).
  * Ledger content is data, never code. Nothing in a ledger, receipt, or
    journal is ever executed, evaluated, or shelled out.
  * Ambiguity is preserved, not resolved: UNKNOWN stays UNKNOWN until a
    human/coordinator records reconciliation evidence.

Protocol version: 1.3.0   Schema version: 2   (see references/ENFORCEMENT.md)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone

TOOL_VERSION = "0.3.0"
PROTOCOL_VERSION = "1.3.0"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {2}
SUPPORTED_PROTOCOL_SERIES = {(1, 3)}

REFERENCE_SET_STAMP = f"Protocol reference set: `{PROTOCOL_VERSION}`."
REFERENCE_SET_FILES = (
    "SKILL.md",
    "DEPLOYMENT.md",
    "references/CONCURRENCY.md",
    "references/ENFORCEMENT.md",
    "references/HOSTS.md",
    "references/REPORTING.md",
    "references/ROUTES.md",
    "references/SCHEDULING.md",
)

MINIMUM_FANOUT_CAPABILITIES = (
    "thread_creation", "thread_listing", "result_collection",
)
HOST_INTEGRATED_CAPABILITIES = MINIMUM_FANOUT_CAPABILITIES + (
    "child_turn_read", "unique_launch_discovery", "cancel_interrupt",
    "model_selection", "effort_selection", "worktree_control",
)

# ---------------------------------------------------------------------------
# Exit codes (machine-readable contract; see references/ENFORCEMENT.md)
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_USAGE = 2          # bad arguments
EXIT_SHAPE = 3          # structurally invalid ledger/receipt (wrong types/keys)
EXIT_SEMANTIC = 4       # protocol invariant violation
EXIT_STALE = 5          # stale generation or lock conflict
EXIT_VERSION = 6        # unsupported protocol/schema version (fail closed)
EXIT_AMBIGUOUS = 7      # unresolved ambiguity requires reconciliation
EXIT_CORRUPT = 8        # unreadable, truncated, oversized, or adversarial input

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
STATES = {
    "PLANNED", "READY", "CLAIMED", "LAUNCHING", "PREPARING", "ARMED",
    "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "ABORTED", "UNKNOWN",
    "CANCELING",
}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "ABORTED", "UNKNOWN"}
# States that block a duplicate fingerprint or conflicting resource claim.
BLOCKING_STATES = {"CLAIMED", "LAUNCHING", "PREPARING", "ARMED", "RUNNING",
                   "CANCELING"}

CLASSES = {"PURE", "ISOLATED", "KEYED_IDEMPOTENT", "NON_IDEMPOTENT",
           "EXCLUSIVE_UNKNOWN", "ONE_SHOT"}
# Classes that may never be retried automatically (CONCURRENCY.md).
GUARDED_RETRY_CLASSES = {"NON_IDEMPOTENT", "EXCLUSIVE_UNKNOWN", "ONE_SHOT"}

EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
PROHIBITED_EFFORTS = {"ultra"}  # SKILL.md: Swarm does not use Ultra.
KNOWN_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}

DISPOSITIONS = {"CURRENT", "INVALIDATED", "SUPERSEDED", "REJECTED",
                "INTEGRATED"}

RECONCILE_OUTCOMES = {"no_delivery_proven", "execution_terminal_proven",
                      "unresolved"}

# (from_state, to_state) pairs that are ever legal; per-edge guards refine.
LEGAL_TRANSITIONS = {
    ("PLANNED", "READY"), ("PLANNED", "CANCELED"),
    ("READY", "CLAIMED"), ("READY", "CANCELED"),
    ("CLAIMED", "READY"), ("CLAIMED", "LAUNCHING"),
    ("CLAIMED", "CANCELED"),
    ("LAUNCHING", "RUNNING"), ("LAUNCHING", "PREPARING"),
    ("LAUNCHING", "CANCELED"), ("LAUNCHING", "CANCELING"),
    ("LAUNCHING", "UNKNOWN"),
    ("PREPARING", "ARMED"), ("PREPARING", "CANCELING"),
    ("PREPARING", "UNKNOWN"),
    ("ARMED", "RUNNING"), ("ARMED", "CANCELING"), ("ARMED", "UNKNOWN"),
    ("RUNNING", "SUCCEEDED"), ("RUNNING", "FAILED"), ("RUNNING", "ABORTED"),
    ("RUNNING", "UNKNOWN"), ("RUNNING", "CANCELING"),
    ("CANCELING", "CANCELED"), ("CANCELING", "ABORTED"),
    ("CANCELING", "UNKNOWN"),
}

# ---------------------------------------------------------------------------
# Limits and identifier grammar (adversarial-input hardening)
# ---------------------------------------------------------------------------
LEDGER_MAX_BYTES = 5_000_000
RECEIPT_MAX_BYTES = 512_000
AUTHORIZATION_MAX_BYTES = 32_000
MAX_JSON_DEPTH = 64
MAX_TEXT_FIELD = 4_000
IGNORED_BASELINE_MAX_FILE_BYTES = 50_000_000
IGNORED_BASELINE_MAX_TOTAL_BYTES = 250_000_000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
NODE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}#[1-9][0-9]*$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_RECEIPT_KEYS = (
    "run_id", "node_id", "attempt", "status", "thread_id", "model_effort",
    "base_revision", "artifact", "touched_paths", "commands", "processes",
    "resources_released", "artifact_hashes", "descendant_thread_ids",
    "assumptions", "unresolved_risks", "cleanup_items",
)

REQUIRED_AUTHORIZATION_KEYS = {
    "authorization_version", "operator_id", "run_id", "node_id",
    "task_fingerprint", "authorization_nonce", "issued_at", "expires_at",
}


class LedgerError(Exception):
    """Raised for every enforced failure; carries the exit code."""

    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


class Finding:
    __slots__ = ("severity", "code", "node", "message")

    def __init__(self, severity, code, node, message):
        self.severity = severity  # VIOLATION | AMBIGUOUS | WARNING
        self.code = code
        self.node = node
        self.message = message

    def as_dict(self):
        return {"severity": self.severity, "code": self.code,
                "node": self.node, "message": self.message}

    def __str__(self):
        where = f" [{self.node}]" if self.node else ""
        return f"{self.severity} {self.code}{where}: {self.message}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_timestamp(value, label):
    if not isinstance(value, str):
        raise LedgerError(EXIT_SHAPE, f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError(EXIT_SEMANTIC,
                          f"{label} is not valid ISO-8601: {exc}")
    if parsed.tzinfo is None:
        raise LedgerError(EXIT_SEMANTIC, f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Text canonicalization and fingerprints
# ---------------------------------------------------------------------------
def canon_text(text: str) -> str:
    """Deterministic normal form for free text used in fingerprints.

    NFC-normalizes, strips Unicode format characters (category Cf, which
    includes zero-width joiners used in homoglyph smuggling), casefolds, and
    collapses whitespace runs. This makes rewordings that differ only in
    case, spacing, or invisible characters collide. It deliberately does NOT
    attempt semantic paraphrase detection - that judgment stays with the
    coordinator (documented limitation).
    """
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.casefold()
    return " ".join(text.split())


def canon_scope_entry(entry: str) -> str:
    """Normalize one resource id of the form 'type:id'."""
    if ":" not in entry:
        raise LedgerError(EXIT_USAGE,
                          f"resource '{entry}' must be 'type:id'")
    rtype, rid = entry.split(":", 1)
    rtype = rtype.strip().lower()
    rid = rid.strip()
    if not rtype or not rid:
        raise LedgerError(EXIT_USAGE, f"resource '{entry}' is incomplete")
    if rtype == "path":
        if rid.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[/\\]", rid):
            raise LedgerError(EXIT_SEMANTIC,
                              f"path resource {rid!r} must be relative")
        parts = [p for p in rid.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise LedgerError(EXIT_SEMANTIC,
                              f"path resource '{rid}' may not contain '..'")
        if not parts:
            raise LedgerError(EXIT_USAGE, f"path resource '{rid}' is empty")
        rid = "/".join(parts)
    else:
        # Non-path resource names are logical identifiers, not
        # case-sensitive filesystem paths. Conservative casefolding prevents
        # aliases such as service:Payments and service:payments.
        rid = rid.casefold()
    return f"{rtype}:{rid}"


def compute_fingerprint(outcome: str, base_revision: str, inputs_digest: str,
                        write_scope, gate: str) -> str:
    digest = inputs_digest.strip().lower()
    if digest != "none" and not HEX64_RE.match(digest):
        raise LedgerError(EXIT_USAGE,
                          "inputs digest must be 64 lowercase hex chars or 'none'")
    scopes = []
    for raw in write_scope:
        scope = canon_scope_entry(raw)
        rtype, rid = scope.split(":", 1)
        scopes.append(f"{rtype}:{rid.casefold()}" if rtype == "path" else scope)
    doc = {
        "v": 1,
        "outcome": canon_text(outcome),
        "base": base_revision.strip(),
        "inputs": digest,
        # Conflict checks casefold paths across filesystems, so task identity
        # does the same. This may deduplicate two case-distinct POSIX paths,
        # which is the conservative cross-platform behavior.
        "scope": sorted(set(scopes)),
        "gate": canon_text(gate),
    }
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Packaged protocol compatibility and host/worktree capability evidence
# ---------------------------------------------------------------------------
def _skill_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verify_reference_set(skill_dir: str = None):
    """Fail closed when packaged normative documents are missing or mixed.

    Version stamps gate compatibility, not authorship or content integrity.
    Repository tests separately check links and cross-file claims.
    """
    base = os.path.abspath(skill_dir or _skill_dir())
    checked = []
    for relative in REFERENCE_SET_FILES:
        path = os.path.join(base, *relative.split("/"))
        try:
            _lstat_regular(path, "protocol reference")
        except LedgerError as exc:
            raise LedgerError(
                EXIT_VERSION,
                f"protocol reference {relative} is missing or unsafe: "
                f"{exc.message}")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise LedgerError(
                EXIT_VERSION, f"cannot read protocol reference {relative}: {exc}")
        try:
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                    raise LedgerError(
                        EXIT_VERSION,
                        f"protocol reference {relative} is not a bounded "
                        "regular file")
                try:
                    content = handle.read().decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise LedgerError(
                        EXIT_VERSION,
                        f"protocol reference {relative} is not UTF-8: {exc}")
        finally:
            if fd >= 0:
                os.close(fd)
        stamps = [line.strip() for line in content.splitlines()
                  if line.strip().startswith("Protocol reference set:")]
        if stamps != [REFERENCE_SET_STAMP]:
            raise LedgerError(
                EXIT_VERSION,
                f"protocol reference {relative} must contain exactly one "
                f"exact stamp {REFERENCE_SET_STAMP!r}; missing, duplicate, "
                "or mixed reference sets are refused")
        checked.append(relative)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "reference_set": PROTOCOL_VERSION,
        "checked": checked,
    }


def capability_profile(capabilities):
    """Derive an honest, display-only host tier from declared capabilities."""
    caps = capabilities or {}
    missing_minimum = [key for key in MINIMUM_FANOUT_CAPABILITIES
                       if caps.get(key) is not True]
    if missing_minimum:
        tier = "serial"
    elif all(caps.get(key) is True for key in HOST_INTEGRATED_CAPABILITIES):
        tier = "host-integrated"
    elif caps.get("worktree_control") is True:
        tier = "ledger-assisted"
    else:
        tier = "ledger-assisted-read-only"

    disabled = []
    if not (caps.get("model_selection") is True and
            caps.get("effort_selection") is True):
        disabled.append("model-specific-routing")
    if caps.get("unique_launch_discovery") is not True:
        disabled.append("guarded-retries")
    if caps.get("one_shot_fence") is not True:
        disabled.append("one-shot")
    if caps.get("cancel_interrupt") is not True:
        disabled.append("authoritative-cancel")
    if caps.get("worktree_control") is not True:
        disabled.append("isolated-write-fanout")
    if caps.get("resource_fencing") is not True:
        disabled.append("shared-resource-mutation")
    if caps.get("background_sessions") is not True:
        disabled.append("background-work")
    return {"tier": tier, "disabled": disabled,
            "missing_minimum": missing_minimum}


def _git(worktree, *args):
    try:
        result = subprocess.run(
            ["git", "-C", worktree, *args], capture_output=True,
            timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LedgerError(EXIT_USAGE,
                          f"cannot inspect Git worktree {worktree!r}: {exc}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise LedgerError(
            EXIT_USAGE,
            f"Git baseline command failed for {worktree!r}: {detail}")
    return result.stdout


def _ignored_content_digest(root: str):
    """Hash ignored file identities and bytes with explicit cost bounds."""
    raw = _git(root, "-c", "core.quotepath=false", "ls-files", "--others",
               "--ignored", "--exclude-standard", "-z", "--", ".",
               ":(exclude).swarm/runs/**")
    paths = sorted(path for path in raw.split(b"\0") if path)
    digest = hashlib.sha256()
    total = 0
    for relative in paths:
        candidate = os.path.join(root, os.fsdecode(relative))
        try:
            info = os.lstat(candidate)
        except OSError as exc:
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"ignored baseline changed during capture: {exc}")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        if stat.S_ISREG(info.st_mode):
            digest.update(b"file\0")
            digest.update(info.st_size.to_bytes(8, "big"))
            if info.st_size > IGNORED_BASELINE_MAX_FILE_BYTES:
                raise LedgerError(
                    EXIT_USAGE,
                    "ignored baseline includes a file larger than "
                    f"{IGNORED_BASELINE_MAX_FILE_BYTES} bytes; narrow the "
                    "worktree or use an external resource fence")
            total += info.st_size
            if total > IGNORED_BASELINE_MAX_TOTAL_BYTES:
                raise LedgerError(
                    EXIT_USAGE,
                    "ignored baseline exceeds the bounded total of "
                    f"{IGNORED_BASELINE_MAX_TOTAL_BYTES} bytes; use an "
                    "external manifest/fence for this worktree")
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(candidate, flags)
            except OSError as exc:
                raise LedgerError(
                    EXIT_AMBIGUOUS,
                    f"cannot read ignored baseline file safely: {exc}")
            with os.fdopen(fd, "rb") as handle:
                current = os.fstat(handle.fileno())
                if not stat.S_ISREG(current.st_mode) or \
                        (current.st_dev, current.st_ino, current.st_size) != \
                        (info.st_dev, info.st_ino, info.st_size):
                    raise LedgerError(
                        EXIT_AMBIGUOUS,
                        "ignored baseline file changed during capture")
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
                after = os.fstat(handle.fileno())
                if (after.st_dev, after.st_ino, after.st_size,
                        after.st_mtime_ns) != (
                        current.st_dev, current.st_ino, current.st_size,
                        current.st_mtime_ns):
                    raise LedgerError(
                        EXIT_AMBIGUOUS,
                        "ignored baseline file changed while being hashed")
        elif stat.S_ISLNK(info.st_mode):
            digest.update(b"symlink\0" + os.fsencode(os.readlink(candidate)))
        else:
            raise LedgerError(
                EXIT_AMBIGUOUS,
                "ignored baseline contains a non-regular, non-symlink entry")
    return "sha256:" + digest.hexdigest(), len(paths), total


def capture_git_baseline(worktree: str, include_ignored: bool = False):
    """Return stable HEAD and Git-visible drift evidence without mutation."""
    root = os.path.abspath(worktree)
    revision = _git(root, "rev-parse", "--verify", "HEAD").decode(
        "ascii", "strict").strip()
    status = _git(root, "-c", "core.quotepath=false", "status",
                  "--porcelain=v1", "-z", "--untracked-files=all",
                  "--ignore-submodules=none", "--", ".",
                  ":(exclude).swarm/runs/**")
    report = {
        "worktree": root,
        "revision": revision,
        "dirty": bool(status),
        "dirty_digest": "sha256:" + hashlib.sha256(status).hexdigest(),
    }
    if include_ignored:
        ignored, count, total = _ignored_content_digest(root)
        report.update({
            "ignored_digest": ignored,
            "ignored_file_count": count,
            "ignored_total_bytes": total,
        })
    return report


def verify_git_baseline(worktree: str, expected_revision: str,
                        expected_dirty_digest: str,
                        expected_ignored_digest: str = None):
    actual = capture_git_baseline(
        worktree, include_ignored=expected_ignored_digest is not None)
    mismatches = []
    if actual["revision"] != expected_revision:
        mismatches.append(
            f"revision expected {expected_revision}, got {actual['revision']}")
    if actual["dirty_digest"] != expected_dirty_digest:
        mismatches.append(
            "dirty-state digest changed (unaccounted worktree drift)")
    if expected_ignored_digest is not None and \
            actual.get("ignored_digest") != expected_ignored_digest:
        mismatches.append(
            "ignored-content digest changed (unaccounted ignored-file drift)")
    if mismatches:
        raise LedgerError(EXIT_AMBIGUOUS, "; ".join(mismatches))
    return actual


# ---------------------------------------------------------------------------
# Safe JSON loading (never executes content; rejects adversarial shapes)
# ---------------------------------------------------------------------------
def _lstat_regular(path: str, label: str) -> None:
    """Require an existing, non-symlink regular file before opening it.

    Descriptor-level fstat checks remain authoritative. This lstat guard also
    protects platforms without O_NOFOLLOW and gives deterministic failures for
    FIFOs, sockets, devices, and symlinks.
    """
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise LedgerError(EXIT_CORRUPT,
                          f"cannot inspect {label} {path}: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LedgerError(EXIT_CORRUPT,
                          f"{label} {path} must be a real regular file")


def _reject_duplicate_keys(pairs):
    seen = set()
    out = {}
    for key, value in pairs:
        if key in seen:
            raise LedgerError(EXIT_CORRUPT, f"duplicate JSON key: {key!r}")
        seen.add(key)
        out[key] = value
    return out


def _reject_json_constant(value):
    raise LedgerError(EXIT_CORRUPT,
                      f"non-standard JSON constant {value!r} is prohibited")


def _bracket_depth_ok(text: str, limit: int = MAX_JSON_DEPTH) -> bool:
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > limit:
                return False
        elif ch in "}]":
            depth -= 1
    return True


def safe_load_json(path: str, max_bytes: int):
    _lstat_regular(path, "JSON input")
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LedgerError(EXIT_CORRUPT, f"cannot safely open {path}: {exc}")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              f"{path} is not a regular file")
        size = info.st_size
        if size > max_bytes:
            raise LedgerError(EXIT_CORRUPT,
                              f"{path} is {size} bytes; cap is {max_bytes}")
        try:
            with os.fdopen(fd, "r", encoding="utf-8", errors="strict") as handle:
                fd = -1
                text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise LedgerError(EXIT_CORRUPT, f"cannot read {path}: {exc}")
    finally:
        if fd >= 0:
            os.close(fd)
    if not _bracket_depth_ok(text):
        raise LedgerError(EXIT_CORRUPT,
                          f"{path} exceeds nesting depth {MAX_JSON_DEPTH}")
    try:
        doc = json.loads(text, object_pairs_hook=_reject_duplicate_keys,
                         parse_constant=_reject_json_constant)
    except LedgerError:
        raise
    except json.JSONDecodeError as exc:
        raise LedgerError(EXIT_CORRUPT,
                          f"{path} is not valid JSON (truncated or corrupted): {exc}")
    if not isinstance(doc, dict):
        raise LedgerError(EXIT_CORRUPT, f"{path} top level must be an object")
    return doc


# ---------------------------------------------------------------------------
# Paths, lock, atomic persistence, journal
# ---------------------------------------------------------------------------
def run_dir(root: str, run_id: str) -> str:
    if not ID_RE.match(run_id):
        raise LedgerError(EXIT_USAGE, f"run id {run_id!r} fails id grammar")
    return os.path.join(root, ".swarm", "runs", run_id)


def ensure_runtime_directory(root: str, run_id: str, create=False) -> str:
    """Reject symlink/non-directory runtime components; optionally create."""
    if not ID_RE.match(run_id):
        raise LedgerError(EXIT_USAGE, f"run id {run_id!r} fails id grammar")
    current = os.path.abspath(root)
    for component in (".swarm", "runs", run_id):
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise LedgerError(EXIT_USAGE,
                                  f"no run directory at {current}")
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                info = os.lstat(current)
            else:
                info = os.lstat(current)
        except OSError as exc:
            raise LedgerError(EXIT_CORRUPT,
                              f"cannot inspect runtime path {current}: {exc}")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              f"runtime path {current} must be a real directory")
    return current


def ledger_path(root: str, run_id: str) -> str:
    return os.path.join(run_dir(root, run_id), "ledger.json")


def journal_path(root: str, run_id: str) -> str:
    return os.path.join(run_dir(root, run_id), "journal.ndjson")


def lock_dir(root: str, run_id: str) -> str:
    return os.path.join(run_dir(root, run_id), "lock")


def acquire_lock(root: str, run_id: str, writer: str) -> str:
    path = lock_dir(root, run_id)
    try:
        os.mkdir(path)
    except FileExistsError:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise LedgerError(
                EXIT_STALE,
                "ledger lock changed while being inspected; re-read and retry")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              "ledger lock path must be a real directory")
        holder = "unknown"
        owner_file = os.path.join(path, "owner.json")
        if os.path.exists(owner_file):
            try:
                holder = json.dumps(safe_load_json(owner_file, 10_000))
            except LedgerError:
                holder = "unreadable owner file"
        raise LedgerError(
            EXIT_STALE,
            "ledger lock is held: " + holder +
            " - if the holder crashed, inspect with 'recover' and clear with "
            "'recover --clear-lock' only after proving it is not live")
    token = secrets.token_hex(16)
    owner_path = os.path.join(path, "owner.json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(owner_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"writer": writer, "pid": os.getpid(),
                       "acquired_at": _now(), "token": token}, fh)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.rmdir(path)
        except OSError:
            pass
        raise
    return token


def release_lock(root: str, run_id: str, token=None, force=False) -> None:
    path = lock_dir(root, run_id)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise LedgerError(EXIT_STALE,
                          f"ledger lock no longer exists: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LedgerError(EXIT_CORRUPT,
                          "ledger lock path must be a real directory")
    owner = os.path.join(path, "owner.json")
    if not force:
        if not token:
            raise LedgerError(EXIT_STALE,
                              "lock release requires its ownership token")
        recorded = safe_load_json(owner, 10_000)
        if recorded.get("token") != token:
            raise LedgerError(EXIT_STALE,
                              "lock ownership changed; refusing ABA-unsafe release")
    if os.path.lexists(owner):
        os.remove(owner)
    os.rmdir(path)


def _file_sha256(path: str) -> str:
    _lstat_regular(path, "hash input")
    digest = hashlib.sha256()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LedgerError(EXIT_CORRUPT, f"cannot safely hash {path}: {exc}")
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              f"{path} is not a regular file")
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ledger_payload(ledger: dict) -> bytes:
    payload = json.dumps(ledger, sort_keys=True, indent=2).encode("utf-8")
    if len(payload) > LEDGER_MAX_BYTES:
        raise LedgerError(EXIT_SEMANTIC,
                          "ledger would exceed size cap; condense evidence fields")
    return payload


def _ledger_digest(ledger: dict) -> str:
    return hashlib.sha256(_ledger_payload(ledger)).hexdigest()


def atomic_write_ledger(root: str, run_id: str, ledger: dict) -> str:
    """Serialize + atomically replace ledger.json; return its sha256."""
    target = ledger_path(root, run_id)
    payload = _ledger_payload(ledger)
    parent = os.path.dirname(target)
    if os.path.lexists(target):
        info = os.lstat(target)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              "ledger target must be a regular file, not a "
                              "symlink, FIFO, socket, or device")
    fd, tmp = tempfile.mkstemp(prefix="ledger.json.tmp.", dir=parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:  # best-effort directory fsync (unavailable on some platforms)
        dfd = os.open(os.path.dirname(target), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return hashlib.sha256(payload).hexdigest()


def journal_append(root: str, run_id: str, entry: dict) -> None:
    line = json.dumps(entry, sort_keys=True) + "\n"
    path = journal_path(root, run_id)
    if os.path.lexists(path):
        _lstat_regular(path, "audit journal")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LedgerError(EXIT_CORRUPT,
                          f"cannot safely append journal {path}: {exc}")
    with os.fdopen(fd, "ab") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              "audit journal must be a regular file")
        handle.write(line.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def scan_journal(root: str, run_id: str):
    """Return (last entry, issue, last-good byte offset)."""
    path = journal_path(root, run_id)
    if not os.path.lexists(path):
        return None, None, 0
    _lstat_regular(path, "audit journal")
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LedgerError(EXIT_CORRUPT,
                          f"cannot safely read journal {path}: {exc}")
    last = None
    last_good = 0
    issue = None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              "audit journal must be a regular file")
        for raw in handle:
            if not raw.endswith(b"\n"):
                issue = "torn final journal line"
                break
            try:
                entry = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, LedgerError):
                issue = "corrupt journal line"
                break
            if not isinstance(entry, dict) or not isinstance(
                    entry.get("generation"), int) or isinstance(
                        entry.get("generation"), bool) or not HEX64_RE.match(
                            str(entry.get("snapshot_sha256", ""))):
                issue = "malformed journal entry"
                break
            last = entry
            last_good += len(raw)
    return last, issue, last_good


def journal_last_anchor(root: str, run_id: str):
    last, issue, _offset = scan_journal(root, run_id)
    if issue:
        raise LedgerError(
            EXIT_AMBIGUOUS,
            f"audit journal is not intact ({issue}); investigate read-only, "
            "then recover --accept-current with evidence")
    return last


def classify_journal(root: str, run_id: str, ledger: dict):
    """Classify journal/ledger agreement, including safe WAL repair cases."""
    anchor, issue, last_good = scan_journal(root, run_id)
    current = _file_sha256(ledger_path(root, run_id))
    report = {"status": "mismatch", "reason": None, "entry": anchor,
              "issue": issue, "last_good": last_good, "current": current}
    if anchor is None:
        report["status"] = "absent"
        report["reason"] = ("journal absent" if ledger["generation"] == 1 else
                            "journal deleted after mutations")
        return report

    phase = anchor.get("phase", "commit")
    if phase == "intent":
        base = anchor.get("base_generation")
        intended = anchor.get("intended_snapshot_sha256")
        valid_intent = (
            isinstance(base, int) and not isinstance(base, bool) and
            base >= 1 and anchor["generation"] == base + 1 and
            isinstance(intended, str) and HEX64_RE.match(intended))
        if not valid_intent:
            report["reason"] = "malformed write-ahead intent"
            return report
        if current == anchor["snapshot_sha256"] and \
                ledger["generation"] == base:
            report["status"] = "recoverable-abort"
            report["reason"] = "intent persisted but ledger replacement did not"
            return report
        if current == intended and ledger["generation"] == anchor["generation"]:
            report["status"] = "recoverable-commit"
            report["reason"] = "ledger replaced but commit record was interrupted"
            return report
        report["reason"] = "ledger matches neither side of the pending intent"
        return report

    if phase not in {"commit", "abort"}:
        report["reason"] = f"unsupported journal phase {phase!r}"
        return report
    if current != anchor["snapshot_sha256"] or \
            ledger["generation"] != anchor["generation"]:
        report["reason"] = "ledger hash/generation differs from journal anchor"
        return report
    if issue:
        report["status"] = "recoverable-tail"
        report["reason"] = issue
    else:
        report["status"] = "anchored"
        report["reason"] = "ledger matches committed journal anchor"
    return report


def repair_recoverable_journal(root: str, run_id: str, ledger: dict,
                               classification: dict, writer: str) -> None:
    """Finish or abandon an interrupted WAL record without changing ledger."""
    status = classification["status"]
    if status not in {"recoverable-abort", "recoverable-commit",
                      "recoverable-tail"}:
        return
    if classification["issue"]:
        truncate_journal(root, run_id, classification["last_good"])
    if status == "recoverable-tail":
        return
    intent = classification["entry"]
    journal_append(root, run_id, {
        "phase": "abort" if status == "recoverable-abort" else "commit",
        "at": _now(), "action": "wal-recovery", "writer": writer,
        "generation": ledger["generation"],
        "detail": {"recovered_intent_generation": intent["generation"],
                   "outcome": status},
        "snapshot_sha256": classification["current"],
    })


def truncate_journal(root: str, run_id: str, offset: int) -> None:
    path = journal_path(root, run_id)
    _lstat_regular(path, "audit journal")
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LedgerError(EXIT_CORRUPT,
                          f"cannot safely repair journal {path}: {exc}")
    with os.fdopen(fd, "r+b") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              "audit journal must be a regular file")
        handle.truncate(offset)
        handle.flush()
        os.fsync(handle.fileno())


def load_ledger(root: str, run_id: str) -> dict:
    ensure_runtime_directory(root, run_id)
    path = ledger_path(root, run_id)
    if not os.path.exists(path):
        raise LedgerError(EXIT_USAGE,
                          f"no ledger at {path}; run 'init' first")
    ledger = safe_load_json(path, LEDGER_MAX_BYTES)
    version = ledger.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise LedgerError(EXIT_SHAPE, "schema_version missing or not an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise LedgerError(
            EXIT_VERSION,
            f"ledger schema_version {version} is not supported by tool "
            f"{TOOL_VERSION} (supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}); "
            "refusing to interpret - upgrade the tool")
    protocol = ledger.get("protocol_version")
    if not isinstance(protocol, str) or not re.match(
            r"^[0-9]+\.[0-9]+\.[0-9]+$", protocol):
        raise LedgerError(EXIT_SHAPE,
                          "protocol_version missing or not MAJOR.MINOR.PATCH")
    major, minor, _patch = (int(part) for part in protocol.split("."))
    if (major, minor) not in SUPPORTED_PROTOCOL_SERIES:
        raise LedgerError(
            EXIT_VERSION,
            f"ledger protocol_version {protocol} is not supported by tool "
            f"{TOOL_VERSION}; supported series: "
            f"{sorted(f'{a}.{b}.x' for a, b in SUPPORTED_PROTOCOL_SERIES)}")
    return ledger


def check_external_writer(root: str, run_id: str, generation: int) -> None:
    """Compare current file hash to the last journal anchor (drift tripwire)."""
    ledger = load_ledger(root, run_id)
    if ledger["generation"] != generation:
        raise LedgerError(EXIT_STALE,
                          "ledger generation changed during drift inspection")
    status = classify_journal(root, run_id, ledger)
    if status["status"] == "absent":
        raise LedgerError(
            EXIT_AMBIGUOUS,
            "the audit journal is missing: initialization may have been "
            "interrupted or the trail was deleted. Investigate read-only, "
            "then 'recover --accept-current --evidence ...' to start a new "
            "anchor once accounted for.")
    if status["status"] != "anchored":
        raise LedgerError(
            EXIT_AMBIGUOUS,
            "ledger content does not match a committed journal anchor: "
            f"{status['reason']}. "
            "Investigate read-only, then 'recover --accept-current "
            "--evidence ...' to re-anchor once accounted for.")


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------
def node_key(node_id: str, attempt: int) -> str:
    return f"{node_id}#{attempt}"


def lineage(ledger: dict, node_id: str):
    out = []
    for key, node in ledger["nodes"].items():
        if node["node_id"] == node_id:
            out.append(node)
    return sorted(out, key=lambda n: n["attempt"])


def get_node(ledger: dict, ref: str) -> dict:
    if "#" not in ref:
        raise LedgerError(EXIT_USAGE,
                          f"node reference {ref!r} must be node_id#attempt")
    if ref not in ledger["nodes"]:
        raise LedgerError(EXIT_USAGE, f"unknown node {ref!r}")
    return ledger["nodes"][ref]


def unresolved_unknowns(ledger: dict):
    out = []
    for key, node in ledger["nodes"].items():
        if node["state"] == "UNKNOWN":
            rec = node.get("reconciliation") or {}
            if rec.get("status") != "resolved":
                out.append(key)
    return sorted(out)


def _paths_overlap(a: str, b: str) -> bool:
    # Case-fold conservatively so the same ledger stays safe when moved
    # between case-sensitive and case-insensitive filesystems.
    pa = [part.casefold() for part in a.split("/")]
    pb = [part.casefold() for part in b.split("/")]
    shorter, longer = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return longer[: len(shorter)] == shorter


def _path_contains(scope: str, candidate: str) -> bool:
    parent = [part.casefold() for part in scope.split("/")]
    child = [part.casefold() for part in candidate.split("/")]
    return len(parent) <= len(child) and child[:len(parent)] == parent


def resource_conflict(res_a: dict, res_b: dict) -> bool:
    if res_a["type"] != res_b["type"]:
        return False
    if res_a["type"] == "path":
        return _paths_overlap(res_a["id"], res_b["id"])
    return res_a["id"] == res_b["id"]


def find_resource_conflicts(ledger: dict, candidate_key: str, resources):
    conflicts = []
    for key, node in ledger["nodes"].items():
        if key == candidate_key or not node.get("holds_resources"):
            continue
        for held in node["resources"]:
            for wanted in resources:
                if resource_conflict(held, wanted):
                    conflicts.append(
                        (key, f"{held['type']}:{held['id']}",
                         f"{wanted['type']}:{wanted['id']}"))
    return conflicts


def parse_resources(entries, root=None):
    resources = []
    for raw in entries:
        canon = canon_scope_entry(raw)
        rtype, rid = canon.split(":", 1)
        if rtype == "path" and root is not None:
            root_real = os.path.realpath(root)
            resolved = os.path.realpath(os.path.join(root_real, rid))
            try:
                inside = os.path.commonpath([root_real, resolved]) == root_real
            except ValueError:
                inside = False
            if not inside:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    f"path resource {rid!r} resolves outside the run root")
            rid = os.path.relpath(resolved, root_real).replace("\\", "/")
        resources.append({"type": rtype, "id": rid, "mode": "exclusive"})
    ordered = sorted(resources, key=lambda r: (r["type"], r["id"]))
    if resources != ordered:
        raise LedgerError(
            EXIT_SEMANTIC,
            "resources must be declared in canonical sorted order "
            "(CONCURRENCY.md: acquire multiple resources in canonical order)")
    return resources


def verify_resource_bindings(root: str, resources) -> None:
    """Re-resolve path scopes immediately before claim/launch."""
    root_real = os.path.realpath(root)
    for resource in resources:
        if resource["type"] != "path":
            continue
        resolved = os.path.realpath(os.path.join(root_real, resource["id"]))
        try:
            inside = os.path.commonpath([root_real, resolved]) == root_real
        except ValueError:
            inside = False
        rebound = (os.path.relpath(resolved, root_real).replace("\\", "/")
                   if inside else None)
        if not inside or rebound != resource["id"]:
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"resource path binding changed for {resource['id']!r}; "
                "a symlink/rename race may alias another scope, so launch is "
                "frozen until the declaration is rebuilt")


def _check_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(EXIT_USAGE, f"{label} must be a non-empty string")
    if len(value) > MAX_TEXT_FIELD:
        raise LedgerError(EXIT_SEMANTIC,
                          f"{label} exceeds {MAX_TEXT_FIELD} chars; store a "
                          "reference or hash, not the full content")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise LedgerError(EXIT_CORRUPT, f"{label} contains control characters")
    return value


# ---------------------------------------------------------------------------
# Receipt validation
# ---------------------------------------------------------------------------
def validate_receipt(node: dict, receipt: dict, run_id: str,
                     expect_status: str) -> bool:
    """Validate a terminal receipt; return whether all scopes were released."""
    if not isinstance(receipt, dict):
        raise LedgerError(EXIT_SHAPE, "receipt must be a JSON object")
    for key in REQUIRED_RECEIPT_KEYS:
        if key not in receipt:
            raise LedgerError(EXIT_SHAPE, f"receipt missing key {key!r}")
    extras = sorted(set(receipt) - set(REQUIRED_RECEIPT_KEYS))
    if extras:
        raise LedgerError(EXIT_SHAPE,
                          f"receipt has unsupported keys {extras}")
    string_fields = ("run_id", "node_id", "status", "thread_id",
                     "model_effort", "base_revision", "artifact")
    for key in string_fields:
        value = receipt[key]
        if not isinstance(value, str):
            raise LedgerError(EXIT_SHAPE,
                              f"receipt {key} must be a string")
        if len(value) > MAX_TEXT_FIELD or any(
                ord(ch) < 32 and ch not in "\n\t" for ch in value):
            raise LedgerError(EXIT_CORRUPT,
                              f"receipt {key} contains oversized or control "
                              "character content")
    if not isinstance(receipt["attempt"], int) or \
            isinstance(receipt["attempt"], bool):
        raise LedgerError(EXIT_SHAPE, "receipt attempt must be an integer")
    list_fields = ("touched_paths", "resources_released",
                   "descendant_thread_ids", "assumptions",
                   "unresolved_risks")
    for key in list_fields:
        if not isinstance(receipt[key], list) or not all(
                isinstance(item, str) for item in receipt[key]):
            raise LedgerError(EXIT_SHAPE,
                              f"receipt {key} must be a list of strings")
    hashes = receipt["artifact_hashes"]
    if not isinstance(hashes, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in hashes.items()):
        raise LedgerError(EXIT_SHAPE,
                          "receipt artifact_hashes must map strings to strings")
    for artifact, digest in hashes.items():
        if not artifact.strip() or not re.match(r"^sha256:[0-9a-f]{64}$", digest):
            raise LedgerError(
                EXIT_SEMANTIC,
                "artifact hashes must use sha256 followed by 64 lowercase "
                "hex characters")
        if os.path.isabs(artifact) or re.match(r"^[A-Za-z]:[/\\]", artifact):
            raise LedgerError(EXIT_SEMANTIC,
                              "artifact hash paths must be relative")
        canonical_artifact = canon_scope_entry(
            f"path:{artifact}").split(":", 1)[1]
        if canonical_artifact != artifact.replace("\\", "/"):
            raise LedgerError(EXIT_SEMANTIC,
                              "artifact hash paths must be canonical")
    if receipt["run_id"] != run_id:
        raise LedgerError(EXIT_SEMANTIC, "receipt run_id does not match run")
    if receipt["node_id"] != node["node_id"] or \
            receipt["attempt"] != node["attempt"]:
        raise LedgerError(EXIT_SEMANTIC,
                          "receipt node identity/attempt mismatch")
    if receipt["status"] != expect_status:
        raise LedgerError(EXIT_SEMANTIC,
                          f"receipt status {receipt['status']!r} does not "
                          f"match transition target {expect_status!r}")
    if receipt["thread_id"] != node.get("thread_id"):
        raise LedgerError(EXIT_SEMANTIC,
                          "receipt thread_id does not match the recorded owner")
    if receipt["base_revision"] != node["fingerprint_inputs"]["base_revision"]:
        raise LedgerError(EXIT_SEMANTIC,
                          "receipt base_revision does not match the pinned base")
    expected_effort = f"{node['model']}/{node['effort']}"
    if receipt["model_effort"] != expected_effort:
        raise LedgerError(EXIT_SEMANTIC,
                          f"receipt model_effort {receipt['model_effort']!r} "
                          f"!= recorded {expected_effort!r}")
    procs = receipt["processes"]
    if not isinstance(procs, dict) or \
            set(procs) != {"spawned", "remaining_live"} or \
            not isinstance(procs.get("spawned"), list) or \
            not isinstance(procs.get("remaining_live"), list) or \
            not all(isinstance(item, str) for item in procs.get("spawned", [])) or \
            not all(isinstance(item, str)
                    for item in procs.get("remaining_live", [])):
        raise LedgerError(EXIT_SHAPE,
                          "receipt processes must contain string-list "
                          "spawned/remaining_live fields")
    if not isinstance(receipt["commands"], list):
        raise LedgerError(EXIT_SHAPE, "receipt commands must be a list")
    for cmd in receipt["commands"]:
        if not isinstance(cmd, dict) or \
                not isinstance(cmd.get("command"), str) or \
                not cmd.get("command").strip() or \
                not isinstance(cmd.get("exit_code"), int) or \
                isinstance(cmd.get("exit_code"), bool):
            raise LedgerError(EXIT_SHAPE,
                              "each receipt command needs a non-empty command "
                              "and integer exit_code")
        if set(cmd) - {"command", "exit_code", "result"}:
            raise LedgerError(EXIT_SHAPE,
                              "receipt command has unsupported keys")
        if "result" in cmd and not isinstance(cmd["result"], str):
            raise LedgerError(EXIT_SHAPE,
                              "receipt command result must be a string")
    if not isinstance(receipt["cleanup_items"], list):
        raise LedgerError(EXIT_SHAPE,
                          "receipt cleanup_items must be a list")
    if expect_status == "SUCCEEDED" and not receipt["artifact"]:
        raise LedgerError(EXIT_SEMANTIC,
                          "SUCCEEDED requires a non-empty artifact reference")
    if procs["remaining_live"]:
        raise LedgerError(
            EXIT_SEMANTIC,
            f"{expect_status} with live processes remaining is not "
            "accounted-for terminal completion; use CANCELING or UNKNOWN")
    if receipt["descendant_thread_ids"]:
        raise LedgerError(EXIT_SEMANTIC,
                          "nested delegation is prohibited in Swarm; "
                          "descendant_thread_ids must be empty")
    held = {f"{r['type']}:{r['id']}" for r in node["resources"]}
    released = set()
    if len(receipt["resources_released"]) != len(
            set(receipt["resources_released"])):
        raise LedgerError(EXIT_SEMANTIC,
                          "resources_released may not contain duplicates")
    for entry in receipt["resources_released"]:
        released.add(canon_scope_entry(entry))
    if not released.issubset(held):
        raise LedgerError(EXIT_SEMANTIC,
                          f"resources_released {sorted(released)} includes "
                          f"unowned scopes; held scopes are {sorted(held)}")
    fully_released = released == held
    if released and not fully_released:
        raise LedgerError(
            EXIT_SEMANTIC,
            "partial resource release cannot be represented safely; report "
            "none or every held scope")
    if expect_status in {"SUCCEEDED", "CANCELED"} and not fully_released:
        raise LedgerError(EXIT_SEMANTIC,
                          f"{expect_status} must reconcile every held scope; "
                          f"released {sorted(released)}, held {sorted(held)}")
    for item in receipt["cleanup_items"]:
        if not isinstance(item, dict) or not item.get("item") or \
                not item.get("owner") or set(item) != {"item", "owner"} or \
                not isinstance(item["item"], str) or \
                not isinstance(item["owner"], str):
            raise LedgerError(EXIT_SEMANTIC,
                              "each cleanup item needs 'item' and 'owner'")
    touched = []
    if len(receipt["touched_paths"]) != len(set(receipt["touched_paths"])):
        raise LedgerError(EXIT_SEMANTIC,
                          "touched_paths may not contain duplicates")
    for path in receipt["touched_paths"]:
        if not path or os.path.isabs(path) or re.match(r"^[A-Za-z]:[/\\]", path):
            raise LedgerError(EXIT_SEMANTIC,
                              f"touched path {path!r} must be relative")
        canonical = canon_scope_entry(f"path:{path}").split(":", 1)[1]
        touched.append(canonical)
    path_scopes = [r["id"] for r in node["resources"]
                   if r["type"] == "path"]
    if node["class"] == "PURE" and touched:
        raise LedgerError(EXIT_SEMANTIC,
                          "PURE work may not report touched paths")
    for path in touched:
        if not any(_path_contains(scope, path) for scope in path_scopes):
            raise LedgerError(
                EXIT_SEMANTIC,
                f"touched path {path!r} is outside the node's declared "
                "path resource scopes")
    if node["class"] == "PURE" and hashes:
        raise LedgerError(EXIT_SEMANTIC,
                          "PURE work may not claim local artifact hashes")
    if expect_status == "SUCCEEDED" and path_scopes and not hashes:
        raise LedgerError(
            EXIT_SEMANTIC,
            "SUCCEEDED work with local path scopes requires at least one "
            "artifact hash")
    for artifact in hashes:
        if not any(_path_contains(scope, artifact) for scope in path_scopes):
            raise LedgerError(
                EXIT_SEMANTIC,
                f"artifact hash path {artifact!r} is outside the node's "
                "declared path resource scopes")
    return fully_released


def verify_receipt_artifacts(node: dict, receipt: dict, worktree: str):
    """Recompute declared artifact hashes from regular, in-scope files."""
    hashes = receipt["artifact_hashes"]
    if not hashes:
        raise LedgerError(EXIT_SEMANTIC,
                          "artifact verification requires non-empty hashes")
    root_real = os.path.realpath(worktree)
    verified = {}
    for relative, expected in sorted(hashes.items()):
        candidate = os.path.join(root_real, *relative.split("/"))
        resolved = os.path.realpath(candidate)
        try:
            inside = os.path.commonpath([root_real, resolved]) == root_real
        except ValueError:
            inside = False
        rebound = (os.path.relpath(resolved, root_real).replace("\\", "/")
                   if inside else None)
        if not inside or rebound != relative:
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"artifact path binding changed for {relative!r}; refusing "
                "symlinked or out-of-tree evidence")
        actual = "sha256:" + _file_sha256(resolved)
        if actual != expected:
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"artifact hash mismatch for {relative!r}: receipt says "
                f"{expected}, current bytes are {actual}")
        verified[relative] = actual
    return {
        "status": "verified", "verified_at": _now(),
        "worktree": root_real,
        "receipt_sha256": hashlib.sha256(json.dumps(
            receipt, sort_keys=True).encode()).hexdigest(),
        "artifact_hashes": verified,
    }


def op_verify_artifacts(root, run_id, ref, receipt_file, worktree,
                        expect_status="SUCCEEDED"):
    ledger = load_ledger(root, run_id)
    findings = _blocking_semantic_findings(validate_ledger(ledger))
    if findings:
        raise LedgerError(EXIT_SEMANTIC,
                          "refusing verification against an invalid ledger")
    node = get_node(ledger, ref)
    receipt = safe_load_json(receipt_file, RECEIPT_MAX_BYTES)
    validate_receipt(node, receipt, run_id, expect_status)
    return verify_receipt_artifacts(node, receipt, worktree)


# ---------------------------------------------------------------------------
# Mutation framework
# ---------------------------------------------------------------------------
def _blocking_semantic_findings(findings):
    return [f for f in findings if f.severity == "VIOLATION"]


def validate_one_shot_authorization(authorization, *, run_id, node_id,
                                    fingerprint, now=None):
    """Validate a bounded, task-bound operator authorization record.

    The record is deliberately not called a signature: it is structured
    operator evidence supplied through a user-owned channel, not proof of
    identity against a trusted key.
    """
    if not isinstance(authorization, dict):
        raise LedgerError(EXIT_SHAPE,
                          "one-shot authorization must be a JSON object")
    if set(authorization) != REQUIRED_AUTHORIZATION_KEYS:
        missing = sorted(REQUIRED_AUTHORIZATION_KEYS - set(authorization))
        extra = sorted(set(authorization) - REQUIRED_AUTHORIZATION_KEYS)
        raise LedgerError(
            EXIT_SHAPE,
            f"one-shot authorization keys mismatch; missing={missing}, "
            f"unsupported={extra}")
    if authorization["authorization_version"] != 1 or isinstance(
            authorization["authorization_version"], bool):
        raise LedgerError(EXIT_VERSION,
                          "authorization_version must be integer 1")
    if not isinstance(authorization["operator_id"], str) or not ID_RE.match(
            authorization["operator_id"]):
        raise LedgerError(EXIT_SHAPE, "operator_id fails identifier grammar")
    if authorization["run_id"] != run_id or \
            authorization["node_id"] != node_id:
        raise LedgerError(EXIT_SEMANTIC,
                          "one-shot authorization is bound to another run/node")
    if authorization["task_fingerprint"] != fingerprint:
        raise LedgerError(EXIT_SEMANTIC,
                          "one-shot authorization task fingerprint mismatch")
    nonce = authorization["authorization_nonce"]
    if not isinstance(nonce, str) or not NONCE_RE.match(nonce):
        raise LedgerError(EXIT_SHAPE,
                          "authorization_nonce fails nonce grammar")
    issued = _parse_utc_timestamp(authorization["issued_at"], "issued_at")
    expires = _parse_utc_timestamp(authorization["expires_at"], "expires_at")
    current = now or datetime.now(timezone.utc)
    if expires <= issued or expires - issued > timedelta(minutes=15):
        raise LedgerError(
            EXIT_SEMANTIC,
            "one-shot authorization lifetime must be positive and no more "
            "than 15 minutes")
    if issued > current + timedelta(minutes=5):
        raise LedgerError(EXIT_SEMANTIC,
                          "one-shot authorization issued_at is too far ahead")
    if current >= expires:
        raise LedgerError(EXIT_SEMANTIC,
                          "one-shot authorization has expired")
    return {
        **authorization,
        "source_sha256": hashlib.sha256(json.dumps(
            authorization, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest(),
        "recorded_at": _now(),
    }


def mutate(root, run_id, writer, expect_generation, action, mutator):
    """Lock -> load -> drift check -> CAS -> pre-validate -> apply ->
    post-validate -> atomic persist -> journal -> unlock."""
    token = acquire_lock(root, run_id, writer)
    try:
        ledger = load_ledger(root, run_id)
        journal_state = classify_journal(root, run_id, ledger)
        repair_recoverable_journal(root, run_id, ledger, journal_state, writer)
        check_external_writer(root, run_id, ledger["generation"])
        if ledger["generation"] != expect_generation:
            raise LedgerError(
                EXIT_STALE,
                f"stale generation: ledger is at {ledger['generation']}, "
                f"caller expected {expect_generation}; re-read before writing")
        pre = _blocking_semantic_findings(validate_ledger(ledger))
        if pre:
            raise LedgerError(
                EXIT_SEMANTIC,
                "refusing to mutate a ledger with existing violations:\n" +
                "\n".join(str(f) for f in pre))
        detail = mutator(ledger)
        post = _blocking_semantic_findings(validate_ledger(ledger))
        if post:
            raise LedgerError(
                EXIT_SEMANTIC,
                "mutation aborted; it would create violations:\n" +
                "\n".join(str(f) for f in post))
        ledger["generation"] += 1
        ledger["tool_version"] = TOOL_VERSION
        ledger["updated_at"] = _now()
        previous_snapshot = _file_sha256(ledger_path(root, run_id))
        intended_snapshot = _ledger_digest(ledger)
        journal_append(root, run_id, {
            "phase": "intent", "at": ledger["updated_at"],
            "action": action, "writer": writer,
            "base_generation": ledger["generation"] - 1,
            "generation": ledger["generation"], "detail": detail,
            "snapshot_sha256": previous_snapshot,
            "intended_snapshot_sha256": intended_snapshot,
        })
        snapshot = atomic_write_ledger(root, run_id, ledger)
        if snapshot != intended_snapshot:
            raise LedgerError(EXIT_CORRUPT,
                              "atomic ledger snapshot digest changed")
        journal_append(root, run_id, {
            "phase": "commit", "at": ledger["updated_at"],
            "action": action, "writer": writer,
            "generation": ledger["generation"], "detail": detail,
            "snapshot_sha256": snapshot,
        })
        return ledger
    finally:
        release_lock(root, run_id, token=token)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def op_init(root, run_id, task_type, task_digest, capabilities, writer):
    # Refuse a partially upgraded skill before creating any runtime state.
    verify_reference_set()
    rdir = ensure_runtime_directory(root, run_id, create=True)
    token = acquire_lock(root, run_id, writer)
    try:
        if os.path.lexists(ledger_path(root, run_id)):
            raise LedgerError(EXIT_USAGE,
                              f"run {run_id!r} already initialized")
        unexpected = [name for name in os.listdir(rdir) if name != "lock"]
        if unexpected:
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"run directory contains pre-existing state {unexpected}; "
                "refusing initialization")
        caps = {}
        for pair in capabilities:
            if "=" not in pair:
                raise LedgerError(EXIT_USAGE,
                                  f"capability {pair!r} must be key=value")
            key, value = pair.split("=", 1)
            key = key.strip()
            if not ID_RE.match(key):
                raise LedgerError(EXIT_USAGE,
                                  f"capability key {key!r} fails id grammar")
            if key in caps:
                raise LedgerError(EXIT_USAGE,
                                  f"capability {key!r} was declared twice")
            normalized = value.strip().lower()
            if normalized not in {"true", "false"}:
                raise LedgerError(EXIT_USAGE,
                                  f"capability {pair!r} must end in true or false")
            caps[key] = normalized == "true"
        ledger = {
            "schema_version": SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "tool_version": TOOL_VERSION,
            "run_id": run_id,
            "task": {"type": _check_text(task_type, "task type"),
                     "description_digest": _check_text(task_digest, "task digest")},
            "host_capabilities": caps,
            "created_at": _now(),
            "updated_at": _now(),
            "generation": 1,
            "nonces": {"launch": {}, "arm": {}, "authorization": {}},
            "nodes": {},
        }
        snapshot = atomic_write_ledger(root, run_id, ledger)
        journal_append(root, run_id, {
            "phase": "commit", "at": ledger["created_at"],
            "action": "init", "writer": writer,
            "generation": 1, "detail": {"run_id": run_id},
            "snapshot_sha256": snapshot,
        })
        return ledger
    finally:
        release_lock(root, run_id, token=token)


def op_create_node(root, run_id, writer, expect_generation, *, node_id,
                   klass, model, effort, outcome, base_revision,
                   inputs_digest, gate, launch_nonce, resources,
                   dependencies, join, supersedes=None,
                   authorize_retry=None, dup_group=None,
                   supplied_fingerprint=None,
                   one_shot_authorization_file=None):
    if not ID_RE.match(node_id):
        raise LedgerError(EXIT_USAGE, f"node id {node_id!r} fails id grammar")
    if klass not in CLASSES:
        raise LedgerError(EXIT_USAGE, f"class must be one of {sorted(CLASSES)}")
    if effort in PROHIBITED_EFFORTS:
        raise LedgerError(
            EXIT_SEMANTIC,
            "effort 'ultra' is prohibited for Swarm nodes: it delegates "
            "outside the root scheduler (SKILL.md model roster)")
    if effort not in EFFORTS:
        raise LedgerError(EXIT_USAGE, f"effort must be one of {sorted(EFFORTS)}")
    if not NONCE_RE.match(launch_nonce):
        raise LedgerError(EXIT_USAGE, "launch nonce fails nonce grammar")
    _check_text(outcome, "outcome")
    _check_text(gate, "gate")
    _check_text(base_revision, "base revision")
    parsed_resources = parse_resources(resources, root)
    if klass == "PURE" and parsed_resources:
        raise LedgerError(EXIT_SEMANTIC,
                          "PURE nodes may not declare mutation resources")
    if klass != "PURE" and not parsed_resources:
        raise LedgerError(
            EXIT_SEMANTIC,
            f"class {klass} must declare at least one exclusive resource")
    if dup_group and klass != "PURE":
        raise LedgerError(EXIT_SEMANTIC,
                          "intentional duplicate groups are PURE-only")
    scope_ids = [f"{r['type']}:{r['id']}" for r in parsed_resources]
    fingerprint = compute_fingerprint(outcome, base_revision, inputs_digest,
                                      scope_ids, gate)
    if supplied_fingerprint and supplied_fingerprint != fingerprint:
        raise LedgerError(EXIT_SEMANTIC,
                          "supplied fingerprint does not match the one computed "
                          "from the declared inputs (possible tampering)")
    if join != "all" and join != "any" and not re.match(r"^quorum:[1-9]\d*$", join):
        raise LedgerError(EXIT_USAGE, "join must be all, any, or quorum:N")
    if klass != "ONE_SHOT" and one_shot_authorization_file:
        raise LedgerError(EXIT_USAGE,
                          "--one-shot-authorization is valid only for ONE_SHOT")
    authorization_source = None
    if one_shot_authorization_file:
        authorization_source = safe_load_json(
            one_shot_authorization_file, AUTHORIZATION_MAX_BYTES)

    def mutator(ledger):
        authorization = None
        caps = ledger.get("host_capabilities", {})
        if klass in GUARDED_RETRY_CLASSES and \
                caps.get("unique_launch_discovery") is not True:
            raise LedgerError(
                EXIT_SEMANTIC,
                f"class {klass} requires the unique_launch_discovery host "
                "capability; narrow the route to read-only fan-out or serial "
                "work (SKILL.md preflight)")
        if klass == "ONE_SHOT" and caps.get("one_shot_fence") is not True:
            raise LedgerError(
                EXIT_SEMANTIC,
                "class ONE_SHOT requires one_shot_fence=true: declare it only "
                "after verifying a fresh output target, target-side "
                "idempotency key, transaction, or effective external fence")
        if authorization_source is not None:
            authorization = validate_one_shot_authorization(
                authorization_source, run_id=run_id, node_id=node_id,
                fingerprint=fingerprint)
        if klass == "ONE_SHOT" and not authorization:
            raise LedgerError(
                EXIT_SEMANTIC,
                "ONE_SHOT requires --one-shot-authorization supplied through "
                "an operator-owned channel; an agent must never mint its own "
                "authority")
        if authorization:
            authorization_nonce = authorization["authorization_nonce"]
            if authorization_nonce in ledger["nonces"]["authorization"] or \
                    authorization_nonce in ledger["nonces"]["launch"] or \
                    authorization_nonce in ledger["nonces"]["arm"]:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "authorization nonce was already issued; authority records "
                    "are immutable and single-use")
        pending = unresolved_unknowns(ledger)
        if pending and klass != "PURE":
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"launch freeze: unresolved UNKNOWN nodes {pending} consume "
                "reconciliation capacity; only PURE nodes may be created "
                "until they are reconciled (references/SCHEDULING.md)")
        if launch_nonce in ledger["nonces"]["launch"] or \
                launch_nonce in ledger["nonces"]["arm"] or \
                launch_nonce in ledger["nonces"]["authorization"]:
            raise LedgerError(EXIT_SEMANTIC,
                              f"nonce {launch_nonce!r} was already issued; "
                              "nonces are immutable and single-use")
        prior = lineage(ledger, node_id)
        attempt = 1
        if prior:
            last = prior[-1]
            if last["state"] not in TERMINAL_STATES:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    f"attempt {last['attempt']} of {node_id} is "
                    f"{last['state']}; a new attempt may begin only after the "
                    "previous execution is terminal")
            if last["state"] == "UNKNOWN":
                rec = last.get("reconciliation") or {}
                if rec.get("status") != "resolved":
                    raise LedgerError(
                        EXIT_AMBIGUOUS,
                        f"{node_key(node_id, last['attempt'])} is UNKNOWN and "
                        "unreconciled; relaunch is prohibited until evidence "
                        "resolves it")
            if last["class"] == "ONE_SHOT":
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "ONE_SHOT lineages are never retried; define a distinct "
                    "new action only with fresh explicit authority")
            if last["fingerprint"] != fingerprint:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "attempt N+1 must preserve the logical task fingerprint; "
                    "use a new node id for changed work")
            if klass in GUARDED_RETRY_CLASSES and not authorize_retry:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    f"class {klass} is never retried automatically; pass "
                    "--authorize-retry with explicit user/root authority "
                    "evidence (CONCURRENCY.md)")
            attempt = last["attempt"] + 1
        dependencies_unique = list(dict.fromkeys(dependencies))
        if dependencies_unique != list(dependencies):
            raise LedgerError(EXIT_SEMANTIC,
                              "dependencies must be unique; duplicates cannot "
                              "count toward a join or quorum")
        if node_id in dependencies_unique:
            raise LedgerError(EXIT_SEMANTIC,
                              "a node may not depend on itself")
        missing_deps = [dep for dep in dependencies_unique
                        if not lineage(ledger, dep)]
        if missing_deps:
            raise LedgerError(EXIT_SEMANTIC,
                              f"dependencies do not exist: {missing_deps}")
        if join.startswith("quorum:") and \
                int(join.split(":", 1)[1]) > len(dependencies_unique):
            raise LedgerError(EXIT_SEMANTIC,
                              "quorum cannot exceed unique dependency count")
        matched_supersedes = False
        for key, other in ledger["nodes"].items():
            if other["fingerprint"] != fingerprint:
                continue
            if dup_group and other.get("dup_group") == dup_group and \
                    klass == "PURE" and other["class"] == "PURE":
                continue  # intentional any/quorum research duplication
            if other["state"] in BLOCKING_STATES or \
                    other["state"] in {"PLANNED", "READY"}:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    f"duplicate task fingerprint: {key} already covers this "
                    "work and is not terminal; observe or wait, never duplicate")
            if other["state"] == "UNKNOWN":
                rec = other.get("reconciliation") or {}
                if rec.get("status") != "resolved":
                    raise LedgerError(
                        EXIT_AMBIGUOUS,
                        f"duplicate task fingerprint: {key} is UNKNOWN and "
                        "unreconciled; relaunch is prohibited")
            if other["state"] == "SUCCEEDED" and \
                    other.get("artifact_disposition") == "CURRENT":
                if supersedes != key:
                    raise LedgerError(
                        EXIT_SEMANTIC,
                        f"{key} already SUCCEEDED with a CURRENT artifact; "
                        "reuse it after revalidating its base, or pass "
                        f"--supersedes {key} to replace it deliberately")
                matched_supersedes = True
        if supersedes and not matched_supersedes:
            raise LedgerError(
                EXIT_SEMANTIC,
                f"--supersedes {supersedes!r} must identify the CURRENT "
                "artifact with the same fingerprint")
        key = node_key(node_id, attempt)
        node = {
            "node_id": node_id, "attempt": attempt, "class": klass,
            "one_shot": klass == "ONE_SHOT", "model": model, "effort": effort,
            "state": "PLANNED", "fingerprint": fingerprint,
            "fingerprint_inputs": {
                "outcome": outcome, "base_revision": base_revision,
                "inputs_digest": inputs_digest.strip().lower(),
                "write_scope": scope_ids, "gate": gate,
            },
            "launch_nonce": launch_nonce, "thread_id": None,
            "dispatch_issued": False,
            "arm": {"nonce": None, "dispatched": False,
                    "acknowledged": False, "spent": False},
            "resources": parsed_resources, "holds_resources": False,
            "dependencies": dependencies_unique, "join": join,
            "receipt": None, "receipt_sha256": None,
            "artifact_verification": None,
            "artifact_disposition": None,
            "reconciliation": None, "supersedes": supersedes,
            "retry_authorization": authorize_retry,
            "dup_group": dup_group,
            "one_shot_authorization": authorization,
            "created_at": _now(), "last_transition_at": _now(),
            "history": [{"from": None, "to": "PLANNED", "at": _now(),
                         "note": "created"}],
        }
        if model not in KNOWN_MODELS:
            node["model_warning"] = "model outside the documented roster"
        ledger["nodes"][key] = node
        ledger["nonces"]["launch"][launch_nonce] = key
        if authorization:
            ledger["nonces"]["authorization"][
                authorization["authorization_nonce"]] = key
        return {"node": key, "fingerprint": fingerprint}

    return mutate(root, run_id, writer, expect_generation, "create-node",
                  mutator)


def _deps_satisfied(ledger, node):
    deps, join = list(dict.fromkeys(node["dependencies"])), node["join"]
    if not deps:
        return True, ""
    passed = 0
    for dep in deps:
        ok = any(n["state"] == "SUCCEEDED" and
                 n.get("artifact_disposition") in {"CURRENT", "INTEGRATED"}
                 for n in lineage(ledger, dep))
        passed += 1 if ok else 0
    if join == "all":
        return passed == len(deps), f"{passed}/{len(deps)} dependencies passed"
    if join == "any":
        return passed >= 1, f"{passed}/{len(deps)} dependencies passed"
    need = int(join.split(":", 1)[1])
    return passed >= need, f"{passed}/{len(deps)} passed, quorum {need}"


def op_record_dispatch(root, run_id, writer, expect_generation, ref):
    def mutator(ledger):
        node = get_node(ledger, ref)
        if node["state"] != "LAUNCHING":
            raise LedgerError(EXIT_SEMANTIC,
                              "dispatch may be recorded only in LAUNCHING")
        if node["dispatch_issued"]:
            raise LedgerError(EXIT_SEMANTIC,
                              "dispatch already recorded; a second create call "
                              "for the same node is a duplicate launch")
        node["dispatch_issued"] = True
        return {"node": ref, "dispatch_issued": True}
    return mutate(root, run_id, writer, expect_generation, "record-dispatch",
                  mutator)


def op_record_arm_dispatch(root, run_id, writer, expect_generation, ref):
    def mutator(ledger):
        node = get_node(ledger, ref)
        if not node["one_shot"]:
            raise LedgerError(EXIT_SEMANTIC, "arm dispatch is one-shot only")
        if node["state"] != "ARMED":
            raise LedgerError(EXIT_SEMANTIC,
                              "the arm message may be sent only from ARMED")
        if node["arm"]["dispatched"]:
            raise LedgerError(
                EXIT_SEMANTIC,
                "the arm message may be sent exactly once; never resend an "
                "ambiguously delivered arm (CONCURRENCY.md one-shot barrier)")
        node["arm"]["dispatched"] = True
        return {"node": ref, "arm_dispatched": True}
    return mutate(root, run_id, writer, expect_generation,
                  "record-arm-dispatch", mutator)


def op_transition(root, run_id, writer, expect_generation, ref, target, *,
                  evidence=None, thread_id=None, receipt_file=None,
                  arm_nonce=None, readiness_evidence=None,
                  arm_acknowledged=False, termination_evidence=None,
                  verification_worktree=None):
    if target not in STATES:
        raise LedgerError(EXIT_USAGE, f"unknown state {target!r}")

    def mutator(ledger):
        node = get_node(ledger, ref)
        source = node["state"]
        if (source, target) not in LEGAL_TRANSITIONS:
            raise LedgerError(EXIT_SEMANTIC,
                              f"illegal transition {source} -> {target}")
        pending = unresolved_unknowns(ledger)
        if target == "LAUNCHING" and pending and node["class"] != "PURE":
            raise LedgerError(
                EXIT_AMBIGUOUS,
                f"launch freeze: unresolved UNKNOWN nodes {pending}; only "
                "PURE nodes may launch until reconciliation")
        # -- per-edge guards -------------------------------------------------
        if source == "PLANNED" and target == "READY":
            ok, why = _deps_satisfied(ledger, node)
            if not ok:
                raise LedgerError(EXIT_SEMANTIC,
                                  f"dependencies/join not satisfied: {why}")
        if source == "READY" and target == "CLAIMED":
            ok, why = _deps_satisfied(ledger, node)
            if not ok:
                raise LedgerError(EXIT_SEMANTIC,
                                  f"dependencies/join no longer satisfied: {why}")
            verify_resource_bindings(root, node["resources"])
            conflicts = find_resource_conflicts(ledger, ref, node["resources"])
            if conflicts:
                lines = [f"{k} holds {h} vs wanted {w}" for k, h, w in conflicts]
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "resource scope conflict; overlapping ownership must "
                    "serialize:\n" + "\n".join(lines))
            node["holds_resources"] = bool(node["resources"])
        if source == "CLAIMED" and target == "LAUNCHING":
            ok, why = _deps_satisfied(ledger, node)
            if not ok:
                raise LedgerError(EXIT_SEMANTIC,
                                  f"dependencies/join no longer satisfied: {why}")
            verify_resource_bindings(root, node["resources"])
            if node["class"] != "PURE" and not node["holds_resources"]:
                raise LedgerError(EXIT_SEMANTIC,
                                  "non-PURE work may launch only while all "
                                  "declared mutation resources are held")
        if source == "CLAIMED" and target == "CANCELED" and \
                node["holds_resources"]:
            raise LedgerError(EXIT_SEMANTIC,
                              "release resources before canceling a CLAIMED "
                              "node ('release-resources')")
        if source == "LAUNCHING":
            if target == "CANCELED" and node["dispatch_issued"] and \
                    not termination_evidence:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "a create call was issued; resolve the nonce - an "
                    "identified execution moves to CANCELING, proof of no "
                    "delivery permits CANCELED with --termination-evidence, "
                    "ambiguity becomes UNKNOWN")
            if target == "CANCELING" and not node["dispatch_issued"]:
                raise LedgerError(EXIT_SEMANTIC,
                                  "no dispatch was issued; cancel directly")
            if target == "CANCELING" and not node.get("thread_id") and \
                    not thread_id:
                raise LedgerError(EXIT_SEMANTIC,
                                  "CANCELING requires the uniquely identified "
                                  "owner thread; otherwise use UNKNOWN")
            if target == "RUNNING":
                if node["one_shot"]:
                    raise LedgerError(
                        EXIT_SEMANTIC,
                        "one-shot executors enter PREPARING first; the "
                        "one-shot command is never in the initial prompt")
                if not node["dispatch_issued"]:
                    raise LedgerError(EXIT_SEMANTIC,
                                      "record-dispatch before RUNNING")
                if not thread_id:
                    raise LedgerError(EXIT_SEMANTIC,
                                      "RUNNING requires --thread-id (the "
                                      "adopted owner)")
            if target == "PREPARING":
                if not node["one_shot"]:
                    raise LedgerError(EXIT_SEMANTIC,
                                      "PREPARING is for one-shot executors only")
                if not node["dispatch_issued"] or not thread_id:
                    raise LedgerError(EXIT_SEMANTIC,
                                      "PREPARING requires a recorded dispatch "
                                      "and --thread-id")
        if source == "PREPARING" and target == "ARMED":
            if not arm_nonce or not NONCE_RE.match(arm_nonce):
                raise LedgerError(EXIT_SEMANTIC,
                                  "ARMED requires a valid --arm-nonce")
            if arm_nonce in ledger["nonces"]["arm"] or \
                    arm_nonce in ledger["nonces"]["launch"] or \
                    arm_nonce in ledger["nonces"]["authorization"]:
                raise LedgerError(EXIT_SEMANTIC,
                                  "arm nonce already issued; arm nonces are "
                                  "single-use and unique")
            if not readiness_evidence:
                raise LedgerError(EXIT_SEMANTIC,
                                  "ARMED requires --readiness-evidence (the "
                                  "executor's verified readiness receipt)")
            node["arm"]["nonce"] = arm_nonce
            ledger["nonces"]["arm"][arm_nonce] = {"node": ref, "spent": False}
        if source == "ARMED" and target == "RUNNING":
            if not node["arm"]["dispatched"]:
                raise LedgerError(EXIT_SEMANTIC,
                                  "record-arm-dispatch before RUNNING")
            if not arm_acknowledged:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "only acknowledged arm delivery enters RUNNING "
                    "(--arm-acknowledged); ambiguous delivery is UNKNOWN")
            entry = ledger["nonces"]["arm"][node["arm"]["nonce"]]
            if entry["spent"]:
                raise LedgerError(EXIT_SEMANTIC,
                                  "arm nonce already spent; a one-shot cannot "
                                  "be executed twice")
            entry["spent"] = True
            node["arm"]["acknowledged"] = True
            node["arm"]["spent"] = True
        receipt_required = target in {"SUCCEEDED", "FAILED", "ABORTED"} or \
            (source == "CANCELING" and target == "CANCELED")
        if receipt_required:
            if receipt_file is None:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    f"{target} requires --receipt proving the owner and its "
                    "processes are terminal (REPORTING.md)")
            receipt = safe_load_json(receipt_file, RECEIPT_MAX_BYTES)
            fully_released = validate_receipt(
                node, receipt, ledger["run_id"], target)
            node["receipt"] = receipt
            node["receipt_sha256"] = hashlib.sha256(
                json.dumps(receipt, sort_keys=True).encode()).hexdigest()
            if target == "SUCCEEDED" and receipt["artifact_hashes"]:
                if not verification_worktree:
                    raise LedgerError(
                        EXIT_SEMANTIC,
                        "artifact hashes require --verification-worktree so "
                        "the coordinator can recompute the current bytes")
                node["artifact_verification"] = verify_receipt_artifacts(
                    node, receipt, verification_worktree)
            elif target == "SUCCEEDED" and any(
                    resource["type"] == "path"
                    for resource in node["resources"]):
                raise LedgerError(
                    EXIT_SEMANTIC,
                    "mutating success requires independently verified "
                    "artifact hashes")
            if fully_released:
                node["holds_resources"] = False
            if target == "SUCCEEDED":
                node["artifact_disposition"] = "CURRENT"
            elif receipt["artifact"]:
                node["artifact_disposition"] = "REJECTED"
        if target == "SUCCEEDED":
            predecessor = node.get("supersedes")
            if predecessor:
                prior = get_node(ledger, predecessor)
                if prior["state"] != "SUCCEEDED" or \
                        prior.get("artifact_disposition") != "CURRENT" or \
                        prior["fingerprint"] != node["fingerprint"]:
                    raise LedgerError(
                        EXIT_SEMANTIC,
                        "superseded artifact is no longer the matching "
                        "CURRENT predecessor")
                prior["artifact_disposition"] = "SUPERSEDED"
        if target in {"FAILED", "ABORTED", "UNKNOWN"} and not evidence:
            raise LedgerError(EXIT_SEMANTIC,
                              f"{target} requires --evidence describing what "
                              "is known")
        if source == "CANCELING" and target == "CANCELED" and \
                not termination_evidence:
            raise LedgerError(EXIT_SEMANTIC,
                              "CANCELED from CANCELING requires "
                              "--termination-evidence proving the execution "
                              "stopped")
        if target == "UNKNOWN":
            node["reconciliation"] = {"status": "pending",
                                      "evidence": [evidence],
                                      "resolved_outcome": None,
                                      "entered_from": source}
        if thread_id:
            tid = _check_text(thread_id, "thread id")
            if node["thread_id"] not in (None, tid):
                raise LedgerError(EXIT_SEMANTIC,
                                  "a node has exactly one owner thread; it "
                                  "cannot be reassigned")
            for key, other in ledger["nodes"].items():
                if key != ref and other.get("thread_id") == tid and \
                        other["node_id"] != node["node_id"]:
                    raise LedgerError(
                        EXIT_SEMANTIC,
                        f"thread {tid!r} already owns {key}; thread reuse is "
                        "allowed only within the same node lineage as a "
                        "recorded attempt N+1")
            node["thread_id"] = tid
        node["state"] = target
        node["last_transition_at"] = _now()
        node["history"].append({"from": source, "to": target, "at": _now(),
                                "note": evidence or termination_evidence or
                                readiness_evidence or ""})
        return {"node": ref, "from": source, "to": target}

    return mutate(root, run_id, writer, expect_generation, "transition",
                  mutator)


def op_release_resources(root, run_id, writer, expect_generation, ref,
                         evidence):
    def mutator(ledger):
        node = get_node(ledger, ref)
        if not node["holds_resources"]:
            raise LedgerError(EXIT_SEMANTIC, "node holds no resources")
        state = node["state"]
        if state == "UNKNOWN":
            rec = node.get("reconciliation") or {}
            if rec.get("status") != "resolved":
                raise LedgerError(
                    EXIT_AMBIGUOUS,
                    "resources of an unreconciled UNKNOWN node stay frozen: "
                    "a missing heartbeat never proves the previous writer "
                    "stopped (CONCURRENCY.md)")
        elif state not in TERMINAL_STATES and state != "CLAIMED":
            raise LedgerError(EXIT_SEMANTIC,
                              "release is allowed for CLAIMED (pre-launch) or "
                              "terminal nodes with evidence, not live work")
        if not evidence:
            raise LedgerError(EXIT_SEMANTIC, "release requires --evidence")
        node["holds_resources"] = False
        target = "READY" if state == "CLAIMED" else state
        node["state"] = target
        node["last_transition_at"] = _now()
        node["history"].append({"from": state, "to": target, "at": _now(),
                                "note": f"resources released: {evidence}"})
        return {"node": ref, "released": True, "state": target}
    return mutate(root, run_id, writer, expect_generation,
                  "release-resources", mutator)


def op_reconcile(root, run_id, writer, expect_generation, ref, evidence,
                 outcome):
    if outcome not in RECONCILE_OUTCOMES:
        raise LedgerError(EXIT_USAGE,
                          f"outcome must be one of {sorted(RECONCILE_OUTCOMES)}")

    def mutator(ledger):
        node = get_node(ledger, ref)
        if node["state"] != "UNKNOWN":
            raise LedgerError(EXIT_SEMANTIC,
                              "reconcile applies only to UNKNOWN nodes")
        rec = node.get("reconciliation") or {"status": "pending",
                                             "evidence": [],
                                             "resolved_outcome": None,
                                             "entered_from": None}
        if rec.get("status") == "resolved":
            raise LedgerError(EXIT_SEMANTIC,
                              "resolved reconciliation is final; contradictory "
                              "or withdrawn proof requires a new run review")
        _check_text(evidence, "evidence")
        entered_from = rec.get("entered_from")
        if outcome == "no_delivery_proven" and entered_from != "LAUNCHING":
            raise LedgerError(
                EXIT_SEMANTIC,
                "no_delivery_proven is valid only for UNKNOWN entered from "
                "LAUNCHING; later states prove an execution identity existed")
        rec["evidence"].append(evidence)
        if outcome != "unresolved":
            rec["status"] = "resolved"
            rec["resolved_outcome"] = outcome
        node["reconciliation"] = rec
        node["history"].append({"from": "UNKNOWN", "to": "UNKNOWN",
                                "at": _now(),
                                "note": f"reconciliation: {outcome}"})
        return {"node": ref, "reconciliation": rec["status"],
                "outcome": outcome}
    return mutate(root, run_id, writer, expect_generation, "reconcile",
                  mutator)


def op_set_disposition(root, run_id, writer, expect_generation, ref,
                       disposition, evidence):
    if disposition not in DISPOSITIONS:
        raise LedgerError(EXIT_USAGE,
                          f"disposition must be one of {sorted(DISPOSITIONS)}")

    def mutator(ledger):
        node = get_node(ledger, ref)
        if node["state"] != "SUCCEEDED":
            raise LedgerError(EXIT_SEMANTIC,
                              "artifact disposition applies to SUCCEEDED nodes; "
                              "changing it never stops a live execution")
        if not evidence:
            raise LedgerError(EXIT_SEMANTIC, "disposition requires --evidence")
        if disposition == "CURRENT":
            conflicts = [key for key, other in ledger["nodes"].items()
                         if key != ref and other["state"] == "SUCCEEDED" and
                         other.get("artifact_disposition") == "CURRENT" and
                         other["fingerprint"] == node["fingerprint"]]
            if conflicts:
                raise LedgerError(
                    EXIT_SEMANTIC,
                    f"another CURRENT artifact already exists: {conflicts}")
        node["artifact_disposition"] = disposition
        node["history"].append({"from": "SUCCEEDED", "to": "SUCCEEDED",
                                "at": _now(),
                                "note": f"disposition {disposition}: {evidence}"})
        return {"node": ref, "disposition": disposition}
    return mutate(root, run_id, writer, expect_generation, "set-disposition",
                  mutator)


# ---------------------------------------------------------------------------
# Read-only validation
# ---------------------------------------------------------------------------
def validate_ledger_shape(ledger):
    """Return structural findings without assuming any nested field exists."""
    findings = []

    def bad(code, node, message):
        findings.append(Finding("VIOLATION", code, node, message))

    def walk(value, path="$", depth=0):
        if depth > MAX_JSON_DEPTH:
            bad("E_DEPTH", None, f"{path} exceeds depth {MAX_JSON_DEPTH}")
            return
        if isinstance(value, str):
            if len(value) > MAX_TEXT_FIELD:
                bad("E_TEXT", None,
                    f"{path} exceeds {MAX_TEXT_FIELD} characters")
            if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
                bad("E_CONTROL", None,
                    f"{path} contains control characters")
        elif isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    bad("E_SHAPE", None, f"{path} has a non-string key")
                    continue
                walk(key, f"{path}.<key>", depth + 1)
                walk(item, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", depth + 1)

    if not isinstance(ledger, dict):
        bad("E_SHAPE", None, "ledger must be an object")
        return findings
    walk(ledger)
    required_top = {
        "schema_version", "protocol_version", "tool_version", "run_id",
        "task", "host_capabilities", "created_at", "updated_at",
        "generation", "nonces", "nodes",
    }
    missing = sorted(required_top - set(ledger))
    if missing:
        bad("E_SHAPE", None, f"ledger missing top-level keys {missing}")
        return findings
    extras = sorted(set(ledger) - required_top)
    if extras:
        bad("E_SHAPE", None, f"ledger has unsupported top-level keys {extras}")
    if not isinstance(ledger["schema_version"], int) or \
            isinstance(ledger["schema_version"], bool):
        bad("E_SHAPE", None, "schema_version must be an integer")
    protocol = ledger["protocol_version"]
    if not isinstance(protocol, str) or not re.match(
            r"^[0-9]+\.[0-9]+\.[0-9]+$", protocol):
        bad("E_VERSION", None,
            "protocol_version must be MAJOR.MINOR.PATCH")
    elif tuple(map(int, protocol.split(".")[:2])) not in \
            SUPPORTED_PROTOCOL_SERIES:
        bad("E_VERSION", None,
            f"unsupported protocol_version {protocol}")
    if not isinstance(ledger["tool_version"], str):
        bad("E_SHAPE", None, "tool_version must be a string")
    if not isinstance(ledger["run_id"], str) or not ID_RE.match(
            ledger["run_id"]):
        bad("E_ID", None, "run_id fails identifier grammar")
    if not isinstance(ledger["generation"], int) or \
            isinstance(ledger["generation"], bool) or \
            ledger["generation"] < 1:
        bad("E_GENERATION", None,
            "generation must be a positive integer")
    for key in ("created_at", "updated_at"):
        if not isinstance(ledger[key], str):
            bad("E_SHAPE", None, f"{key} must be a string")
    task = ledger["task"]
    if not isinstance(task, dict) or not all(
            isinstance(task.get(key), str)
            for key in ("type", "description_digest")):
        bad("E_SHAPE", None,
            "task must contain string type and description_digest")
    elif set(task) != {"type", "description_digest"}:
        bad("E_SHAPE", None, "task has unsupported keys")
    caps = ledger["host_capabilities"]
    if not isinstance(caps, dict) or not all(
            isinstance(key, str) and isinstance(value, bool)
            for key, value in caps.items()):
        bad("E_SHAPE", None,
            "host_capabilities must map strings to booleans")
    nonces = ledger["nonces"]
    if not isinstance(nonces, dict) or set(nonces) != {
            "launch", "arm", "authorization"}:
        bad("E_SHAPE", None,
            "nonces must contain exactly launch, arm, and authorization objects")
        return findings
    launch = nonces["launch"]
    arm_registry = nonces["arm"]
    authorization_registry = nonces["authorization"]
    if not isinstance(launch, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in launch.items()):
        bad("E_SHAPE", None, "nonces.launch must map strings to strings")
    else:
        for nonce, target in launch.items():
            if not NONCE_RE.match(nonce):
                bad("E_NONCE", None,
                    f"launch nonce {nonce!r} fails nonce grammar")
            if not NODE_REF_RE.match(target):
                bad("E_NODE_KEY", None,
                    f"launch nonce {nonce!r} has invalid node reference")
    if not isinstance(arm_registry, dict):
        bad("E_SHAPE", None, "nonces.arm must be an object")
    else:
        for nonce, entry in arm_registry.items():
            if not isinstance(nonce, str) or not NONCE_RE.match(nonce) or \
                    not isinstance(entry, dict) or \
                    set(entry) != {"node", "spent"} or \
                    not isinstance(entry.get("node"), str) or \
                    not NODE_REF_RE.match(entry.get("node", "")) or \
                    not isinstance(entry.get("spent"), bool):
                bad("E_SHAPE", None,
                    f"arm nonce {nonce!r} needs a valid nonce, exact node/"
                    "spent keys, a node reference, and boolean spent")
    if not isinstance(authorization_registry, dict) or not all(
            isinstance(key, str) and NONCE_RE.match(key) and
            isinstance(value, str) and NODE_REF_RE.match(value)
            for key, value in authorization_registry.items()):
        bad("E_SHAPE", None,
            "nonces.authorization must map valid nonces to node references")
    nodes = ledger["nodes"]
    if not isinstance(nodes, dict):
        bad("E_SHAPE", None, "nodes must be an object")
        return findings
    required_node = {
        "node_id", "attempt", "class", "one_shot", "model", "effort",
        "state", "fingerprint", "fingerprint_inputs", "launch_nonce",
        "thread_id", "dispatch_issued", "arm", "resources",
        "holds_resources", "dependencies", "join", "receipt",
        "receipt_sha256", "artifact_verification", "artifact_disposition",
        "one_shot_authorization", "reconciliation",
        "supersedes", "retry_authorization", "dup_group", "created_at",
        "last_transition_at", "history",
    }
    for ref, node in nodes.items():
        if not isinstance(ref, str) or not isinstance(node, dict):
            bad("E_SHAPE", str(ref), "node entry must be an object")
            continue
        absent = sorted(required_node - set(node))
        if absent:
            bad("E_SHAPE", ref, f"node missing keys {absent}")
            continue
        extra_node = sorted(set(node) - required_node - {"model_warning"})
        if extra_node:
            bad("E_SHAPE", ref,
                f"node has unsupported keys {extra_node}")
        node_id, attempt = node["node_id"], node["attempt"]
        if not isinstance(node_id, str) or not ID_RE.match(node_id):
            bad("E_ID", ref, "node_id fails identifier grammar")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or \
                attempt < 1:
            bad("E_SHAPE", ref, "attempt must be a positive integer")
        elif isinstance(node_id, str) and ref != node_key(node_id, attempt):
            bad("E_NODE_KEY", ref,
                "node key must equal node_id#attempt")
        for key in ("class", "model", "effort", "state", "launch_nonce",
                    "created_at", "last_transition_at"):
            if not isinstance(node[key], str):
                bad("E_SHAPE", ref, f"{key} must be a string")
        if isinstance(node["launch_nonce"], str) and not NONCE_RE.match(
                node["launch_nonce"]):
            bad("E_NONCE", ref, "launch_nonce fails nonce grammar")
        for key in ("one_shot", "dispatch_issued", "holds_resources"):
            if not isinstance(node[key], bool):
                bad("E_SHAPE", ref, f"{key} must be boolean")
        if node["thread_id"] is not None and not isinstance(
                node["thread_id"], str):
            bad("E_SHAPE", ref, "thread_id must be string or null")
        if not isinstance(node["fingerprint"], str) or not HEX64_RE.match(
                node["fingerprint"]):
            bad("E_FINGERPRINT", ref,
                "fingerprint must be 64 lowercase hex characters")
        fpi = node["fingerprint_inputs"]
        if not isinstance(fpi, dict) or not all(
                isinstance(fpi.get(key), str)
                for key in ("outcome", "base_revision", "inputs_digest",
                            "gate")) or not isinstance(
                                fpi.get("write_scope"), list) or not all(
                                    isinstance(item, str)
                                    for item in fpi.get("write_scope", [])):
            bad("E_SHAPE", ref, "fingerprint_inputs has invalid shape")
        elif set(fpi) != {"outcome", "base_revision", "inputs_digest",
                          "write_scope", "gate"}:
            bad("E_SHAPE", ref,
                "fingerprint_inputs has unsupported keys")
        arm = node["arm"]
        if not isinstance(arm, dict) or set(arm) != {
                "nonce", "dispatched", "acknowledged", "spent"}:
            bad("E_SHAPE", ref, "arm has invalid shape")
        else:
            if arm["nonce"] is not None and not isinstance(arm["nonce"], str):
                bad("E_SHAPE", ref, "arm nonce must be string or null")
            for key in ("dispatched", "acknowledged", "spent"):
                if not isinstance(arm[key], bool):
                    bad("E_SHAPE", ref, f"arm.{key} must be boolean")
        resources = node["resources"]
        if not isinstance(resources, list):
            bad("E_SHAPE", ref, "resources must be a list")
        else:
            for resource in resources:
                if not isinstance(resource, dict) or not all(
                        isinstance(resource.get(key), str)
                        for key in ("type", "id", "mode")):
                    bad("E_SHAPE", ref, "resource has invalid shape")
                elif set(resource) != {"type", "id", "mode"}:
                    bad("E_SHAPE", ref, "resource has unsupported keys")
        deps = node["dependencies"]
        if not isinstance(deps, list) or not all(
                isinstance(dep, str) for dep in deps):
            bad("E_SHAPE", ref, "dependencies must be a list of strings")
        if not isinstance(node["join"], str):
            bad("E_SHAPE", ref, "join must be a string")
        if node["receipt"] is not None and not isinstance(node["receipt"], dict):
            bad("E_SHAPE", ref, "receipt must be object or null")
        receipt_hash = node["receipt_sha256"]
        if receipt_hash is not None and (not isinstance(receipt_hash, str) or
                                         not HEX64_RE.match(receipt_hash)):
            bad("E_SHAPE", ref,
                "receipt_sha256 must be 64 lowercase hex or null")
        verification = node["artifact_verification"]
        if verification is not None:
            valid_verification = (
                isinstance(verification, dict) and set(verification) == {
                    "status", "verified_at", "worktree", "receipt_sha256",
                    "artifact_hashes"} and
                verification.get("status") == "verified" and
                all(isinstance(verification.get(key), str)
                    for key in ("verified_at", "worktree", "receipt_sha256")) and
                HEX64_RE.match(verification.get("receipt_sha256", "")) and
                isinstance(verification.get("artifact_hashes"), dict) and
                all(isinstance(key, str) and isinstance(value, str) and
                    re.match(r"^sha256:[0-9a-f]{64}$", value)
                    for key, value in verification.get(
                        "artifact_hashes", {}).items()))
            if not valid_verification:
                bad("E_SHAPE", ref, "artifact_verification has invalid shape")
        authorization = node["one_shot_authorization"]
        if authorization is not None:
            auth_keys = REQUIRED_AUTHORIZATION_KEYS | {
                "source_sha256", "recorded_at"}
            if not isinstance(authorization, dict) or \
                    set(authorization) != auth_keys or \
                    not isinstance(authorization.get(
                        "authorization_version"), int) or \
                    isinstance(authorization.get(
                        "authorization_version"), bool) or \
                    authorization.get("authorization_version") != 1 or \
                    not isinstance(authorization.get("operator_id"), str) or \
                    not ID_RE.match(authorization.get("operator_id", "")) or \
                    not isinstance(authorization.get("authorization_nonce"), str) or \
                    not NONCE_RE.match(authorization.get(
                        "authorization_nonce", "")) or \
                    not isinstance(authorization.get("source_sha256"), str) or \
                    not HEX64_RE.match(authorization.get("source_sha256", "")) or \
                    not all(isinstance(authorization.get(key), str)
                            for key in ("run_id", "node_id",
                                        "task_fingerprint", "issued_at",
                                        "expires_at", "recorded_at")):
                bad("E_SHAPE", ref,
                    "one_shot_authorization has invalid shape")
        for key in ("artifact_disposition", "supersedes",
                    "retry_authorization", "dup_group"):
            if node[key] is not None and not isinstance(node[key], str):
                bad("E_SHAPE", ref, f"{key} must be string or null")
        reconciliation = node["reconciliation"]
        if reconciliation is not None:
            if not isinstance(reconciliation, dict):
                bad("E_SHAPE", ref, "reconciliation must be object or null")
            elif set(reconciliation) != {"status", "evidence",
                                         "resolved_outcome", "entered_from"} or \
                    reconciliation.get("status") not in {"pending", "resolved"} or \
                    not isinstance(reconciliation.get("evidence"), list) or \
                    not all(isinstance(item, str)
                            for item in reconciliation.get("evidence", [])) or \
                    (reconciliation.get("resolved_outcome") is not None and
                     reconciliation.get("resolved_outcome") not in
                     RECONCILE_OUTCOMES) or \
                    not isinstance(reconciliation.get("entered_from"), str):
                bad("E_SHAPE", ref, "reconciliation has invalid shape")
        history = node["history"]
        if not isinstance(history, list):
            bad("E_SHAPE", ref, "history must be a list")
        else:
            for step in history:
                if not isinstance(step, dict) or \
                        not {"from", "to", "at"}.issubset(step) or \
                        not isinstance(
                        step.get("to"), str) or not isinstance(
                            step.get("at"), str) or (step.get("from") is not None
                                                     and not isinstance(
                                                         step.get("from"), str)):
                    bad("E_SHAPE", ref, "history step has invalid shape")
                elif set(step) - {"from", "to", "at", "note"}:
                    bad("E_SHAPE", ref, "history step has unsupported keys")
                elif "note" in step and not isinstance(step["note"], str):
                    bad("E_SHAPE", ref, "history note must be a string")
    return findings


def validate_ledger(ledger: dict):
    findings = validate_ledger_shape(ledger)
    if findings:
        return findings

    def bad(code, node, msg):
        findings.append(Finding("VIOLATION", code, node, msg))

    def ambiguous(code, node, msg):
        findings.append(Finding("AMBIGUOUS", code, node, msg))

    def warn(code, node, msg):
        findings.append(Finding("WARNING", code, node, msg))

    for key in ("run_id", "generation", "nodes", "nonces",
                "host_capabilities", "protocol_version"):
        if key not in ledger:
            bad("E_SHAPE", None, f"ledger missing top-level key {key!r}")
            return findings
    if not isinstance(ledger["generation"], int) or ledger["generation"] < 1:
        bad("E_GENERATION", None, "generation must be a positive integer")
    if not isinstance(ledger["nodes"], dict):
        bad("E_SHAPE", None, "nodes must be an object")
        return findings
    launch_reg = ledger["nonces"].get("launch", {})
    arm_reg = ledger["nonces"].get("arm", {})
    authorization_reg = ledger["nonces"].get("authorization", {})
    caps = ledger.get("host_capabilities", {})

    fingerprint_active = {}
    current_artifacts = {}
    thread_owner = {}
    lineages = {}
    for key, node in sorted(ledger["nodes"].items()):
        state = node.get("state")
        if state not in STATES:
            bad("E_STATE", key, f"unknown state {state!r}")
            continue
        if node.get("effort") in PROHIBITED_EFFORTS:
            bad("E_EFFORT", key, "ultra effort is prohibited for Swarm nodes")
        if node.get("effort") not in EFFORTS:
            bad("E_EFFORT", key, f"effort {node.get('effort')!r} not allowed")
        if node.get("class") not in CLASSES:
            bad("E_CLASS", key, f"unknown class {node.get('class')!r}")
        lineages.setdefault(node["node_id"], []).append(node)
        if node["one_shot"] != (node["class"] == "ONE_SHOT"):
            bad("E_ONESHOT", key,
                "one_shot flag must exactly match class ONE_SHOT")
        if node["class"] == "PURE" and node["resources"]:
            bad("E_RESOURCE", key,
                "PURE nodes may not declare mutation resources")
        if node["class"] != "PURE" and not node["resources"]:
            bad("E_RESOURCE", key,
                "non-PURE nodes must declare an exclusive resource")
        if node.get("dup_group") and node["class"] != "PURE":
            bad("E_DUP_GROUP", key,
                "intentional duplicate groups are PURE-only")
        if node.get("model") not in KNOWN_MODELS:
            warn("W_MODEL", key,
                 f"model {node.get('model')!r} is outside the documented roster")
        # fingerprint integrity: recompute from recorded inputs
        fpi = node.get("fingerprint_inputs") or {}
        try:
            recomputed = compute_fingerprint(
                fpi.get("outcome", ""), fpi.get("base_revision", ""),
                fpi.get("inputs_digest", "none"),
                fpi.get("write_scope", []), fpi.get("gate", ""))
            if recomputed != node.get("fingerprint"):
                bad("E_FINGERPRINT", key,
                    "stored fingerprint does not match its recorded inputs "
                    "(tampering or hand-edit)")
        except LedgerError as exc:
            bad("E_FINGERPRINT", key, f"fingerprint inputs invalid: {exc.message}")
        scopes = [f"{resource['type']}:{resource['id']}"
                  for resource in node["resources"]]
        if fpi.get("write_scope") != scopes:
            bad("E_RESOURCE", key,
                "fingerprint write_scope must exactly match resources")
        if scopes != sorted(scopes) or len(scopes) != len(set(scopes)):
            bad("E_RESOURCE", key,
                "resources must be unique and in canonical sorted order")
        if any(resource["mode"] != "exclusive"
               for resource in node["resources"]):
            bad("E_RESOURCE", key,
                "every resource mode must be exclusive")
        active_lease_states = {"CLAIMED", "LAUNCHING", "PREPARING", "ARMED",
                               "RUNNING", "CANCELING"}
        if node["class"] != "PURE" and state in active_lease_states and \
                not node["holds_resources"]:
            bad("E_RESOURCE", key,
                f"non-PURE node in {state} must still hold its resources")
        if node["class"] == "PURE" and node["holds_resources"]:
            bad("E_RESOURCE", key, "PURE node may not hold mutation resources")
        # history consistency
        history = node.get("history") or []
        if not history or history[-1].get("to") != state:
            bad("E_HISTORY", key, "history does not end at the current state")
        if history and (history[0].get("from") is not None or
                        history[0].get("to") != "PLANNED"):
            bad("E_HISTORY", key,
                "history must begin with None -> PLANNED")
        for index, step in enumerate(history):
            frm, to = step.get("from"), step.get("to")
            if frm is None:
                continue
            if frm != to and (frm, to) not in LEGAL_TRANSITIONS:
                bad("E_TRANSITION", key,
                    f"history contains illegal transition {frm} -> {to}")
            if index and history[index - 1].get("to") != frm:
                bad("E_HISTORY", key,
                    f"history is disconnected at step {index}")
        reached_launch = any(step.get("to") in {
            "RUNNING", "PREPARING", "ARMED", "CANCELING"}
            for step in history)
        if reached_launch and not node["dispatch_issued"]:
            bad("E_DISPATCH", key,
                "launched execution history requires dispatch_issued=true")
        if node["dispatch_issued"] and state in {"PLANNED", "READY", "CLAIMED"}:
            bad("E_DISPATCH", key,
                f"dispatch_issued is incompatible with state {state}")
        if state in {"PREPARING", "ARMED", "RUNNING", "CANCELING"} and \
                not node.get("thread_id"):
            bad("E_OWNER", key,
                f"state {state} requires a recorded owner thread")
        # nonce registry backlink
        if launch_reg.get(node.get("launch_nonce")) != key:
            bad("E_NONCE", key, "launch nonce is not registered to this node")
        # dedup among non-terminal work
        if state in BLOCKING_STATES or state in {"PLANNED", "READY"}:
            prev = fingerprint_active.get(node["fingerprint"])
            previous = ledger["nodes"].get(prev) if prev else None
            allowed_duplicate = bool(
                previous and node.get("dup_group") and
                node.get("dup_group") == previous.get("dup_group") and
                node["class"] == previous["class"] == "PURE")
            if prev and not allowed_duplicate:
                bad("E_DUP_FINGERPRINT", key,
                    f"active fingerprint duplicated by {prev}")
            fingerprint_active[node["fingerprint"]] = key
        # one owner per thread outside a lineage
        tid = node.get("thread_id")
        if tid:
            prior = thread_owner.get(tid)
            if prior and ledger["nodes"][prior]["node_id"] != node["node_id"]:
                bad("E_TWO_OWNERS", key,
                    f"thread {tid!r} also owns {prior} (different lineage)")
            thread_owner[tid] = key
        # one-shot invariants
        arm = node.get("arm") or {}
        if node.get("one_shot"):
            authorization = node.get("one_shot_authorization") or {}
            if not authorization:
                bad("E_AUTHORIZATION", key,
                    "ONE_SHOT requires a task-bound operator authorization")
            else:
                if authorization.get("run_id") != ledger["run_id"] or \
                        authorization.get("node_id") != node["node_id"] or \
                        authorization.get("task_fingerprint") != node[
                            "fingerprint"]:
                    bad("E_AUTHORIZATION", key,
                        "one-shot authorization binding does not match this "
                        "run, node, and fingerprint")
                original = {field: authorization.get(field)
                            for field in REQUIRED_AUTHORIZATION_KEYS}
                expected_auth_hash = hashlib.sha256(json.dumps(
                    original, sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest()
                if authorization.get("source_sha256") != expected_auth_hash:
                    bad("E_AUTHORIZATION", key,
                        "one-shot authorization source hash does not match")
                try:
                    issued = _parse_utc_timestamp(
                        authorization.get("issued_at"), "issued_at")
                    expires = _parse_utc_timestamp(
                        authorization.get("expires_at"), "expires_at")
                    if expires <= issued or \
                            expires - issued > timedelta(minutes=15):
                        bad("E_AUTHORIZATION", key,
                            "recorded one-shot authorization has an invalid "
                            "lifetime")
                except LedgerError as exc:
                    bad("E_AUTHORIZATION", key, exc.message)
                if authorization_reg.get(
                        authorization.get("authorization_nonce")) != key:
                    bad("E_AUTHORIZATION", key,
                        "authorization nonce is not registered to this node")
            if bool(arm.get("acknowledged")) != bool(arm.get("spent")):
                bad("E_ONESHOT", key,
                    "one-shot acknowledged and spent flags must agree")
            if (arm.get("acknowledged") or arm.get("spent")) and not \
                    arm.get("dispatched"):
                bad("E_ONESHOT", key,
                    "one-shot cannot be acknowledged or spent before dispatch")
            if state == "ARMED" and not arm.get("nonce"):
                bad("E_ONESHOT", key,
                    "ARMED one-shot requires a registered arm nonce")
            if state == "RUNNING" or (state in TERMINAL_STATES and
                                      any(h.get("from") == "ARMED" and
                                          h.get("to") == "RUNNING"
                                          for h in history)):
                if not arm.get("spent") or not arm.get("acknowledged"):
                    bad("E_ONESHOT", key,
                        "one-shot reached RUNNING without a spent, "
                        "acknowledged arm nonce")
            if arm.get("nonce"):
                entry = arm_reg.get(arm["nonce"])
                if not entry or entry.get("node") != key:
                    bad("E_NONCE", key, "arm nonce is not registered to this node")
                elif bool(entry.get("spent")) != bool(arm.get("spent")):
                    bad("E_ONESHOT", key,
                        "arm nonce spent flag disagrees with the registry")
        elif node.get("one_shot_authorization") is not None:
            bad("E_AUTHORIZATION", key,
                "non one-shot node carries one-shot authorization")
        elif arm.get("nonce") or arm.get("dispatched") or \
                arm.get("acknowledged") or arm.get("spent"):
            bad("E_ONESHOT", key,
                "non one-shot node carries one-shot arm state")
        # terminal receipt gate
        canceled_after_execution = state == "CANCELED" and any(
            step.get("from") == "CANCELING" and step.get("to") == "CANCELED"
            for step in history)
        receipt_required = state in {"SUCCEEDED", "FAILED", "ABORTED"} or \
            canceled_after_execution
        if receipt_required:
            if not node.get("receipt"):
                bad("E_RECEIPT", key,
                    f"{state} without an embedded terminal receipt")
            else:
                try:
                    validate_receipt(node, node["receipt"],
                                     ledger["run_id"], state)
                except LedgerError as exc:
                    bad("E_RECEIPT", key, exc.message)
                expected_hash = hashlib.sha256(
                    json.dumps(node["receipt"], sort_keys=True).encode()
                ).hexdigest()
                if node.get("receipt_sha256") != expected_hash:
                    bad("E_RECEIPT", key,
                        "receipt_sha256 does not match the embedded receipt")
            if state == "SUCCEEDED" and \
                    node.get("artifact_disposition") not in DISPOSITIONS:
                bad("E_DISPOSITION", key,
                    "SUCCEEDED nodes need an artifact disposition")
            if state != "SUCCEEDED" and node.get("artifact_disposition") not in {
                    None, "REJECTED"}:
                bad("E_DISPOSITION", key,
                    "non-success terminal artifacts may only be REJECTED")
            if state == "SUCCEEDED" and \
                    node.get("artifact_disposition") == "CURRENT":
                previous_current = current_artifacts.get(node["fingerprint"])
                if previous_current:
                    bad("E_DISPOSITION", key,
                        f"CURRENT artifact duplicates {previous_current}")
                current_artifacts[node["fingerprint"]] = key
            verification = node.get("artifact_verification")
            hashes = (node.get("receipt") or {}).get("artifact_hashes", {})
            if state == "SUCCEEDED" and any(
                    resource["type"] == "path"
                    for resource in node["resources"]):
                if not verification:
                    bad("E_ARTIFACT_VERIFY", key,
                        "mutating success lacks independent artifact-byte "
                        "verification")
                elif verification.get("receipt_sha256") != node.get(
                        "receipt_sha256") or verification.get(
                            "artifact_hashes") != hashes:
                    bad("E_ARTIFACT_VERIFY", key,
                        "artifact verification does not bind the embedded "
                        "receipt and its hashes")
            elif verification is not None:
                bad("E_ARTIFACT_VERIFY", key,
                    "artifact verification is valid only for mutating success")
        elif node.get("receipt") is not None or \
                node.get("receipt_sha256") is not None or \
                node.get("artifact_disposition") is not None or \
                node.get("artifact_verification") is not None:
            bad("E_RECEIPT", key,
                "this state may not carry a terminal receipt, receipt hash, "
                "or artifact disposition")
        predecessor_ref = node.get("supersedes")
        if predecessor_ref:
            predecessor = ledger["nodes"].get(predecessor_ref)
            if not predecessor or predecessor["state"] != "SUCCEEDED" or \
                    predecessor["fingerprint"] != node["fingerprint"]:
                bad("E_DISPOSITION", key,
                    "supersedes must reference a SUCCEEDED node with the same "
                    "fingerprint")
            elif state == "SUCCEEDED" and \
                    predecessor.get("artifact_disposition") != "SUPERSEDED":
                bad("E_DISPOSITION", key,
                    "successful replacement must supersede its predecessor")
        if state == "UNKNOWN":
            rec = node.get("reconciliation") or {}
            if rec.get("status") != "resolved":
                ambiguous("A_UNKNOWN", key,
                          "UNKNOWN awaiting reconciliation evidence; it is "
                          "fail-closed and blocks conflicting work")
            elif rec.get("resolved_outcome") not in {
                    "no_delivery_proven", "execution_terminal_proven"}:
                bad("E_RECONCILIATION", key,
                    "resolved UNKNOWN needs a supported resolved_outcome")
            if rec.get("status") == "pending" and \
                    rec.get("resolved_outcome") is not None:
                bad("E_RECONCILIATION", key,
                    "pending reconciliation cannot have a resolved outcome")
            if rec.get("resolved_outcome") == "no_delivery_proven" and \
                    rec.get("entered_from") != "LAUNCHING":
                bad("E_RECONCILIATION", key,
                    "no_delivery_proven requires UNKNOWN entered from LAUNCHING")
        elif node.get("reconciliation") is not None:
            bad("E_RECONCILIATION", key,
                "only UNKNOWN nodes may carry reconciliation state")
        dependencies = node["dependencies"]
        if len(dependencies) != len(set(dependencies)):
            bad("E_DEPENDENCY", key,
                "dependencies must be unique")
        if node["node_id"] in dependencies:
            bad("E_DEPENDENCY", key, "node may not depend on itself")
        missing = [dep for dep in dependencies if not lineage(ledger, dep)]
        if missing:
            bad("E_DEPENDENCY", key,
                f"dependencies do not exist: {missing}")
        if node["join"] != "all" and node["join"] != "any" and not re.match(
                r"^quorum:[1-9]\d*$", node["join"]):
            bad("E_DEPENDENCY", key, "join has invalid syntax")
        elif node["join"].startswith("quorum:") and int(
                node["join"].split(":", 1)[1]) > len(set(dependencies)):
            bad("E_DEPENDENCY", key,
                "quorum exceeds unique dependency count")
        # capability gates
        if node.get("class") in GUARDED_RETRY_CLASSES and \
                caps.get("unique_launch_discovery") is not True:
            bad("E_CAPABILITY", key,
                f"class {node.get('class')} recorded without the "
                "unique_launch_discovery capability")
        if node.get("class") == "ONE_SHOT" and \
                caps.get("one_shot_fence") is not True:
            bad("E_CAPABILITY", key,
                "ONE_SHOT recorded without one_shot_fence capability")
        receipt = node.get("receipt") or {}
        spawned = ((receipt.get("processes") or {}).get("spawned") or [])
        if spawned and caps.get("background_sessions") is not True:
            bad("E_CAPABILITY", key,
                "receipt reports spawned processes but the host lacks the "
                "background_sessions capability (background work prohibited)")
    # resource conflicts among current holders
    holders = [(k, n) for k, n in ledger["nodes"].items()
               if n.get("holds_resources")]
    for i, (key_a, node_a) in enumerate(holders):
        for key_b, node_b in holders[i + 1:]:
            for ra in node_a["resources"]:
                for rb in node_b["resources"]:
                    if resource_conflict(ra, rb):
                        bad("E_RESOURCE", key_b,
                            f"holds {rb['type']}:{rb['id']} conflicting with "
                            f"{key_a}'s {ra['type']}:{ra['id']}")
    # orphan nonces
    for nonce, target in launch_reg.items():
        if target not in ledger["nodes"]:
            bad("E_NONCE", None, f"launch nonce {nonce!r} maps to missing node")
        elif ledger["nodes"][target].get("launch_nonce") != nonce:
            bad("E_NONCE", target,
                f"launch nonce {nonce!r} is an alias, not the node's issued "
                "launch nonce")
    for nonce, entry in arm_reg.items():
        if not isinstance(entry, dict) or entry.get("node") not in ledger["nodes"]:
            bad("E_NONCE", None, f"arm nonce {nonce!r} maps to missing node")
        elif ledger["nodes"][entry["node"]].get("arm", {}).get("nonce") != nonce:
            bad("E_NONCE", entry["node"],
                f"arm nonce {nonce!r} is an alias, not the node's issued arm "
                "nonce")
    for nonce, target in authorization_reg.items():
        if target not in ledger["nodes"]:
            bad("E_NONCE", None,
                f"authorization nonce {nonce!r} maps to missing node")
        elif (ledger["nodes"][target].get("one_shot_authorization") or {}).get(
                "authorization_nonce") != nonce:
            bad("E_NONCE", target,
                f"authorization nonce {nonce!r} is an alias, not the node's "
                "issued authorization nonce")
    registries = [set(launch_reg), set(arm_reg), set(authorization_reg)]
    if any(registries[i] & registries[j] for i in range(3)
           for j in range(i + 1, 3)):
        bad("E_NONCE", None,
            "launch, arm, and authorization nonce registries must be disjoint")
    for node_id, attempts in lineages.items():
        ordered = sorted(attempts, key=lambda item: item["attempt"])
        expected = list(range(1, len(ordered) + 1))
        actual = [item["attempt"] for item in ordered]
        if actual != expected:
            bad("E_ATTEMPT", node_id,
                f"attempt sequence must be contiguous from 1; got {actual}")
        if any(item["class"] == "ONE_SHOT" for item in ordered) and \
                len(ordered) > 1:
            bad("E_ONESHOT", node_id,
                "ONE_SHOT lineages may have exactly one attempt")
        fingerprints = {item["fingerprint"] for item in ordered}
        if len(fingerprints) > 1:
            bad("E_FINGERPRINT", node_id,
                "attempts in one lineage must preserve the fingerprint")
    dependency_graph = {
        node_id: set(dep for item in attempts for dep in item["dependencies"])
        for node_id, attempts in lineages.items()
    }
    visiting, visited = set(), set()

    def visit(node_id):
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(dep) for dep in dependency_graph.get(node_id, ())
               if dep in dependency_graph):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    for node_id in dependency_graph:
        if visit(node_id):
            bad("E_DEPENDENCY", node_id,
                "dependency graph contains a cycle")
            break
    return findings


def exit_code_for(findings) -> int:
    if any(f.code in {"E_CONTROL", "E_DEPTH", "E_TEXT"}
           for f in findings):
        return EXIT_CORRUPT
    if any(f.code == "E_VERSION" for f in findings):
        return EXIT_VERSION
    if any(f.code in {"E_SHAPE", "E_ID", "E_NODE_KEY"}
           for f in findings):
        return EXIT_SHAPE
    if any(f.severity == "VIOLATION" for f in findings):
        return EXIT_SEMANTIC
    if any(f.severity == "AMBIGUOUS" for f in findings):
        return EXIT_AMBIGUOUS
    return EXIT_OK


def op_validate(root, run_id, check_journal=False):
    ledger = load_ledger(root, run_id)
    findings = validate_ledger(ledger)
    if check_journal:
        journal = classify_journal(root, run_id, ledger)
        if journal["status"].startswith("recoverable-"):
            findings.append(Finding(
                "WARNING", "W_JOURNAL_RECOVERABLE", None,
                f"write-ahead journal is safely recoverable "
                f"({journal['reason']}); the next mutation will repair it"))
        elif journal["status"] == "mismatch":
            code = ("A_JOURNAL_CORRUPT" if journal["issue"] or
                    "malformed" in (journal["reason"] or "") else
                    "A_EXTERNAL_WRITER")
            findings.append(Finding(
                "AMBIGUOUS", code, None,
                f"audit journal is not safely anchored "
                f"({journal['reason']}); investigate "
                "read-only, then recover --accept-current"))
        elif journal["status"] == "absent":
            findings.append(Finding(
                "AMBIGUOUS", "A_EXTERNAL_WRITER", None,
                "audit journal missing; initialization was interrupted or "
                "the trail was deleted - re-anchor via recover "
                "--accept-current after investigating"))
    return ledger, findings


def op_recover(root, run_id, apply_changes=False, clear_lock=False,
               accept_current=False, evidence=None, writer="coordinator"):
    report = {"orphan_temp_files": [], "lock": None, "journal": "absent",
              "pending_unknown": [], "in_flight": [], "actions": []}
    rdir = ensure_runtime_directory(root, run_id)
    lpath = lock_dir(root, run_id)
    lock_exists = os.path.lexists(lpath)
    if lock_exists:
        info = os.lstat(lpath)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise LedgerError(EXIT_CORRUPT,
                              "ledger lock path must be a real directory")
        owner_file = os.path.join(lpath, "owner.json")
        try:
            report["lock"] = safe_load_json(owner_file, 10_000) \
                if os.path.exists(owner_file) else "owner file missing"
        except LedgerError:
            report["lock"] = "owner file unreadable"
    if clear_lock:
        if apply_changes or accept_current:
            raise LedgerError(
                EXIT_USAGE,
                "--clear-lock is a standalone recovery action; rerun after "
                "clearing for any other mutation")
        if not evidence:
            raise LedgerError(EXIT_USAGE,
                              "--clear-lock requires --evidence proving the "
                              "recorded holder is not live")
        if not lock_exists:
            raise LedgerError(EXIT_USAGE, "no lock exists to clear")
        release_lock(root, run_id, force=True)
        report["actions"].append(
            f"cleared stale lock with evidence: {evidence}")
        report["lock"] = None
        ledger = load_ledger(root, run_id)
        return ledger, report

    token = None
    if apply_changes or accept_current:
        token = acquire_lock(root, run_id, f"recovery:{writer}")
    try:
        temp_pattern = re.compile(r"^ledger\.json\.tmp\.[A-Za-z0-9._-]+$")
        for name in sorted(os.listdir(rdir)):
            if not temp_pattern.match(name):
                continue
            report["orphan_temp_files"].append(name)
            if apply_changes:
                candidate = os.path.join(rdir, name)
                info = os.lstat(candidate)
                if stat.S_ISDIR(info.st_mode):
                    raise LedgerError(EXIT_CORRUPT,
                                      f"orphan temp {name} is a directory")
                os.remove(candidate)
                report["actions"].append(
                    f"removed orphan ledger temp file {name}")
        ledger = load_ledger(root, run_id)
        findings = validate_ledger(ledger)
        violations = _blocking_semantic_findings(findings)
        if accept_current and violations:
            raise LedgerError(
                exit_code_for(findings),
                "refusing to re-anchor an invalid ledger:\n" +
                "\n".join(str(finding) for finding in violations))
        journal = classify_journal(root, run_id, ledger)
        journal_issue = journal["issue"]
        last_good = journal["last_good"]
        current = journal["current"]
        if journal["status"].startswith("recoverable-"):
            report["journal"] = (
                f"RECOVERABLE: {journal['status']}: {journal['reason']}")
            if apply_changes:
                repair_recoverable_journal(
                    root, run_id, ledger, journal, f"recovery:{writer}")
                report["actions"].append("repaired interrupted WAL record")
                report["journal"] = "anchored"
        elif journal["status"] == "anchored":
            report["journal"] = "anchored"
        elif journal["status"] == "absent":
            report["journal"] = "MISMATCH: journal absent"
        else:
            report["journal"] = f"MISMATCH: {journal['reason']}"
        if report["journal"].startswith("MISMATCH") and accept_current:
            if not evidence:
                raise LedgerError(EXIT_USAGE,
                                  "--accept-current requires --evidence")
            if journal_issue:
                truncate_journal(root, run_id, last_good)
            journal_append(root, run_id, {
                "at": _now(), "action": "accept-current", "writer": writer,
                "generation": ledger["generation"],
                "detail": {"evidence": evidence},
                "snapshot_sha256": current,
            })
            report["actions"].append("re-anchored journal to current content")
            report["journal"] = "anchored"
        report["pending_unknown"] = unresolved_unknowns(ledger)
        for key, node in sorted(ledger["nodes"].items()):
            if node["state"] == "LAUNCHING" and node["dispatch_issued"]:
                report["in_flight"].append(
                    f"{key}: dispatch issued, outcome unrecorded - resolve the "
                    "launch nonce via thread discovery; adopt exactly one match "
                    "(-> RUNNING), prove no delivery (-> CANCELED), or mark "
                    "UNKNOWN")
            if node["state"] == "ARMED" and node["arm"]["dispatched"] and \
                    not node["arm"]["acknowledged"]:
                report["in_flight"].append(
                    f"{key}: arm message sent, delivery unconfirmed - NEVER "
                    "resend; confirm acknowledgment or mark UNKNOWN")
        return ledger, report
    finally:
        if token is not None:
            release_lock(root, run_id, token=token)


def op_show(root, run_id):
    ledger = load_ledger(root, run_id)
    findings = validate_ledger(ledger)
    if _blocking_semantic_findings(findings):
        raise LedgerError(exit_code_for(findings),
                          "refusing to display an invalid ledger:\n" +
                          "\n".join(str(f) for f in findings))
    rows = []
    for key, node in sorted(ledger["nodes"].items()):
        rows.append("{:<24} {:<10} {:<18} {:<20} holds={} fp={}".format(
            key, node["state"], node["class"],
            f"{node['model']}/{node['effort']}",
            "y" if node.get("holds_resources") else "n",
            node["fingerprint"][:12]))
    header = (f"run {ledger['run_id']} gen {ledger['generation']} "
              f"protocol {ledger['protocol_version']} "
              f"schema {ledger['schema_version']}")
    profile = capability_profile(ledger.get("host_capabilities", {}))
    disabled = ",".join(profile["disabled"]) or "none"
    capability_row = (f"capability tier {profile['tier']} "
                      f"disabled={disabled}")
    return ledger, [header, capability_row] + rows


def op_doctor(root, run_id):
    """Return a read-only, conservative continuation and evidence report."""
    ledger = load_ledger(root, run_id)
    findings = validate_ledger(ledger)
    blocking = _blocking_semantic_findings(findings)
    journal = classify_journal(root, run_id, ledger)
    unknown = unresolved_unknowns(ledger)
    in_flight = []
    artifacts = []
    for ref, node in sorted(ledger["nodes"].items()):
        if node["state"] == "LAUNCHING" and node["dispatch_issued"]:
            in_flight.append({"node": ref, "kind": "dispatch-unresolved"})
        if node["state"] == "ARMED" and node["arm"]["dispatched"] and \
                not node["arm"]["acknowledged"]:
            in_flight.append({"node": ref, "kind": "arm-unresolved"})
        if node["state"] == "SUCCEEDED":
            receipt = node.get("receipt") or {}
            verification = node.get("artifact_verification")
            artifacts.append({
                "node": ref,
                "artifact": receipt.get("artifact"),
                "disposition": node.get("artifact_disposition"),
                "verification": (
                    "verified-local-bytes" if verification else
                    ("unverified-pure-result" if node["class"] == "PURE"
                     else "unverified-external-effect")),
                "receipt_sha256": node.get("receipt_sha256"),
                "artifact_hashes": receipt.get("artifact_hashes", {}),
            })
    journal_safe = journal["status"] == "anchored" or \
        journal["status"].startswith("recoverable-")
    resumable = not blocking and not unknown and not in_flight and journal_safe
    current_hash = _file_sha256(ledger_path(root, run_id))
    resume_token = None
    if resumable:
        resume_token = hashlib.sha256(json.dumps({
            "run_id": run_id, "generation": ledger["generation"],
            "ledger_sha256": current_hash,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    profile = capability_profile(ledger.get("host_capabilities", {}))
    return {
        "run_id": run_id,
        "generation": ledger["generation"],
        "protocol_version": ledger["protocol_version"],
        "schema_version": ledger["schema_version"],
        "safety_badge": (
            "RECORDED-CONSISTENCY ONLY — host capabilities and external "
            "effects remain unverified"),
        "capabilities": profile,
        "capability_evidence": (
            "self-attested host declarations; not independently verified"),
        "journal": {
            "status": journal["status"], "reason": journal["reason"],
            "auto_recoverable": journal["status"].startswith("recoverable-"),
        },
        "unresolved_unknown": unknown,
        "in_flight_ambiguity": in_flight,
        "artifacts": artifacts,
        "findings": [finding.as_dict() for finding in findings],
        "resume": {"resumable": resumable, "token": resume_token},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_findings(findings, as_json):
    if as_json:
        print(json.dumps({"findings": [f.as_dict() for f in findings],
                          "ok": exit_code_for(findings) == EXIT_OK},
                         indent=2, sort_keys=True))
    else:
        for finding in findings:
            print(str(finding))
        if not findings:
            print("OK: no findings")


def build_parser():
    globals_parser = argparse.ArgumentParser(add_help=False)
    globals_parser.add_argument(
        "--root", default=argparse.SUPPRESS,
        help="repository root containing .swarm/ (default: .)")
    globals_parser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="machine-readable output")
    parser = argparse.ArgumentParser(
        prog="swarm_ledger", parents=[globals_parser],
        description="Deterministic enforcement core for GPT-5.6 Swarm "
                    "(read-only by default; mutations are explicit).")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_parser = sub.add_parser

    def add_parser(*a, **kw):
        kw.setdefault("parents", [globals_parser])
        return _add_parser(*a, **kw)

    sub.add_parser = add_parser

    def common(p, mutating=True):
        p.add_argument("--run-id", required=True)
        if mutating:
            p.add_argument("--expect-generation", type=int, required=True,
                           help="compare-and-set token from your last read")
            p.add_argument("--writer", default="coordinator")

    p = sub.add_parser("init", help="create a run ledger")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-type", required=True)
    p.add_argument("--task-digest", required=True,
                   help="digest or short reference of the task; never the "
                        "full prompt")
    p.add_argument("--capability", action="append", default=[],
                   metavar="KEY=true|false")
    p.add_argument("--writer", default="coordinator")

    p = sub.add_parser("fingerprint", help="compute a task fingerprint")
    for flag in ("--outcome", "--base-revision", "--gate"):
        p.add_argument(flag, required=True)
    p.add_argument("--inputs-digest", default="none",
                   metavar="none|64-hex",
                   help="raw 64-character lowercase SHA-256 hex digest, or "
                        "'none' (do not include a 'sha256:' prefix)")
    p.add_argument("--write-scope", action="append", default=[],
                   metavar="type:id")

    sub.add_parser(
        "verify-reference-set",
        help="verify that all packaged normative documents match this tool")

    p = sub.add_parser(
        "capture-baseline",
        help="capture a read-only Git HEAD and dirty-state digest")
    p.add_argument("--worktree", default=".")
    p.add_argument("--include-ignored", action="store_true",
                   help="also hash bounded ignored-file identities and bytes")

    p = sub.add_parser(
        "verify-baseline",
        help="fail closed if Git HEAD or dirty-state digest drifted")
    p.add_argument("--worktree", default=".")
    p.add_argument("--expected-revision", required=True)
    p.add_argument("--expected-dirty-digest", required=True)
    p.add_argument("--expected-ignored-digest",
                   help="also verify the ignored-file content digest")

    p = sub.add_parser("create-node", help="add a PLANNED node")
    common(p)
    p.add_argument("--node-id", required=True)
    p.add_argument("--class", dest="klass", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)
    p.add_argument("--outcome", required=True)
    p.add_argument("--base-revision", required=True)
    p.add_argument("--inputs-digest", default="none",
                   metavar="none|64-hex",
                   help="raw 64-character lowercase SHA-256 hex digest, or "
                        "'none' (do not include a 'sha256:' prefix)")
    p.add_argument("--gate", required=True)
    p.add_argument("--launch-nonce", required=True)
    p.add_argument("--resource", action="append", default=[],
                   metavar="type:id (canonical sorted order)")
    p.add_argument("--dependency", action="append", default=[])
    p.add_argument("--join", default="all")
    p.add_argument("--supersedes")
    p.add_argument("--authorize-retry",
                   help="explicit authority evidence for retrying a guarded "
                        "class; never automatic")
    p.add_argument("--intentional-duplicate", dest="dup_group",
                   help="any/quorum research duplication group (PURE only)")
    p.add_argument("--fingerprint", dest="supplied_fingerprint",
                   help="optional cross-check of a precomputed fingerprint")
    p.add_argument("--one-shot-authorization",
                   dest="one_shot_authorization_file",
                   help="operator-supplied, task-bound authorization JSON")

    p = sub.add_parser("record-dispatch",
                       help="record that the create call was issued")
    common(p)
    p.add_argument("node")

    p = sub.add_parser("record-arm-dispatch",
                       help="record that the single arm message was sent")
    common(p)
    p.add_argument("node")

    p = sub.add_parser("transition", help="apply one allowed state change")
    common(p)
    p.add_argument("node")
    p.add_argument("target")
    p.add_argument("--evidence")
    p.add_argument("--thread-id")
    p.add_argument("--receipt", dest="receipt_file")
    p.add_argument("--arm-nonce")
    p.add_argument("--readiness-evidence")
    p.add_argument("--arm-acknowledged", action="store_true")
    p.add_argument("--termination-evidence")
    p.add_argument("--verification-worktree",
                   help="worktree used to recompute receipt artifact hashes")

    p = sub.add_parser(
        "verify-artifacts",
        help="read-only recomputation of receipt artifact hashes")
    common(p, mutating=False)
    p.add_argument("node")
    p.add_argument("--receipt", dest="receipt_file", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--expect-status", default="SUCCEEDED",
                   choices=sorted(TERMINAL_STATES - {"UNKNOWN"}))

    p = sub.add_parser("release-resources", help="release held scopes")
    common(p)
    p.add_argument("node")
    p.add_argument("--evidence", required=True)

    p = sub.add_parser("reconcile", help="record UNKNOWN reconciliation")
    common(p)
    p.add_argument("node")
    p.add_argument("--evidence", required=True)
    p.add_argument("--outcome", required=True,
                   choices=sorted(RECONCILE_OUTCOMES))

    p = sub.add_parser("set-disposition", help="change artifact disposition")
    common(p)
    p.add_argument("node")
    p.add_argument("--disposition", required=True)
    p.add_argument("--evidence", required=True)

    p = sub.add_parser("validate", help="read-only semantic validation")
    common(p, mutating=False)
    p.add_argument("--journal", action="store_true",
                   help="also verify the journal anchor (external-writer check)")

    p = sub.add_parser("recover", help="report crash artifacts (read-only "
                                       "unless flags are passed)")
    common(p, mutating=False)
    p.add_argument("--apply", action="store_true",
                   help="remove orphan temp files only")
    p.add_argument("--clear-lock", action="store_true")
    p.add_argument("--accept-current", action="store_true")
    p.add_argument("--evidence")
    p.add_argument("--writer", default="coordinator")

    p = sub.add_parser("show", help="print a compact run table")
    common(p, mutating=False)
    p = sub.add_parser("doctor", help="read-only safety and resume report")
    common(p, mutating=False)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    args.root = getattr(args, "root", ".")
    args.json = getattr(args, "json", False)
    try:
        if args.command == "init":
            ledger = op_init(args.root, args.run_id, args.task_type,
                             args.task_digest, args.capability, args.writer)
            profile = capability_profile(ledger["host_capabilities"])
            disabled = ",".join(profile["disabled"]) or "none"
            print(f"initialized run {args.run_id} at generation "
                  f"{ledger['generation']}; capability tier "
                  f"{profile['tier']}; disabled={disabled}")
        elif args.command == "fingerprint":
            print(compute_fingerprint(args.outcome, args.base_revision,
                                      args.inputs_digest, args.write_scope,
                                      args.gate))
        elif args.command == "verify-reference-set":
            print(json.dumps(verify_reference_set(), indent=2, sort_keys=True))
        elif args.command == "capture-baseline":
            print(json.dumps(capture_git_baseline(
                args.worktree, include_ignored=args.include_ignored), indent=2,
                             sort_keys=True))
        elif args.command == "verify-baseline":
            print(json.dumps(verify_git_baseline(
                args.worktree, args.expected_revision,
                args.expected_dirty_digest,
                args.expected_ignored_digest), indent=2, sort_keys=True))
        elif args.command == "create-node":
            ledger = op_create_node(
                args.root, args.run_id, args.writer, args.expect_generation,
                node_id=args.node_id, klass=args.klass, model=args.model,
                effort=args.effort, outcome=args.outcome,
                base_revision=args.base_revision,
                inputs_digest=args.inputs_digest, gate=args.gate,
                launch_nonce=args.launch_nonce, resources=args.resource,
                dependencies=args.dependency, join=args.join,
                supersedes=args.supersedes,
                authorize_retry=args.authorize_retry,
                dup_group=args.dup_group,
                supplied_fingerprint=args.supplied_fingerprint,
                one_shot_authorization_file=
                args.one_shot_authorization_file)
            print(f"created node; ledger at generation {ledger['generation']}")
        elif args.command == "record-dispatch":
            ledger = op_record_dispatch(args.root, args.run_id, args.writer,
                                        args.expect_generation, args.node)
            print(f"dispatch recorded; generation {ledger['generation']}")
        elif args.command == "record-arm-dispatch":
            ledger = op_record_arm_dispatch(args.root, args.run_id,
                                            args.writer,
                                            args.expect_generation, args.node)
            print(f"arm dispatch recorded; generation {ledger['generation']}")
        elif args.command == "transition":
            ledger = op_transition(
                args.root, args.run_id, args.writer, args.expect_generation,
                args.node, args.target, evidence=args.evidence,
                thread_id=args.thread_id, receipt_file=args.receipt_file,
                arm_nonce=args.arm_nonce,
                readiness_evidence=args.readiness_evidence,
                arm_acknowledged=args.arm_acknowledged,
                termination_evidence=args.termination_evidence,
                verification_worktree=args.verification_worktree)
            print(f"transitioned; generation {ledger['generation']}")
        elif args.command == "verify-artifacts":
            report = op_verify_artifacts(
                args.root, args.run_id, args.node, args.receipt_file,
                args.worktree, args.expect_status)
            print(json.dumps(report, indent=2, sort_keys=True))
        elif args.command == "release-resources":
            ledger = op_release_resources(args.root, args.run_id, args.writer,
                                          args.expect_generation, args.node,
                                          args.evidence)
            print(f"released; generation {ledger['generation']}")
        elif args.command == "reconcile":
            ledger = op_reconcile(args.root, args.run_id, args.writer,
                                  args.expect_generation, args.node,
                                  args.evidence, args.outcome)
            print(f"reconciliation recorded; generation {ledger['generation']}")
        elif args.command == "set-disposition":
            ledger = op_set_disposition(args.root, args.run_id, args.writer,
                                        args.expect_generation, args.node,
                                        args.disposition, args.evidence)
            print(f"disposition set; generation {ledger['generation']}")
        elif args.command == "validate":
            ledger, findings = op_validate(args.root, args.run_id,
                                           check_journal=args.journal)
            _print_findings(findings, args.json)
            return exit_code_for(findings)
        elif args.command == "recover":
            ledger, report = op_recover(args.root, args.run_id,
                                        apply_changes=args.apply,
                                        clear_lock=args.clear_lock,
                                        accept_current=args.accept_current,
                                        evidence=args.evidence,
                                        writer=args.writer)
            print(json.dumps(report, indent=2, sort_keys=True))
            if report["journal"].startswith("MISMATCH") or \
                    report["in_flight"] or report["pending_unknown"]:
                return EXIT_AMBIGUOUS
        elif args.command == "show":
            ledger, rows = op_show(args.root, args.run_id)
            print("\n".join(rows))
        elif args.command == "doctor":
            print(json.dumps(op_doctor(args.root, args.run_id),
                             indent=2, sort_keys=True))
        return EXIT_OK
    except LedgerError as exc:
        print(f"ERROR({exc.exit_code}): {exc.message}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
