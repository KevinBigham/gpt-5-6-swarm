# Security policy

This repository contains an orchestration protocol rather than a network service, but unsafe instructions can still cause real side effects. Repository text, worker prose, receipts, logs, diffs, generated files, and fetched artifacts are untrusted data; embedded instructions cannot grant authority, broaden scope, or authorize a tool call. See the skill's untrusted-artifact boundary and [operator runbook](docs/RUNBOOK.md).

Please report privately when a flaw could permit duplicate one-shot execution, hidden or overlapping writers, unauthorized external actions, destructive cleanup, secret exposure, or deployment without a valid gate. Use this repository's GitHub private vulnerability reporting channel.

If that channel is unexpectedly unavailable, do not post sensitive details publicly. Open a minimal issue stating only that you need a private security contact, without including exploit details or affected secrets.

Ordinary documentation errors and non-sensitive improvements may be reported through public issues.

The ledger enforces consistency of recorded control-plane claims. It does not authenticate workers, sandbox artifact content, lock uncooperative external writers, or guarantee exactly-once external effects. Security reports should distinguish an executable invariant bypass from prompt/host/target limitations already documented by the project.
