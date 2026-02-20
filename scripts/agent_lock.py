#!/usr/bin/env python3
"""Queue + lock helper for FlowSmith dispatch workflows.

This script manages lock labels on target repository issues.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


OPERATION_LOG_MARKER = "flowsmith-operation-log"
DEFAULT_LOCK_LABEL = "agent/running"
DEFAULT_SERVICE_LABEL_PREFIX = "agent/service:"
DEFAULT_OPERATION_LABEL_PREFIX = "agent/op:"
DEFAULT_MAX_PARALLEL = 2
DEFAULT_POLL_SECONDS = 20
DEFAULT_TIMEOUT_MINUTES = 180
DEFAULT_STALE_MINUTES = 360
DEFAULT_COOLDOWN_MINUTES = 30
DEFAULT_LIST_LIMIT = 100


def log(message: str) -> None:
    print(f"[agent-lock] {message}", flush=True)


def run_process(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        joined = " ".join(args)
        raise RuntimeError(
            f"Command failed: {joined}\n"
            f"exit={proc.returncode}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def run_gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_process(["gh", *args], check=check)


def parse_time(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def to_iso8601(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json_stdout(proc: subprocess.CompletedProcess[str], *, where: str) -> Any:
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON from {where}") from err


def normalize_repo_and_issue(repo_value: str, issue_value: int) -> tuple[str, int]:
    repo = str(repo_value or "").strip()
    issue_number = int(issue_value)
    if not repo:
        raise RuntimeError("repo is required.")
    if issue_number <= 0:
        raise RuntimeError("issue-number must be positive.")
    return repo, issue_number


def parse_label_names(labels_raw: Any) -> set[str]:
    if not isinstance(labels_raw, list):
        return set()
    labels: set[str] = set()
    for item in labels_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            labels.add(name)
    return labels


def ensure_label_exists(repo: str, label: str) -> None:
    if not label:
        raise RuntimeError("Label name is empty.")
    proc = run_gh(
        [
            "label",
            "create",
            label,
            "--repo",
            repo,
            "--color",
            "A8E6CF",
            "--description",
            "FlowSmith lock label",
        ],
        check=False,
    )
    if proc.returncode == 0:
        return
    detail = (proc.stderr or proc.stdout or "").lower()
    if "already exists" in detail:
        return
    raise RuntimeError(f"Unable to ensure label '{label}' on {repo}: {proc.stderr or proc.stdout}")


@dataclass
class IssueInfo:
    number: int
    updated_at: dt.datetime | None
    labels: set[str]


def parse_issue_info(payload: Any) -> IssueInfo | None:
    if not isinstance(payload, dict):
        return None
    number = int(payload.get("number") or 0)
    if number <= 0:
        return None
    updated_at = parse_time(str(payload.get("updatedAt") or ""))
    labels = parse_label_names(payload.get("labels"))
    return IssueInfo(number=number, updated_at=updated_at, labels=labels)


def get_issue(repo: str, issue_number: int) -> IssueInfo:
    proc = run_gh(
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo,
            "--json",
            "number,updatedAt,labels",
        ]
    )
    payload = load_json_stdout(proc, where="gh issue view")
    issue = parse_issue_info(payload)
    if issue is None:
        raise RuntimeError("Issue payload is invalid.")
    return issue


def list_open_issues_with_label(
    repo: str,
    label: str,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[IssueInfo]:
    proc = run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--label",
            label,
            "--limit",
            str(limit),
            "--json",
            "number,updatedAt,labels",
        ]
    )
    payload = load_json_stdout(proc, where="gh issue list")
    if not isinstance(payload, list):
        return []
    issues: list[IssueInfo] = []
    for item in payload:
        parsed = parse_issue_info(item)
        if parsed is not None:
            issues.append(parsed)
    return issues


def add_issue_label(repo: str, issue_number: int, label: str) -> None:
    run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo,
            "--add-label",
            label,
        ]
    )


def remove_issue_label(repo: str, issue_number: int, label: str) -> None:
    proc = run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo,
            "--remove-label",
            label,
        ],
        check=False,
    )
    if proc.returncode == 0:
        return
    detail = (proc.stderr or proc.stdout or "").lower()
    if "not found" in detail or "no such label" in detail or "does not have label" in detail:
        return
    raise RuntimeError(f"Unable to remove label '{label}' from issue #{issue_number}: {proc.stderr or proc.stdout}")


def cleanup_stale_locks(
    *,
    repo: str,
    lock_label: str,
    stale_minutes: int,
) -> int:
    if stale_minutes <= 0:
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    stale_delta = dt.timedelta(minutes=stale_minutes)
    removed = 0
    for issue in list_open_issues_with_label(repo, lock_label):
        if issue.updated_at is None:
            continue
        if now - issue.updated_at < stale_delta:
            continue
        log(
            "Removing stale lock label "
            f"{lock_label} from issue #{issue.number} "
            f"(updated_at={to_iso8601(issue.updated_at)})"
        )
        remove_issue_label(repo, issue.number, lock_label)
        removed += 1
    return removed


def detect_service_and_operation_labels(
    *,
    labels: set[str],
    service_prefix: str,
    operation_prefix: str,
) -> tuple[str, str]:
    service_label = ""
    operation_label = ""
    for label in sorted(labels):
        if not service_label and label.startswith(service_prefix):
            service_label = label
        if not operation_label and label.startswith(operation_prefix):
            operation_label = label
    return service_label, operation_label


def list_issue_numbers_for_labels(
    *,
    repo: str,
    labels: list[str],
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[int]:
    cmd = [
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        str(limit),
        "--json",
        "number",
    ]
    for label in labels:
        cmd.extend(["--label", label])
    proc = run_gh(cmd)
    payload = load_json_stdout(proc, where="gh issue list for cooldown")
    if not isinstance(payload, list):
        return []
    numbers: list[int] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        number = int(item.get("number") or 0)
        if number > 0:
            numbers.append(number)
    return numbers


OPERATION_LOG_PATTERN = re.compile(
    r"<!--\s*"
    + re.escape(OPERATION_LOG_MARKER)
    + r"\s+service=(?P<service>\S+)\s+operation=(?P<operation>\S+)\s+executed_at=(?P<executed_at>\S+)\s*-->",
    flags=re.IGNORECASE,
)


def find_latest_operation_timestamp(
    *,
    repo: str,
    service_label: str,
    operation_label: str,
    issue_limit: int = 80,
) -> dt.datetime | None:
    if not service_label or not operation_label:
        return None
    latest: dt.datetime | None = None
    issue_numbers = list_issue_numbers_for_labels(
        repo=repo,
        labels=[service_label, operation_label],
        limit=issue_limit,
    )
    for issue_number in issue_numbers:
        proc = run_gh(
            [
                "api",
                f"repos/{repo}/issues/{issue_number}/comments?per_page=100",
            ],
            check=False,
        )
        if proc.returncode != 0:
            continue
        payload = load_json_stdout(proc, where="gh api issue comments")
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            body = str(item.get("body") or "")
            match = OPERATION_LOG_PATTERN.search(body)
            if not match:
                continue
            if match.group("service") != service_label:
                continue
            if match.group("operation") != operation_label:
                continue
            executed_at = parse_time(match.group("executed_at"))
            if executed_at is None:
                continue
            if latest is None or executed_at > latest:
                latest = executed_at
    return latest


def calculate_cooldown_wait_seconds(
    *,
    cooldown_minutes: int,
    last_executed_at: dt.datetime | None,
) -> int:
    if cooldown_minutes <= 0 or last_executed_at is None:
        return 0
    cooldown_deadline = last_executed_at + dt.timedelta(minutes=cooldown_minutes)
    now = dt.datetime.now(dt.timezone.utc)
    if now >= cooldown_deadline:
        return 0
    return int((cooldown_deadline - now).total_seconds())


def build_wait_reason_text(
    *,
    issue_locked: bool,
    lock_label: str,
    running_count: int,
    max_parallel: int,
    cooldown_wait_seconds: int,
) -> str:
    reasons: list[str] = []
    if issue_locked:
        reasons.append(f"issue is locked ({lock_label})")
    if running_count >= max_parallel:
        reasons.append(f"repo parallel limit reached ({running_count}/{max_parallel})")
    if cooldown_wait_seconds > 0:
        reasons.append(f"operation cooldown active ({cooldown_wait_seconds}s)")
    return ", ".join(reasons) or "unknown"


def write_outputs(outputs: dict[str, str], github_output: str) -> None:
    if not github_output:
        return
    path = os.path.abspath(github_output)
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            safe = str(value).replace("\n", " ").strip()
            handle.write(f"{key}={safe}\n")


def acquire_lock(args: argparse.Namespace) -> int:
    repo, issue_number = normalize_repo_and_issue(args.repo, args.issue_number)

    lock_label = str(args.lock_label).strip() or DEFAULT_LOCK_LABEL
    max_parallel = max(int(args.max_parallel), 1)
    poll_seconds = max(int(args.poll_seconds), 5)
    timeout_minutes = max(int(args.timeout_minutes), 1)
    stale_minutes = max(int(args.stale_minutes), 0)
    cooldown_minutes = max(int(args.cooldown_minutes), 0)
    service_prefix = str(args.service_label_prefix).strip() or DEFAULT_SERVICE_LABEL_PREFIX
    operation_prefix = str(args.operation_label_prefix).strip() or DEFAULT_OPERATION_LABEL_PREFIX

    ensure_label_exists(repo, lock_label)

    start_at = dt.datetime.now(dt.timezone.utc)
    deadline = start_at + dt.timedelta(minutes=timeout_minutes)
    while True:
        cleanup_stale_locks(
            repo=repo,
            lock_label=lock_label,
            stale_minutes=stale_minutes,
        )
        issue = get_issue(repo, issue_number)
        running_issues = list_open_issues_with_label(repo, lock_label)
        running_count = len(running_issues)
        issue_locked = lock_label in issue.labels

        service_label, operation_label = detect_service_and_operation_labels(
            labels=issue.labels,
            service_prefix=service_prefix,
            operation_prefix=operation_prefix,
        )

        cooldown_wait_seconds = 0
        if cooldown_minutes > 0 and service_label and operation_label:
            last_executed_at = find_latest_operation_timestamp(
                repo=repo,
                service_label=service_label,
                operation_label=operation_label,
            )
            cooldown_wait_seconds = calculate_cooldown_wait_seconds(
                cooldown_minutes=cooldown_minutes,
                last_executed_at=last_executed_at,
            )

        if not issue_locked and running_count < max_parallel and cooldown_wait_seconds <= 0:
            log(
                "Acquiring lock "
                f"repo={repo} issue=#{issue_number} label={lock_label} "
                f"running={running_count}/{max_parallel}"
            )
            add_issue_label(repo, issue_number, lock_label)
            verify = get_issue(repo, issue_number)
            if lock_label not in verify.labels:
                raise RuntimeError("Lock label was not added.")

            outputs = {
                "lock_acquired": "true",
                "target_repo": repo,
                "issue_number": str(issue_number),
                "lock_label": lock_label,
                "service_label": service_label,
                "operation_label": operation_label,
                "running_count_at_acquire": str(running_count),
            }
            write_outputs(outputs, str(args.github_output))
            log("Lock acquired.")
            return 0

        now = dt.datetime.now(dt.timezone.utc)
        if now >= deadline:
            raise RuntimeError(
                "Timed out while waiting for lock. "
                f"repo={repo} issue=#{issue_number} lock_label={lock_label}"
            )

        reason_text = build_wait_reason_text(
            issue_locked=issue_locked,
            lock_label=lock_label,
            running_count=running_count,
            max_parallel=max_parallel,
            cooldown_wait_seconds=cooldown_wait_seconds,
        )
        log(f"Waiting for lock: {reason_text}")
        time.sleep(poll_seconds)


def release_lock(args: argparse.Namespace) -> int:
    repo, issue_number = normalize_repo_and_issue(args.repo, args.issue_number)

    lock_label = str(args.lock_label).strip() or DEFAULT_LOCK_LABEL
    remove_issue_label(repo, issue_number, lock_label)
    log(f"Released lock label {lock_label} on {repo}#{issue_number}")

    service_label = str(args.service_label or "").strip()
    operation_label = str(args.operation_label or "").strip()
    if args.record_operation and service_label and operation_label:
        executed_at = to_iso8601(dt.datetime.now(dt.timezone.utc))
        marker = (
            f"<!-- {OPERATION_LOG_MARKER} "
            f"service={service_label} operation={operation_label} "
            f"executed_at={executed_at} -->"
        )
        body = marker + "\n\n_FlowSmith operation cooldown record._"
        run_gh(
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo,
                "--body",
                body,
            ]
        )
        log(
            "Recorded operation cooldown marker "
            f"service={service_label} operation={operation_label} at={executed_at}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FlowSmith queue + lock manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire = subparsers.add_parser("acquire", help="Acquire issue lock with queue semantics")
    acquire.add_argument("--repo", required=True)
    acquire.add_argument("--issue-number", required=True, type=int)
    acquire.add_argument("--lock-label", default=DEFAULT_LOCK_LABEL)
    acquire.add_argument("--max-parallel", default=DEFAULT_MAX_PARALLEL, type=int)
    acquire.add_argument("--poll-seconds", default=DEFAULT_POLL_SECONDS, type=int)
    acquire.add_argument("--timeout-minutes", default=DEFAULT_TIMEOUT_MINUTES, type=int)
    acquire.add_argument("--stale-minutes", default=DEFAULT_STALE_MINUTES, type=int)
    acquire.add_argument("--cooldown-minutes", default=DEFAULT_COOLDOWN_MINUTES, type=int)
    acquire.add_argument("--service-label-prefix", default=DEFAULT_SERVICE_LABEL_PREFIX)
    acquire.add_argument("--operation-label-prefix", default=DEFAULT_OPERATION_LABEL_PREFIX)
    acquire.add_argument("--github-output", default=os.getenv("GITHUB_OUTPUT", ""))

    release = subparsers.add_parser("release", help="Release issue lock")
    release.add_argument("--repo", required=True)
    release.add_argument("--issue-number", required=True, type=int)
    release.add_argument("--lock-label", default=DEFAULT_LOCK_LABEL)
    release.add_argument("--service-label", default="")
    release.add_argument("--operation-label", default="")
    release.add_argument("--record-operation", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "acquire":
        return acquire_lock(args)
    if args.command == "release":
        return release_lock(args)
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as err:
        print(f"[agent-lock] ERROR: {err}", file=sys.stderr)
        raise SystemExit(1)
