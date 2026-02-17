#!/usr/bin/env python3
"""Autonomous issue-to-PR pipeline with multi-project support."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(".agent/pipeline.json")
DEFAULT_PROJECTS_PATH = Path(".agent/projects.json")


def log(message: str) -> None:
    print(f"[agent-pipeline] {message}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_positive_int(value: Any, *, default: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        raise RuntimeError(f"Config '{name}' must be a positive integer.")
    return parsed


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def clip_text(content: str, *, max_chars: int) -> str:
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    suffix = "\n...[truncated]"
    end = max(max_chars - len(suffix), 0)
    return content[:end].rstrip() + suffix


def resolve_repo_relative_path(value: str, *, repo_root: Path, setting_name: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise RuntimeError(f"Config '{setting_name}' must be a relative path.")
    resolved = (repo_root / relative).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as err:
        raise RuntimeError(
            f"Config '{setting_name}' points outside repository root: {value}"
        ) from err
    return resolved


def run_process(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        joined = " ".join(shlex.quote(a) for a in args)
        raise RuntimeError(
            f"Command failed: {joined}\n"
            f"exit={proc.returncode}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def run_shell(
    command: str,
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_process(["bash", "-lc", command], cwd=cwd, check=check, env=env)


def format_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def write_command_log(path: Path, args: list[str], proc: subprocess.CompletedProcess[str]) -> None:
    write_text(
        path,
        (
            f"# Command\n\n{format_command(args)}\n\n"
            f"# Exit Code\n\n{proc.returncode}\n\n"
            f"# Stdout\n\n{proc.stdout}\n\n"
            f"# Stderr\n\n{proc.stderr}\n"
        ),
    )


def run_logged_process(
    args: list[str],
    *,
    cwd: Path | None,
    log_file: Path,
    check: bool,
    error_message: str,
) -> subprocess.CompletedProcess[str]:
    proc = run_process(args, cwd=cwd, check=False)
    write_command_log(log_file, args, proc)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{error_message} See {log_file} for details.")
    return proc


def git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_process(["git", *args], cwd=cwd, check=check)


def format_template(template: str, context: dict[str, Any], template_name: str) -> str:
    try:
        return template.format(**context)
    except KeyError as err:
        missing = err.args[0]
        raise RuntimeError(
            f"Missing template variable '{missing}' in {template_name}."
        ) from err


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].strip("-")) or "task"


def resolve_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(merged[key], value)
            continue
        merged[key] = deepcopy(value)
    return merged


def normalize_repo_path(path_value: str) -> str:
    normalized = Path(path_value).as_posix()
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def normalize_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clip_inline_text(value: str, *, max_chars: int) -> str:
    normalized = normalize_inline_text(value)
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized
    suffix = "...[truncated]"
    end = max(max_chars - len(suffix), 0)
    return normalized[:end].rstrip() + suffix


def strip_markdown_prefix(line: str) -> str:
    text = line.strip()
    if not text:
        return ""
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text)
    return normalize_inline_text(text)


def is_noninformative_highlight(text: str) -> bool:
    normalized = normalize_inline_text(text).lower()
    if not normalized:
        return True
    generic_tokens = {
        "plan",
        "review",
        "summary",
        "overview",
        "scope",
        "notes",
        "todo",
        "概要",
        "要約",
        "実装計画",
        "検証結果",
        "レビューレポート",
    }
    if normalized in generic_tokens:
        return True
    if len(normalized) <= 2 and re.fullmatch(r"[a-z0-9]+", normalized):
        return True
    return False


def extract_text_highlights(raw_text: str, *, max_items: int, max_chars: int) -> list[str]:
    if max_items <= 0:
        return []
    lines = raw_text.splitlines()
    highlights: list[str] = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        normalized = strip_markdown_prefix(line)
        if not normalized:
            continue
        if is_noninformative_highlight(normalized):
            continue
        highlights.append(clip_inline_text(normalized, max_chars=max_chars))
        if len(highlights) >= max_items:
            break

    if highlights:
        return highlights
    fallback = clip_inline_text(raw_text or "(empty)", max_chars=max_chars).strip()
    return [fallback or "(empty)"]


def extract_file_highlights(path: Path, *, max_items: int, max_chars: int) -> list[str]:
    if not path.exists():
        return ["(missing)"]
    content = read_text(path).strip()
    if not content:
        return ["(empty)"]
    return extract_text_highlights(content, max_items=max_items, max_chars=max_chars)


def first_meaningful(items: list[str], *, fallback: str) -> str:
    for item in items:
        value = normalize_inline_text(item)
        if value and value not in {"(missing)", "(empty)"}:
            return value
    return fallback


def detect_attempt_status(validation_text: str) -> str:
    upper = validation_text.upper()
    if "FAIL" in upper:
        return "failed"
    if "PASS" in upper:
        return "passed"
    return "unknown"


def build_codex_commit_summary(
    *,
    run_dir: Path,
    context: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    summary_conf_raw = config.get("codex_commit_summary", {})
    if summary_conf_raw is None:
        summary_conf_raw = {}
    if not isinstance(summary_conf_raw, dict):
        raise RuntimeError("Config 'codex_commit_summary' must be an object when specified.")

    enabled = bool(summary_conf_raw.get("enabled", True))
    required = bool(summary_conf_raw.get("required", True))
    max_chars = parse_positive_int(
        summary_conf_raw.get("max_chars_per_item"),
        default=240,
        name="codex_commit_summary.max_chars_per_item",
    )
    max_attempts = parse_positive_int(
        summary_conf_raw.get("max_attempts"),
        default=5,
        name="codex_commit_summary.max_attempts",
    )
    max_points = parse_positive_int(
        summary_conf_raw.get("max_points"),
        default=3,
        name="codex_commit_summary.max_points",
    )
    max_total_chars = parse_positive_int(
        summary_conf_raw.get("max_total_chars"),
        default=3000,
        name="codex_commit_summary.max_total_chars",
    )

    default_state = {
        "codex_commit_summary_required": required,
        "codex_commit_summary_status": "skipped",
        "codex_commit_summary_appendix": "",
        "codex_commit_summary_markdown": "_Codex判断ログは未生成です。_",
    }
    if not enabled:
        write_text(run_dir / "codex_commit_summary_status.md", "- Codex要約は無効です。\n")
        return default_state

    try:
        issue_title = str(context.get("issue_title", "")).strip() or "(untitled)"
        issue_number = context.get("issue_number")
        issue_body = str(context.get("issue_body", "")).strip()
        issue_url = str(context.get("issue_url", "")).strip()
        plan_file = Path(str(context.get("plan_file", run_dir / "plan.md")))
        review_file = Path(str(context.get("review_file", run_dir / "review.md")))
        planner_prompt = run_dir / "planner_prompt.md"
        quality_gates = config.get("quality_gates", [])
        quality_gate_lines: list[str] = []
        if isinstance(quality_gates, list):
            for gate in quality_gates:
                gate_text = str(gate).strip()
                if gate_text:
                    quality_gate_lines.append(f"`{gate_text}`")

        issue_points = extract_text_highlights(
            issue_body or "(Issue 本文なし)",
            max_items=max_points,
            max_chars=max_chars,
        )
        planner_prompt_points = extract_file_highlights(
            planner_prompt,
            max_items=1,
            max_chars=max_chars,
        )
        plan_points = extract_file_highlights(
            plan_file,
            max_items=max_points,
            max_chars=max_chars,
        )
        review_points = extract_file_highlights(
            review_file,
            max_items=max_points,
            max_chars=max_chars,
        )

        attempt_ids: set[int] = set()
        for path in run_dir.glob("coder_output_attempt_*.md"):
            idx = extract_attempt_index(path.name)
            if idx != sys.maxsize:
                attempt_ids.add(idx)
        for path in run_dir.glob("validation_attempt_*.md"):
            idx = extract_attempt_index(path.name)
            if idx != sys.maxsize:
                attempt_ids.add(idx)

        attempt_rows: list[dict[str, Any]] = []
        last_validation_lines: list[str] = ["(missing)"]
        for idx in sorted(attempt_ids)[:max_attempts]:
            coder_prompt_points = extract_file_highlights(
                run_dir / f"coder_prompt_attempt_{idx}.md",
                max_items=1,
                max_chars=max_chars,
            )
            coder_points = extract_file_highlights(
                run_dir / f"coder_output_attempt_{idx}.md",
                max_items=1,
                max_chars=max_chars,
            )
            validation_path = run_dir / f"validation_attempt_{idx}.md"
            validation_points = extract_file_highlights(
                validation_path,
                max_items=max_points,
                max_chars=max_chars,
            )
            validation_raw = read_text(validation_path) if validation_path.exists() else ""
            status = detect_attempt_status(validation_raw)
            goal = first_meaningful(
                coder_prompt_points,
                fallback="要件実装のための変更を実施",
            )
            action = first_meaningful(
                coder_points,
                fallback="変更内容の詳細は ai-logs を参照",
            )
            validation = first_meaningful(
                validation_points,
                fallback="検証ログを参照",
            )
            attempt_rows.append(
                {
                    "attempt": idx,
                    "status": status,
                    "goal": goal,
                    "action": action,
                    "validation": validation,
                }
            )
            last_validation_lines = validation_points

        problem_line = f"Issue #{issue_number} {issue_title}"
        decision_line = first_meaningful(
            plan_points,
            fallback="最小変更で要件を満たす方針",
        )
        validation_line = first_meaningful(
            last_validation_lines,
            fallback=clip_inline_text(
                str(context.get("validation_summary", "検証結果なし")),
                max_chars=max_chars,
            ),
        )
        risk_line = first_meaningful(
            review_points,
            fallback="重大な未解決リスクは記録されていません。",
        )
        issue_basis = first_meaningful(
            issue_points,
            fallback=clip_inline_text(issue_title, max_chars=max_chars),
        )
        implementation_basis = first_meaningful(
            plan_points[1:] if len(plan_points) > 1 else plan_points,
            fallback=decision_line,
        )
        quality_basis = ", ".join(quality_gate_lines) if quality_gate_lines else "quality_gates 未設定"

        ai_logs_conf_raw = config.get("ai_logs", {})
        if ai_logs_conf_raw is None:
            ai_logs_conf_raw = {}
        if not isinstance(ai_logs_conf_raw, dict):
            raise RuntimeError("Config 'ai_logs' must be an object when specified.")
        ai_logs_path_template = str(
            ai_logs_conf_raw.get("path", "ai-logs/issue-{issue_number}-{run_timestamp}")
        ).strip() or "ai-logs/issue-{issue_number}-{run_timestamp}"
        ai_logs_index_name = str(ai_logs_conf_raw.get("index_file", "index.md")).strip() or "index.md"
        ai_logs_dir = format_template(ai_logs_path_template, context, "ai_logs.path")
        evidence_path = normalize_repo_path(str(Path(ai_logs_dir) / ai_logs_index_name))

        appendix_lines = [
            "Codex-Summary:",
            f"- Problem: {clip_inline_text(problem_line, max_chars=max_chars)}",
            f"- Decision: {decision_line}",
            f"- Validation: {validation_line}",
            f"- Risk: {risk_line}",
            "",
            "Codex-Log-Reference:",
            f"- AI Logs: {evidence_path}",
        ]
        appendix_text = "\n".join(appendix_lines).strip()
        if len(appendix_text) > max_total_chars:
            appendix_text = clip_text(appendix_text, max_chars=max_total_chars).strip()

        request_lines = [f"- {item}" for item in issue_points]
        validation_result_lines = [f"- {item}" for item in last_validation_lines]
        if not validation_result_lines:
            validation_result_lines = ["- 検証結果なし"]

        risk_lines = [f"- {item}" for item in review_points[:max_points]]
        if not risk_lines:
            risk_lines = ["- 重大な未解決リスクは記録されていません。"]

        attempt_markdown_lines: list[str] = []
        if attempt_rows:
            for row in attempt_rows:
                attempt_markdown_lines.extend(
                    [
                        f"- attempt {row['attempt']} ({row['status']})",
                        f"  - 目的: {row['goal']}",
                        f"  - 実施: {row['action']}",
                        f"  - 結果: {row['validation']}",
                    ]
                )
        else:
            attempt_markdown_lines.append("- attempt記録なし")

        markdown_lines = [
            "### TL;DR",
            f"- 課題: {clip_inline_text(problem_line, max_chars=max_chars)}",
            f"- 採用方針: {decision_line}",
            f"- 検証結果: {validation_line}",
            "",
            "### 要求の再解釈",
            *request_lines,
            "",
            "### Decision Log",
            "| ID | 論点 | 選択肢 | 採用案 | 根拠 | 影響 |",
            "|---|---|---|---|---|---|",
            f"| D1 | スコープ | 最小変更 / 拡張変更 | {decision_line} | {issue_basis} | 変更範囲を限定し、実装速度を優先 |",
            f"| D2 | 実装方式 | 既存構成順守 / 新規構成追加 | {implementation_basis} | {first_meaningful(planner_prompt_points, fallback='Planner指示')} | 保守性と追従性を確保 |",
            f"| D3 | 検証方式 | 最小確認 / 包括確認 | {validation_line} | {quality_basis} | リグレッション検知性を確保 |",
            "",
            "### 試行ログ",
            *attempt_markdown_lines,
            "",
            "### 検証結果",
            *validation_result_lines,
            "",
            "### 残リスク・未解決",
            *risk_lines,
            "",
            "### 証跡リンク",
            f"- Issue: {issue_url or '(local file)'}",
            f"- ai-logs: `{evidence_path}`",
            f"- run_dir: `{run_dir}`",
        ]
        markdown_text = "\n".join(markdown_lines).strip()
    except (RuntimeError, OSError) as err:
        if required:
            raise RuntimeError(f"Codex要約生成に失敗しました: {err}") from err
        message = f"Codex要約生成をスキップしました: {err}"
        log(f"WARNING: {message}")
        write_text(run_dir / "codex_commit_summary_status.md", f"- {message}\n")
        return default_state

    write_text(
        run_dir / "codex_commit_summary_status.md",
        (
            "- Codex要約を生成しました（2層構成）。\n"
            f"- max_chars_per_item: `{max_chars}`\n"
            f"- max_attempts: `{max_attempts}`\n"
            f"- max_points: `{max_points}`\n"
        ),
    )
    write_text(run_dir / "codex_commit_summary.md", appendix_text + "\n")
    return {
        **default_state,
        "codex_commit_summary_status": "generated",
        "codex_commit_summary_appendix": appendix_text,
        "codex_commit_summary_markdown": markdown_text,
    }


def render_issue_instruction_markdown(
    *,
    issue_number: int,
    issue_title: str,
    issue_url: str,
    issue_body: str,
    max_chars: int = 12000,
) -> str:
    body = issue_body.strip() if issue_body else ""
    if not body:
        body = "(Issue 本文なし)"
    clipped_body = clip_text(body, max_chars=max_chars).strip()
    url_value = issue_url.strip() if issue_url and issue_url.strip() else "(local file)"
    return (
        f"- Issue: `#{issue_number}` `{issue_title}`\n"
        f"- URL: {url_value}\n"
        "- 要求本文:\n\n"
        "~~~text\n"
        f"{clipped_body}\n"
        "~~~"
    )


def render_validation_commands_markdown(commands: list[str]) -> str:
    if not commands:
        return "- `(quality_gates 未設定)`"
    return "\n".join(f"- `{item}`" for item in commands)


def render_log_location_markdown(context: dict[str, Any]) -> str:
    ai_logs_status = str(context.get("ai_logs_status", "unknown")).strip() or "unknown"
    ai_logs_dir = str(context.get("ai_logs_dir", "未保存")).strip() or "未保存"
    ai_logs_index_file = str(context.get("ai_logs_index_file", "未保存")).strip() or "未保存"
    ai_logs_url = str(context.get("ai_logs_index_url", "")).strip()
    ai_logs_publish_mode = str(context.get("ai_logs_publish_mode", "same-branch")).strip() or "same-branch"
    ai_logs_publish_status = str(context.get("ai_logs_publish_status", "unknown")).strip() or "unknown"
    ai_logs_publish_branch = str(context.get("ai_logs_publish_branch", "")).strip()
    ai_logs_publish_commit = str(context.get("ai_logs_publish_commit", "")).strip()
    run_dir = str(context.get("run_dir", "")).strip()
    entire_trace_file = str(context.get("entire_trace_file", "")).strip()

    lines = [
        f"- AIログ保存状態: `{ai_logs_status}`",
        f"- AIログディレクトリ: `{ai_logs_dir}`",
        f"- AIログインデックス: `{ai_logs_index_file}`",
        f"- AIログ保存モード: `{ai_logs_publish_mode}`",
    ]
    if ai_logs_publish_mode == "dedicated-branch":
        lines.append(f"- AIログ保存ブランチ: `{ai_logs_publish_branch or '未設定'}`")
        lines.append(f"- AIログブランチ反映状態: `{ai_logs_publish_status}`")
        if ai_logs_publish_commit:
            lines.append(f"- AIログブランチコミット: `{ai_logs_publish_commit}`")
    if ai_logs_url:
        lines.append(f"- AIログリンク: {ai_logs_url}")
    else:
        lines.append("- AIログリンク: `(コミット後に生成)`")
    if run_dir:
        lines.append(f"- FlowSmith 実行ログ(ローカル): `{run_dir}`")
    if entire_trace_file and entire_trace_file != "未登録":
        lines.append(f"- Entire 明示証跡: `{entire_trace_file}`")
    return "\n".join(lines)


def validate_required_pr_context(context: dict[str, Any]) -> None:
    required_items = {
        "instruction_markdown": "指示内容",
        "validation_commands_markdown": "検証コマンド",
        "log_location_markdown": "ログの場所",
    }
    missing_labels: list[str] = []
    for key, label in required_items.items():
        value = str(context.get(key, "")).strip()
        if not value:
            missing_labels.append(label)
    if missing_labels:
        raise RuntimeError(
            "PR本文の必須項目が不足しています: " + ", ".join(missing_labels)
        )

    ai_logs_required = bool(context.get("ai_logs_required", True))
    ai_logs_status = str(context.get("ai_logs_status", "")).strip().lower()
    if ai_logs_required and ai_logs_status != "saved":
        raise RuntimeError(
            "PR本文の必須要件を満たせません: ai-logs が保存されていません。"
        )


def normalize_repo_slug(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    https_match = re.match(
        r"https?://[^/]+/([^/]+/[^/]+?)(?:\.git)?/?$",
        value,
        flags=re.IGNORECASE,
    )
    if https_match:
        return https_match.group(1)

    ssh_match = re.match(r"git@[^:]+:([^/]+/[^/]+?)(?:\.git)?$", value)
    if ssh_match:
        return ssh_match.group(1)

    plain_match = re.match(r"^[^/]+/[^/]+$", value)
    if plain_match:
        return value.removesuffix(".git")

    return value.removesuffix(".git")


def detect_repo_slug(repo_root: Path) -> str:
    remote = git(["remote", "get-url", "origin"], cwd=repo_root, check=False)
    if remote.returncode != 0:
        return ""
    return normalize_repo_slug(remote.stdout.strip())


def require_clean_worktree(repo_root: Path) -> None:
    status = git(["status", "--porcelain"], cwd=repo_root)
    if status.stdout.strip():
        raise RuntimeError(
            f"Worktree is not clean: {repo_root}. Commit or stash local changes before running the pipeline."
        )


def validate_config(data: dict[str, Any], config_path: Path) -> None:
    if "commands" not in data:
        raise RuntimeError(f"Config is invalid ({config_path}): missing 'commands'.")
    if "templates" not in data:
        raise RuntimeError(f"Config is invalid ({config_path}): missing 'templates'.")


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(read_text(path))
    except FileNotFoundError as err:
        raise RuntimeError(f"JSON file not found: {path}") from err
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON in {path}: {err}") from err

    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def load_issue_from_file(path: Path, issue_number: int) -> dict[str, Any]:
    body = read_text(path).strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    title = lines[0].lstrip("# ").strip() if lines else f"Issue {issue_number}"
    return {
        "number": issue_number,
        "title": title or f"Issue {issue_number}",
        "body": body,
        "url": "",
        "labels": [],
    }


def load_issue_from_gh(issue_number: int, *, repo_slug: str, cwd: Path) -> dict[str, Any]:
    cmd = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        "--json",
        "number,title,body,url,labels",
    ]
    if repo_slug:
        cmd.extend(["--repo", repo_slug])

    proc = run_process(cmd, cwd=cwd, check=False)
    if proc.returncode != 0:
        target = repo_slug or str(cwd)
        raise RuntimeError(
            "Unable to read issue from GitHub. "
            "Use --issue-file for local runs or set GH_TOKEN for remote runs.\n"
            f"target={target}\n"
            f"stderr:\n{proc.stderr}"
        )

    payload = json.loads(proc.stdout)
    labels = [item["name"] for item in payload.get("labels", [])]
    return {
        "number": payload["number"],
        "title": payload["title"],
        "body": payload.get("body") or "",
        "url": payload.get("url") or "",
        "labels": labels,
    }


def resolve_command(raw: str, *, required: bool) -> str:
    value = (raw or "").strip()
    if not value:
        if required:
            raise RuntimeError("Required command is empty.")
        return ""

    if value.startswith("${") and value.endswith("}") and len(value) > 3:
        key = value[2:-1]
        resolved = os.getenv(key, "").strip()
        if not resolved and required:
            raise RuntimeError(f"Environment variable {key} is not set.")
        return resolved

    if value.startswith("$") and " " not in value and len(value) > 1:
        key = value[1:]
        resolved = os.getenv(key, "").strip()
        if not resolved and required:
            raise RuntimeError(f"Environment variable {key} is not set.")
        return resolved

    return value


def split_command(value: str, *, name: str) -> list[str]:
    try:
        parts = shlex.split(value)
    except ValueError as err:
        raise RuntimeError(f"Unable to parse {name}: {err}") from err
    if not parts:
        raise RuntimeError(f"{name} is empty.")
    return parts


def run_agent_command(
    *,
    step_name: str,
    command_template: str,
    context: dict[str, Any],
    repo_root: Path,
    prompt_file: Path,
    output_file: Path,
    log_file: Path,
    required_output: bool,
) -> None:
    rendered = format_template(command_template, context, f"{step_name} command")
    log(f"Running {step_name} command")
    proc = run_shell(rendered, cwd=repo_root, check=False)

    output = (
        f"# Command\n\n{rendered}\n\n"
        f"# Exit Code\n\n{proc.returncode}\n\n"
        f"# Stdout\n\n{proc.stdout}\n\n"
        f"# Stderr\n\n{proc.stderr}\n"
    )
    write_text(log_file, output)

    if proc.returncode != 0:
        stdout_excerpt = clip_text(proc.stdout.strip(), max_chars=1200)
        stderr_excerpt = clip_text(proc.stderr.strip(), max_chars=1200)
        raise RuntimeError(
            (
                f"{step_name} command failed. See {log_file} for details.\n"
                f"exit={proc.returncode}\n"
                f"stdout_excerpt:\n{stdout_excerpt or '(empty)'}\n"
                f"stderr_excerpt:\n{stderr_excerpt or '(empty)'}"
            )
        )

    if not output_file.exists():
        stdout = proc.stdout.strip()
        if stdout:
            write_text(output_file, stdout + "\n")

    if required_output:
        content = read_text(output_file).strip() if output_file.exists() else ""
        if not content:
            raise RuntimeError(
                f"{step_name} produced no output file content. "
                f"Expected output at {output_file}."
            )

    if not prompt_file.exists():
        raise RuntimeError(f"{step_name} prompt file missing: {prompt_file}")


def run_quality_gates(
    *,
    gates: list[str],
    repo_root: Path,
    run_dir: Path,
    attempt: int,
) -> tuple[bool, str]:
    if not gates:
        return True, "- No quality gates configured."

    lines: list[str] = []
    for idx, gate in enumerate(gates, start=1):
        gate_log = run_dir / f"gate-attempt-{attempt}-{idx}.log"
        proc = run_shell(gate, cwd=repo_root, check=False)
        gate_report = (
            f"# Gate\n\n{gate}\n\n"
            f"# Exit Code\n\n{proc.returncode}\n\n"
            f"# Stdout\n\n{proc.stdout}\n\n"
            f"# Stderr\n\n{proc.stderr}\n"
        )
        write_text(gate_log, gate_report)
        if proc.returncode == 0:
            lines.append(f"- PASS `{gate}`")
            continue

        lines.append(f"- FAIL `{gate}` (see `{gate_log}`)")
        return False, "\n".join(lines)

    return True, "\n".join(lines)


def render_template_file(path: Path, context: dict[str, Any]) -> str:
    return format_template(read_text(path), context, str(path))


def ensure_branch(
    repo_root: Path,
    base_branch: str,
    branch_name: str,
    *,
    sync_base: bool,
) -> None:
    if sync_base:
        git(["fetch", "origin", base_branch], cwd=repo_root, check=False)

    git(["checkout", base_branch], cwd=repo_root)

    if sync_base:
        git(["pull", "--ff-only", "origin", base_branch], cwd=repo_root, check=False)

    git(["checkout", "-B", branch_name], cwd=repo_root)


def setup_entire_trace(
    *,
    repo_root: Path,
    run_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    entire_conf_raw = config.get("entire", {})
    if not isinstance(entire_conf_raw, dict):
        raise RuntimeError("Config 'entire' must be an object when specified.")

    enabled = bool(entire_conf_raw.get("enabled", False))
    required = bool(entire_conf_raw.get("required", False))
    trailer_key = str(entire_conf_raw.get("trailer_key", "Entire-Checkpoint")).strip() or "Entire-Checkpoint"
    verify_trailer = bool(entire_conf_raw.get("verify_trailer", True))
    explicit_conf_raw = entire_conf_raw.get("explicit_registration", {})
    if explicit_conf_raw is None:
        explicit_conf_raw = {}
    if not isinstance(explicit_conf_raw, dict):
        raise RuntimeError("Config 'entire.explicit_registration' must be an object when specified.")
    explicit_enabled = bool(explicit_conf_raw.get("enabled", False) and enabled)
    explicit_required = bool(explicit_conf_raw.get("required", required))
    explicit_append_trailers = bool(explicit_conf_raw.get("append_commit_trailers", True))
    explicit_artifact_path = str(
        explicit_conf_raw.get(
            "artifact_path",
            ".entire/evidence/issue-{issue_number}-{run_timestamp}.md",
        )
    ).strip() or ".entire/evidence/issue-{issue_number}-{run_timestamp}.md"
    explicit_max_chars = parse_positive_int(
        explicit_conf_raw.get("max_chars_per_section", 6000),
        default=6000,
        name="entire.explicit_registration.max_chars_per_section",
    )
    explicit_generate_explain = bool(explicit_conf_raw.get("generate_explain", True))

    default_state = {
        "entire_enabled": enabled,
        "entire_required": required,
        "entire_verify_trailer": verify_trailer,
        "entire_trailer_key": trailer_key,
        "entire_status": "disabled",
        "entire_agent": "",
        "entire_strategy": "",
        "entire_command": "",
        "entire_setup_log": "",
        "entire_explicit_enabled": explicit_enabled,
        "entire_explicit_required": explicit_required,
        "entire_explicit_append_commit_trailers": explicit_append_trailers,
        "entire_explicit_artifact_path_template": explicit_artifact_path,
        "entire_explicit_max_chars_per_section": explicit_max_chars,
        "entire_explicit_generate_explain": explicit_generate_explain,
        "entire_trace_status": "skipped",
        "entire_trace_file": "未登録",
        "entire_trace_sha256": "",
        "entire_trace_attempts": 0,
        "entire_trace_commit_appendix": "",
        "entire_trace_verify_status": "skipped",
        "entire_explain_status": "skipped",
        "entire_explain_log": "",
    }

    if not enabled:
        write_text(run_dir / "entire_status.md", "- Entire 連携は無効です。\n")
        return default_state

    raw_command = str(entire_conf_raw.get("command", "entire")).strip() or "entire"
    resolved_command = resolve_command(raw_command, required=False)
    if not resolved_command:
        message = "Entire コマンドが設定されていないため、証跡連携をスキップします。"
        if required:
            raise RuntimeError(message)
        log(message)
        write_text(run_dir / "entire_status.md", f"- {message}\n")
        return {
            **default_state,
            "entire_status": "skipped",
        }

    command_parts = split_command(resolved_command, name="entire.command")
    version_log = run_dir / "entire_version.log"
    version_proc = run_logged_process(
        [*command_parts, "version"],
        cwd=repo_root,
        log_file=version_log,
        check=False,
        error_message="Entire バージョン確認に失敗しました。",
    )
    if version_proc.returncode != 0:
        message = "Entire CLI が利用できないため、証跡連携をスキップします。"
        if required:
            raise RuntimeError(f"{message} See {version_log} for details.")
        log(message)
        write_text(run_dir / "entire_status.md", f"- {message}\n")
        return {
            **default_state,
            "entire_status": "skipped",
            "entire_command": resolved_command,
            "entire_setup_log": str(version_log),
        }

    strategy = str(entire_conf_raw.get("strategy", "manual-commit")).strip() or "manual-commit"
    scope = str(entire_conf_raw.get("scope", "project")).strip().lower() or "project"
    agent = str(entire_conf_raw.get("agent", "codex")).strip() or "codex"

    strategy_log = run_dir / "entire_strategy.log"
    strategy_proc = run_logged_process(
        [*command_parts, "strategy", "set", strategy],
        cwd=repo_root,
        log_file=strategy_log,
        check=False,
        error_message="Entire strategy 設定に失敗しました。",
    )
    if strategy_proc.returncode != 0 and required:
        raise RuntimeError(f"Entire strategy 設定に失敗しました。See {strategy_log} for details.")

    enable_cmd = [*command_parts, "enable", "--agent", agent]
    if scope == "global":
        enable_cmd.append("--global")
    else:
        enable_cmd.append("--project")

    if strategy == "manual-commit":
        enable_cmd.append("--manual-commit")
    elif strategy == "auto-commit":
        enable_cmd.append("--auto-commit")

    enable_log = run_dir / "entire_enable.log"
    enable_proc = run_logged_process(
        enable_cmd,
        cwd=repo_root,
        log_file=enable_log,
        check=False,
        error_message="Entire enable 実行に失敗しました。",
    )
    if enable_proc.returncode != 0:
        message = "Entire enable の実行に失敗したため、証跡連携をスキップします。"
        if required:
            raise RuntimeError(f"{message} See {enable_log} for details.")
        log(message)
        write_text(run_dir / "entire_status.md", f"- {message}\n")
        return {
            **default_state,
            "entire_status": "skipped",
            "entire_agent": agent,
            "entire_strategy": strategy,
            "entire_command": resolved_command,
            "entire_setup_log": str(enable_log),
        }

    write_text(
        run_dir / "entire_status.md",
        (
            "- Entire 連携を有効化しました。\n"
            f"- command: `{resolved_command}`\n"
            f"- agent: `{agent}`\n"
            f"- strategy: `{strategy}`\n"
            f"- trailer_key: `{trailer_key}`\n"
            f"- explicit_registration.enabled: `{explicit_enabled}`\n"
            f"- explicit_registration.artifact_path: `{explicit_artifact_path}`\n"
            f"- explicit_registration.append_commit_trailers: `{explicit_append_trailers}`\n"
            f"- explicit_registration.generate_explain: `{explicit_generate_explain}`\n"
        ),
    )
    return {
        **default_state,
        "entire_status": "enabled",
        "entire_agent": agent,
        "entire_strategy": strategy,
        "entire_command": resolved_command,
        "entire_setup_log": str(enable_log),
    }


def commit_changes(
    repo_root: Path,
    message: str,
    *,
    ignore_paths: list[str] | None = None,
    force_add_paths: list[str] | None = None,
    required_paths: list[str] | None = None,
) -> None:
    git(["add", "-A"], cwd=repo_root)
    force_add_set = {
        normalize_repo_path(str(item))
        for item in (force_add_paths or [])
        if str(item).strip()
    }
    for path in sorted(force_add_set):
        git(["add", "-f", "--", path], cwd=repo_root)

    staged_names = git(["diff", "--cached", "--name-only"], cwd=repo_root)
    staged_paths = [line.strip() for line in staged_names.stdout.splitlines() if line.strip()]
    if not staged_paths:
        raise RuntimeError("No file changes were created by the coder agent.")

    ignore_set = {
        normalize_repo_path(str(item))
        for item in (ignore_paths or [])
        if str(item).strip()
    }
    meaningful_changes = [
        path
        for path in staged_paths
        if normalize_repo_path(path) not in ignore_set
    ]
    if not meaningful_changes:
        raise RuntimeError(
            "No file changes were created by the coder agent. "
            "Only trace artifact files were changed."
        )

    required_set = {
        normalize_repo_path(str(item))
        for item in (required_paths or [])
        if str(item).strip()
    }
    missing_required = sorted(path for path in required_set if path not in staged_paths)
    if missing_required:
        joined = ", ".join(missing_required)
        raise RuntimeError(
            "Required trace artifact files are not staged for commit: "
            f"{joined}"
        )

    git(["commit", "-m", message], cwd=repo_root)


def get_head_commit_sha(repo_root: Path) -> str:
    return git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()


def get_head_commit_message(repo_root: Path) -> str:
    return git(["log", "-1", "--pretty=%B"], cwd=repo_root).stdout


def extract_commit_trailer(commit_message: str, trailer_key: str) -> str:
    pattern = re.compile(rf"(?mi)^{re.escape(trailer_key)}:\s*(.+)$")
    match = pattern.search(commit_message)
    if not match:
        return ""
    return match.group(1).strip()


def extract_attempt_index(file_name: str) -> int:
    match = re.search(r"_attempt_(\d+)\.md$", file_name)
    if not match:
        return sys.maxsize
    return int(match.group(1))


def render_trace_file_section(
    *,
    title: str,
    path: Path,
    max_chars: int,
) -> str:
    lines = [f"### {title}", f"- source: `{path.name}`"]
    if not path.exists():
        lines.append("- status: missing")
        lines.append("")
        return "\n".join(lines)

    raw_text = read_text(path)
    digest = sha256_text(raw_text)
    clipped = clip_text(raw_text.strip(), max_chars=max_chars).strip()
    lines.append(f"- sha256: `{digest}`")
    lines.append("")
    lines.append("~~~text")
    lines.append(clipped or "(empty)")
    lines.append("~~~")
    lines.append("")
    return "\n".join(lines)


def build_entire_registration_markdown(
    *,
    run_dir: Path,
    context: dict[str, Any],
    max_chars: int,
) -> tuple[str, int]:
    prompt_paths = [run_dir / "planner_prompt.md"]
    prompt_paths.extend(sorted(run_dir.glob("coder_prompt_attempt_*.md"), key=lambda item: extract_attempt_index(item.name)))
    prompt_paths.append(run_dir / "reviewer_prompt.md")

    attempt_numbers: set[int] = set()
    for path in run_dir.glob("*_attempt_*.md"):
        index = extract_attempt_index(path.name)
        if index != sys.maxsize:
            attempt_numbers.add(index)

    lines: list[str] = [
        "# Entire 証跡登録",
        "",
        "## メタデータ",
        f"- issue: `#{context.get('issue_number')}`",
        f"- branch: `{context.get('branch_name')}`",
        f"- project: `{context.get('project_id') or 'default'}`",
        f"- target_repo: `{context.get('target_repo') or '(inferred local git)'}`",
        f"- run_timestamp: `{context.get('run_timestamp')}`",
        f"- run_dir: `{run_dir}`",
        "",
        "## 1. 指示したプロンプト",
        "",
    ]
    for path in prompt_paths:
        title = path.name
        lines.append(render_trace_file_section(title=title, path=path, max_chars=max_chars))

    lines.extend(["## 2. 試行錯誤", ""])
    for attempt in sorted(attempt_numbers):
        lines.append(f"### attempt {attempt}")
        lines.append(
            render_trace_file_section(
                title=f"coder_output_attempt_{attempt}.md",
                path=run_dir / f"coder_output_attempt_{attempt}.md",
                max_chars=max_chars,
            )
        )
        lines.append(
            render_trace_file_section(
                title=f"validation_attempt_{attempt}.md",
                path=run_dir / f"validation_attempt_{attempt}.md",
                max_chars=max_chars,
            )
        )

    lines.extend(["## 3. 設計根拠", ""])
    plan_file = Path(str(context.get("plan_file", run_dir / "plan.md")))
    review_file = Path(str(context.get("review_file", run_dir / "review.md")))
    lines.append(render_trace_file_section(title=plan_file.name, path=plan_file, max_chars=max_chars))
    lines.append(render_trace_file_section(title=review_file.name, path=review_file, max_chars=max_chars))

    content = "\n".join(lines).strip() + "\n"
    return content, len(attempt_numbers)


def build_commit_message(base: str, appendix: str) -> str:
    base_text = base.strip()
    appendix_text = appendix.strip()
    if not appendix_text:
        return base_text
    return f"{base_text}\n\n{appendix_text}"


def prepare_entire_explicit_registration(
    *,
    repo_root: Path,
    run_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    explicit_enabled = bool(context.get("entire_explicit_enabled"))
    explicit_required = bool(context.get("entire_explicit_required"))
    append_trailers = bool(context.get("entire_explicit_append_commit_trailers"))
    max_chars = parse_positive_int(
        context.get("entire_explicit_max_chars_per_section"),
        default=6000,
        name="entire.explicit_registration.max_chars_per_section",
    )

    default_state = {
        "entire_trace_status": "skipped",
        "entire_trace_file": "未登録",
        "entire_trace_sha256": "",
        "entire_trace_attempts": 0,
        "entire_trace_commit_appendix": "",
    }
    if not explicit_enabled:
        write_text(run_dir / "entire_registration_status.md", "- 明示登録は無効です。\n")
        return default_state

    artifact_template = str(
        context.get(
            "entire_explicit_artifact_path_template",
            ".entire/evidence/issue-{issue_number}-{run_timestamp}.md",
        )
    ).strip()
    if not artifact_template:
        message = "entire.explicit_registration.artifact_path が空です。"
        if explicit_required:
            raise RuntimeError(message)
        log(f"WARNING: {message}")
        write_text(run_dir / "entire_registration_status.md", f"- {message}\n")
        return default_state

    try:
        artifact_relative_path = format_template(
            artifact_template,
            context,
            "entire.explicit_registration.artifact_path",
        ).strip()
        artifact_path = resolve_repo_relative_path(
            artifact_relative_path,
            repo_root=repo_root,
            setting_name="entire.explicit_registration.artifact_path",
        )
        artifact_content, attempt_count = build_entire_registration_markdown(
            run_dir=run_dir,
            context=context,
            max_chars=max_chars,
        )
        artifact_sha = sha256_text(artifact_content)
    except RuntimeError as err:
        if explicit_required:
            raise
        message = f"明示登録バンドル生成をスキップしました: {err}"
        log(f"WARNING: {message}")
        write_text(run_dir / "entire_registration_status.md", f"- {message}\n")
        return default_state

    write_text(run_dir / "entire_registration_bundle.md", artifact_content)
    write_text(artifact_path, artifact_content)

    commit_appendix = ""
    if append_trailers:
        commit_appendix = (
            "AI-Trace:\n"
            f"- Evidence File: `{artifact_relative_path}`\n"
            f"- Evidence SHA256: `{artifact_sha}`\n"
            f"- Attempts: `{attempt_count}`\n"
            f"- Run Dir: `{run_dir}`\n\n"
            f"Entire-Trace-File: {artifact_relative_path}\n"
            f"Entire-Trace-SHA256: {artifact_sha}"
        )

    write_text(
        run_dir / "entire_registration_status.md",
        (
            "- 明示登録バンドルを生成しました。\n"
            f"- artifact: `{artifact_relative_path}`\n"
            f"- sha256: `{artifact_sha}`\n"
            f"- attempts: `{attempt_count}`\n"
            f"- append_commit_trailers: `{append_trailers}`\n"
        ),
    )
    return {
        **default_state,
        "entire_trace_status": "registered",
        "entire_trace_file": artifact_relative_path,
        "entire_trace_sha256": artifact_sha,
        "entire_trace_attempts": attempt_count,
        "entire_trace_commit_appendix": commit_appendix,
    }


def verify_entire_explicit_registration(
    *,
    repo_root: Path,
    run_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    explicit_enabled = bool(context.get("entire_explicit_enabled"))
    explicit_required = bool(context.get("entire_explicit_required"))
    append_trailers = bool(context.get("entire_explicit_append_commit_trailers"))

    default_state = {
        "entire_trace_verify_status": "skipped",
    }
    if not explicit_enabled:
        write_text(run_dir / "entire_registration_check.md", "- 明示登録検証は無効です。\n")
        return default_state

    checks: list[str] = []
    errors: list[str] = []
    commit_message = get_head_commit_message(repo_root)

    trace_file = str(context.get("entire_trace_file", "")).strip()
    trace_hash = str(context.get("entire_trace_sha256", "")).strip()
    if append_trailers:
        trailer_file = extract_commit_trailer(commit_message, "Entire-Trace-File")
        trailer_hash = extract_commit_trailer(commit_message, "Entire-Trace-SHA256")
        checks.append(f"- trailer_file: `{trailer_file or '未検出'}`")
        checks.append(f"- trailer_hash: `{trailer_hash or '未検出'}`")
        if not trailer_file:
            errors.append("コミットメッセージに Entire-Trace-File トレーラーがありません。")
        if not trailer_hash:
            errors.append("コミットメッセージに Entire-Trace-SHA256 トレーラーがありません。")
        if trailer_file and trace_file and trailer_file != trace_file:
            errors.append(
                "コミットトレーラーの Entire-Trace-File が生成値と一致しません。 "
                f"trailer={trailer_file} generated={trace_file}"
            )
        if trailer_hash and trace_hash and trailer_hash != trace_hash:
            errors.append(
                "コミットトレーラーの Entire-Trace-SHA256 が生成値と一致しません。 "
                f"trailer={trailer_hash} generated={trace_hash}"
            )

    if trace_file:
        try:
            trace_path = resolve_repo_relative_path(
                trace_file,
                repo_root=repo_root,
                setting_name="Entire-Trace-File",
            )
        except RuntimeError as err:
            errors.append(str(err))
        else:
            if not trace_path.exists():
                errors.append(f"証跡ファイルが見つかりません: {trace_file}")
            else:
                actual_hash = sha256_text(read_text(trace_path))
                checks.append(f"- artifact_hash: `{actual_hash}`")
                in_head = git(
                    ["cat-file", "-e", f"HEAD:{trace_file}"],
                    cwd=repo_root,
                    check=False,
                ).returncode == 0
                checks.append(f"- artifact_in_head: `{'yes' if in_head else 'no'}`")
                if not in_head:
                    errors.append(f"証跡ファイルが HEAD コミットに含まれていません: {trace_file}")
                if trace_hash and actual_hash != trace_hash:
                    errors.append(
                        "証跡ファイルの SHA256 が生成値と一致しません。 "
                        f"actual={actual_hash} expected={trace_hash}"
                    )
    else:
        errors.append("証跡ファイルの情報が context にありません。")

    report_lines = ["# Entire 明示登録検証", "", *checks]
    if errors:
        report_lines.extend(["", "## エラー", *[f"- {item}" for item in errors]])
    else:
        report_lines.extend(["", "- 明示登録検証に成功しました。"])
    write_text(run_dir / "entire_registration_check.md", "\n".join(report_lines).strip() + "\n")

    if errors:
        if explicit_required:
            raise RuntimeError("\n".join(errors))
        log("WARNING: Entire 明示登録の検証でエラーが発生しました。")
        for message in errors:
            log(f"WARNING: {message}")
        return {
            **default_state,
            "entire_trace_verify_status": "failed",
        }

    return {
        **default_state,
        "entire_trace_verify_status": "passed",
    }


def generate_entire_explain(
    *,
    repo_root: Path,
    run_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(context.get("entire_status") == "enabled")
    explicit_enabled = bool(context.get("entire_explicit_enabled"))
    should_generate = bool(context.get("entire_explicit_generate_explain"))
    required = bool(context.get("entire_explicit_required"))

    default_state = {
        "entire_explain_status": "skipped",
        "entire_explain_log": "",
    }
    if not (enabled and explicit_enabled and should_generate):
        return default_state

    raw_command = str(context.get("entire_command", "")).strip()
    if not raw_command:
        message = "Entire CLI コマンドが未設定のため explain 生成をスキップします。"
        if required:
            raise RuntimeError(message)
        log(f"WARNING: {message}")
        return default_state

    command_parts = split_command(raw_command, name="entire.command")
    explain_log = run_dir / "entire_explain_generate.log"
    explain_proc = run_logged_process(
        [*command_parts, "explain", "--commit", "HEAD", "--generate", "--no-pager"],
        cwd=repo_root,
        log_file=explain_log,
        check=False,
        error_message="Entire explain 生成に失敗しました。",
    )
    if explain_proc.returncode != 0:
        message = "Entire explain --generate の実行に失敗しました。"
        if required:
            raise RuntimeError(f"{message} See {explain_log} for details.")
        log(f"WARNING: {message}")
        return {
            **default_state,
            "entire_explain_status": "failed",
            "entire_explain_log": str(explain_log),
        }

    return {
        **default_state,
        "entire_explain_status": "generated",
        "entire_explain_log": str(explain_log),
    }


def save_ai_logs_bundle(
    *,
    repo_root: Path,
    run_dir: Path,
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    ai_logs_conf_raw = config.get("ai_logs", {})
    if ai_logs_conf_raw is None:
        ai_logs_conf_raw = {}
    if not isinstance(ai_logs_conf_raw, dict):
        raise RuntimeError("Config 'ai_logs' must be an object when specified.")

    enabled = bool(ai_logs_conf_raw.get("enabled", True))
    required = bool(ai_logs_conf_raw.get("required", True))
    dir_template = (
        str(ai_logs_conf_raw.get("path", "ai-logs/issue-{issue_number}-{run_timestamp}")).strip()
        or "ai-logs/issue-{issue_number}-{run_timestamp}"
    )
    index_file_name = str(ai_logs_conf_raw.get("index_file", "index.md")).strip() or "index.md"

    default_state = {
        "ai_logs_required": required,
        "ai_logs_status": "skipped",
        "ai_logs_dir": "未保存",
        "ai_logs_index_file": "未保存",
        "ai_logs_index_url": "",
        "ai_logs_file_count": 0,
        "ai_logs_paths": [],
    }
    if not enabled:
        write_text(run_dir / "ai_logs_status.md", "- ai-logs 保存は無効です。\n")
        return default_state

    try:
        dir_relative_path = format_template(
            dir_template,
            context,
            "ai_logs.path",
        ).strip().rstrip("/")
        if not dir_relative_path:
            raise RuntimeError("ai_logs.path が空です。")
        logs_dir_path = resolve_repo_relative_path(
            dir_relative_path,
            repo_root=repo_root,
            setting_name="ai_logs.path",
        )

        source_files = sorted(path for path in run_dir.iterdir() if path.is_file())
        if not source_files:
            raise RuntimeError(f"ai-logs に保存するソースファイルがありません: {run_dir}")

        copied_relative_paths: list[str] = []
        for source in source_files:
            destination = logs_dir_path / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied_relative_paths.append(
                normalize_repo_path(str(Path(dir_relative_path) / source.name))
            )

        index_relative_path = normalize_repo_path(str(Path(dir_relative_path) / index_file_name))
        index_path = resolve_repo_relative_path(
            index_relative_path,
            repo_root=repo_root,
            setting_name="ai_logs.index_file",
        )
        file_list_markdown = "\n".join(f"- `{path}`" for path in copied_relative_paths)
        index_content = (
            "# AI Agent Logs\n\n"
            "## メタデータ\n"
            f"- issue: `#{context.get('issue_number')}`\n"
            f"- branch: `{context.get('branch_name')}`\n"
            f"- project: `{context.get('project_id') or 'default'}`\n"
            f"- target_repo: `{context.get('target_repo') or '(inferred local git)'}`\n"
            f"- run_timestamp: `{context.get('run_timestamp')}`\n"
            f"- source_run_dir: `{run_dir}`\n"
            f"- copied_file_count: `{len(copied_relative_paths)}`\n\n"
            "## 収集ファイル\n"
            f"{file_list_markdown}\n"
        )
        write_text(index_path, index_content)
        if index_relative_path not in copied_relative_paths:
            copied_relative_paths.append(index_relative_path)

    except (RuntimeError, OSError) as err:
        if required:
            raise RuntimeError(f"ai-logs 保存に失敗しました: {err}") from err
        message = f"ai-logs 保存をスキップしました: {err}"
        log(f"WARNING: {message}")
        write_text(run_dir / "ai_logs_status.md", f"- {message}\n")
        return default_state

    copied_relative_paths = sorted(set(copied_relative_paths))
    write_text(
        run_dir / "ai_logs_status.md",
        (
            "- ai-logs を保存しました。\n"
            f"- directory: `{dir_relative_path}`\n"
            f"- index: `{index_relative_path}`\n"
            f"- files: `{len(copied_relative_paths)}`\n"
        ),
    )
    return {
        **default_state,
        "ai_logs_status": "saved",
        "ai_logs_dir": normalize_repo_path(dir_relative_path),
        "ai_logs_index_file": index_relative_path,
        "ai_logs_file_count": len(copied_relative_paths),
        "ai_logs_paths": copied_relative_paths,
    }


def resolve_ai_logs_publish_settings(
    *,
    config: dict[str, Any],
    ai_logs_required: bool,
) -> dict[str, Any]:
    ai_logs_conf_raw = config.get("ai_logs", {})
    if ai_logs_conf_raw is None:
        ai_logs_conf_raw = {}
    if not isinstance(ai_logs_conf_raw, dict):
        raise RuntimeError("Config 'ai_logs' must be an object when specified.")

    publish_conf_raw = ai_logs_conf_raw.get("publish", {})
    if publish_conf_raw is None:
        publish_conf_raw = {}
    if not isinstance(publish_conf_raw, dict):
        raise RuntimeError("Config 'ai_logs.publish' must be an object when specified.")

    mode = str(publish_conf_raw.get("mode", "same-branch")).strip().lower()
    if mode not in {"same-branch", "dedicated-branch"}:
        raise RuntimeError(
            "Config 'ai_logs.publish.mode' must be 'same-branch' or 'dedicated-branch'."
        )

    branch_name = ""
    if mode == "dedicated-branch":
        branch_name = str(publish_conf_raw.get("branch", "agent-ai-logs")).strip()
        if not branch_name:
            raise RuntimeError(
                "Config 'ai_logs.publish.branch' must be set when mode is dedicated-branch."
            )

    required = bool(publish_conf_raw.get("required", ai_logs_required))
    commit_message_template = str(
        publish_conf_raw.get(
            "commit_message",
            "chore(agent-logs): store issue #{issue_number} logs ({run_timestamp})",
        )
    ).strip() or "chore(agent-logs): store issue #{issue_number} logs ({run_timestamp})"

    return {
        "mode": mode,
        "branch": branch_name,
        "required": required,
        "commit_message_template": commit_message_template,
    }


def remove_ai_log_paths_from_worktree(
    *,
    repo_root: Path,
    relative_paths: list[str],
) -> None:
    normalized_paths = sorted(
        {
            normalize_repo_path(str(item))
            for item in relative_paths
            if str(item).strip()
        }
    )
    for relative_path in normalized_paths:
        resolved = resolve_repo_relative_path(
            relative_path,
            repo_root=repo_root,
            setting_name="ai_logs.path",
        )
        if resolved.is_file():
            resolved.unlink()
        elif resolved.is_dir():
            shutil.rmtree(resolved, ignore_errors=True)
        current = resolved.parent
        while current != repo_root and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def publish_ai_logs_to_dedicated_branch(
    *,
    repo_root: Path,
    run_dir: Path,
    config: dict[str, Any],
    context: dict[str, Any],
    repo_slug: str,
) -> dict[str, Any]:
    ai_logs_required = bool(context.get("ai_logs_required", True))
    publish_settings = resolve_ai_logs_publish_settings(
        config=config,
        ai_logs_required=ai_logs_required,
    )
    mode = str(publish_settings["mode"])
    branch_name = str(publish_settings["branch"])
    required = bool(publish_settings["required"])
    commit_message_template = str(publish_settings["commit_message_template"])

    default_state = {
        "ai_logs_publish_mode": mode,
        "ai_logs_publish_required": required,
        "ai_logs_publish_branch": branch_name,
        "ai_logs_publish_status": "skipped",
        "ai_logs_publish_commit": "",
    }
    if mode != "dedicated-branch":
        return default_state
    if str(context.get("ai_logs_status", "")).strip().lower() != "saved":
        return default_state

    ai_logs_paths_raw = context.get("ai_logs_paths", [])
    if not isinstance(ai_logs_paths_raw, list):
        raise RuntimeError("Internal error: ai_logs_paths must be a list.")
    ai_logs_paths = sorted(
        {
            normalize_repo_path(str(item))
            for item in ai_logs_paths_raw
            if str(item).strip()
        }
    )
    if not ai_logs_paths:
        message = "ai-logs ファイル一覧が空のため dedicated-branch へ保存できません。"
        if required:
            raise RuntimeError(message)
        log(f"WARNING: {message}")
        return {
            **default_state,
            "ai_logs_publish_status": "failed",
        }

    worktree_dir = Path(tempfile.mkdtemp(prefix="flowsmith-ai-logs-"))
    worktree_added = False
    published_commit = ""
    try:
        git(["fetch", "origin", branch_name], cwd=repo_root, check=False)
        git(["worktree", "add", "--detach", str(worktree_dir), "HEAD"], cwd=repo_root)
        worktree_added = True

        remote_exists = (
            git(
                ["ls-remote", "--exit-code", "--heads", "origin", branch_name],
                cwd=worktree_dir,
                check=False,
            ).returncode
            == 0
        )
        if remote_exists:
            git(["fetch", "origin", branch_name], cwd=worktree_dir, check=False)
            git(["checkout", "-B", branch_name, f"origin/{branch_name}"], cwd=worktree_dir)
        else:
            git(["checkout", "-B", branch_name], cwd=worktree_dir)

        for relative_path in ai_logs_paths:
            source = resolve_repo_relative_path(
                relative_path,
                repo_root=repo_root,
                setting_name="ai_logs.path",
            )
            if not source.exists():
                raise RuntimeError(
                    f"ai-logs 保存対象ファイルが見つかりません: {relative_path}"
                )
            destination = resolve_repo_relative_path(
                relative_path,
                repo_root=worktree_dir,
                setting_name="ai_logs.path",
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        git(["add", "--", *ai_logs_paths], cwd=worktree_dir)
        has_changes = git(["diff", "--cached", "--quiet"], cwd=worktree_dir, check=False)
        if has_changes.returncode != 0:
            publish_message = format_template(
                commit_message_template,
                context,
                "ai_logs.publish.commit_message",
            )
            git(["commit", "-m", publish_message], cwd=worktree_dir)

        push_proc = git(
            ["push", "-u", "origin", branch_name],
            cwd=worktree_dir,
            check=False,
        )
        if push_proc.returncode != 0:
            push_stderr = push_proc.stderr or ""
            if "non-fast-forward" in push_stderr:
                git(["pull", "--rebase", "origin", branch_name], cwd=worktree_dir, check=True)
                git(["push", "-u", "origin", branch_name], cwd=worktree_dir, check=True)
            else:
                raise RuntimeError(
                    "ai-logs dedicated-branch への push に失敗しました。\n"
                    f"stderr:\n{push_stderr}"
                )

        published_commit = git(["rev-parse", "HEAD"], cwd=worktree_dir).stdout.strip()
    except (RuntimeError, OSError) as err:
        if required:
            raise RuntimeError(
                f"ai-logs dedicated-branch 保存に失敗しました: {err}"
            ) from err
        message = f"ai-logs dedicated-branch 保存をスキップしました: {err}"
        log(f"WARNING: {message}")
        write_text(run_dir / "ai_logs_publish_status.md", f"- {message}\n")
        return {
            **default_state,
            "ai_logs_publish_status": "failed",
        }
    finally:
        if worktree_added:
            git(["worktree", "remove", "--force", str(worktree_dir)], cwd=repo_root, check=False)
        shutil.rmtree(worktree_dir, ignore_errors=True)

    remove_ai_log_paths_from_worktree(
        repo_root=repo_root,
        relative_paths=ai_logs_paths,
    )
    write_text(
        run_dir / "ai_logs_publish_status.md",
        (
            "- ai-logs を dedicated-branch に反映しました。\n"
            f"- branch: `{branch_name}`\n"
            f"- commit: `{published_commit}`\n"
            f"- files: `{len(ai_logs_paths)}`\n"
        ),
    )

    index_file = str(context.get("ai_logs_index_file", "")).strip()
    index_url = ""
    if repo_slug and index_file:
        index_url = f"https://github.com/{repo_slug}/blob/{branch_name}/{index_file}"

    return {
        **default_state,
        "ai_logs_publish_status": "published",
        "ai_logs_publish_commit": published_commit,
        "ai_logs_index_url": index_url,
        # コード変更用ブランチには ai-logs を含めない。
        "ai_logs_paths": [],
    }


def push_branch(repo_root: Path, branch_name: str) -> None:
    git(["push", "-u", "origin", branch_name], cwd=repo_root)


def create_or_update_pr(
    *,
    repo_root: Path,
    repo_slug: str,
    base_branch: str,
    branch_name: str,
    title: str,
    body_file: Path,
    labels: list[str],
    draft: bool,
) -> str:
    repo_args = ["--repo", repo_slug] if repo_slug else []

    existing = run_process(
        [
            "gh",
            "pr",
            "list",
            *repo_args,
            "--head",
            branch_name,
            "--state",
            "open",
            "--json",
            "number,url",
        ],
        cwd=repo_root,
        check=True,
    )
    current = json.loads(existing.stdout or "[]")

    if current:
        number = str(current[0]["number"])
        run_process(
            [
                "gh",
                "pr",
                "edit",
                number,
                *repo_args,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ],
            cwd=repo_root,
            check=True,
        )
        for label in labels:
            run_process(
                ["gh", "pr", "edit", number, *repo_args, "--add-label", label],
                cwd=repo_root,
                check=False,
            )
        pr_url = current[0]["url"]
        log(f"Updated existing PR: {pr_url}")
        return pr_url

    cmd = [
        "gh",
        "pr",
        "create",
        *repo_args,
        "--base",
        base_branch,
        "--head",
        branch_name,
        "--title",
        title,
        "--body-file",
        str(body_file),
    ]
    if draft:
        cmd.append("--draft")
    for label in labels:
        cmd.extend(["--label", label])

    created = run_process(cmd, cwd=repo_root, check=True)
    pr_url = created.stdout.strip().splitlines()[-1]
    log(f"Created PR: {pr_url}")
    return pr_url


def prepare_target_repo(
    *,
    target_repo_root: Path,
    clone_url: str,
    repo_slug: str,
    sync_target: bool,
) -> None:
    if target_repo_root.exists():
        if not (target_repo_root / ".git").exists():
            raise RuntimeError(
                f"Target path exists but is not a git repository: {target_repo_root}"
            )
        if sync_target:
            git(["fetch", "--all", "--prune"], cwd=target_repo_root, check=False)
        return

    effective_clone_url = clone_url.strip()
    if not effective_clone_url and repo_slug:
        effective_clone_url = f"https://github.com/{repo_slug}.git"

    if not effective_clone_url:
        raise RuntimeError(
            "Unable to prepare target repository: clone URL is not set. "
            "Set project.clone_url or project.repo."
        )

    target_repo_root.parent.mkdir(parents=True, exist_ok=True)
    run_process(["git", "clone", effective_clone_url, str(target_repo_root)], check=True)


def load_project_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        raise RuntimeError(f"Invalid projects manifest ({path}): 'projects' must be an object.")
    return payload


def resolve_runtime(
    *,
    control_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    base_config_path = resolve_path(args.config, base_dir=control_root)
    base_config = load_json(base_config_path)
    validate_config(base_config, base_config_path)

    target_repo_root = resolve_path(args.target_path, base_dir=control_root) if args.target_path else control_root
    project_id = ""
    repo_slug = normalize_repo_slug(args.target_repo or "")
    default_base_branch = ""
    config_base_dir = base_config_path.parent
    config_validation_path = base_config_path
    config = deepcopy(base_config)

    if args.project:
        project_id = args.project
        manifest_path = resolve_path(args.projects_file, base_dir=control_root)
        manifest = load_project_manifest(manifest_path)

        if project_id not in manifest["projects"]:
            raise RuntimeError(
                f"Project '{project_id}' not found in {manifest_path}."
            )

        project = manifest["projects"][project_id]
        if not isinstance(project, dict):
            raise RuntimeError(
                f"Project '{project_id}' in {manifest_path} must be an object."
            )

        workspace_root_value = project.get("workspace_root") or manifest.get(
            "workspace_root", ".agent/workspaces"
        )
        workspace_root = resolve_path(workspace_root_value, base_dir=manifest_path.parent)

        if not args.target_path:
            local_path_value = project.get("local_path")
            if local_path_value:
                target_repo_root = resolve_path(local_path_value, base_dir=manifest_path.parent)
            else:
                target_repo_root = (workspace_root / slugify(project_id, max_len=80)).resolve()

        repo_slug = normalize_repo_slug(args.target_repo or project.get("repo", ""))
        clone_url = str(project.get("clone_url", "")).strip()

        prepare_target_repo(
            target_repo_root=target_repo_root,
            clone_url=clone_url,
            repo_slug=repo_slug,
            sync_target=not args.no_sync,
        )

        if not repo_slug:
            repo_slug = detect_repo_slug(target_repo_root)

        project_config_value = project.get("config")
        if project_config_value:
            project_config_path = resolve_path(project_config_value, base_dir=manifest_path.parent)
            project_config = load_json(project_config_path)
            config = merge_dict(config, project_config)
            config_base_dir = project_config_path.parent
            config_validation_path = project_config_path

        inline_overrides = project.get("overrides")
        if isinstance(inline_overrides, dict):
            config = merge_dict(config, inline_overrides)

        default_base_branch = str(project.get("base_branch", "")).strip()
    else:
        # For non-project mode with --target-repo, use a managed workspace clone by default.
        if repo_slug:
            if not args.target_path:
                target_repo_root = (
                    control_root / ".agent" / "workspaces" / slugify(repo_slug, max_len=80)
                ).resolve()
            prepare_target_repo(
                target_repo_root=target_repo_root,
                clone_url="",
                repo_slug=repo_slug,
                sync_target=not args.no_sync,
            )
        else:
            if not (target_repo_root / ".git").exists():
                raise RuntimeError(
                    f"Target repository path is not a git repository: {target_repo_root}"
                )
            repo_slug = detect_repo_slug(target_repo_root)

        target_defaults_raw = config.get("target_repo_defaults")
        if target_defaults_raw is None:
            target_defaults_raw = {}
        if not isinstance(target_defaults_raw, dict):
            raise RuntimeError("Config 'target_repo_defaults' must be an object when specified.")

        # Apply centralized defaults when targeting an external repository without --project.
        if target_defaults_raw and (
            bool(args.target_repo) or bool(args.target_path and target_repo_root != control_root)
        ):
            config = merge_dict(config, target_defaults_raw)

    validate_config(config, config_validation_path)

    run_namespace = slugify(project_id or repo_slug or target_repo_root.name, max_len=80)
    return {
        "config": config,
        "config_base_dir": config_base_dir,
        "target_repo_root": target_repo_root,
        "project_id": project_id,
        "repo_slug": repo_slug,
        "default_base_branch": default_base_branch,
        "run_namespace": run_namespace,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run autonomous issue-to-PR pipeline.")
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--issue-file", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--project", default=None, help="Project id in projects manifest")
    parser.add_argument("--projects-file", type=Path, default=DEFAULT_PROJECTS_PATH)
    parser.add_argument("--target-repo", default=None, help="GitHub repo slug (owner/repo)")
    parser.add_argument("--target-path", type=Path, default=None, help="Local target repository path")
    parser.add_argument("--no-sync", action="store_true", help="Skip fetch/pull before branch work")
    parser.add_argument("--base-branch", default=None)
    parser.add_argument("--branch-name", default=None)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--create-pr", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    control_root = Path.cwd().resolve()

    runtime = resolve_runtime(control_root=control_root, args=args)
    config = runtime["config"]
    config_base_dir = runtime["config_base_dir"]
    target_repo_root: Path = runtime["target_repo_root"]
    project_id = runtime["project_id"]
    repo_slug = runtime["repo_slug"]

    require_clean_worktree(target_repo_root)

    issue = (
        load_issue_from_file(args.issue_file, args.issue_number)
        if args.issue_file
        else load_issue_from_gh(args.issue_number, repo_slug=repo_slug, cwd=target_repo_root)
    )

    config_base_branch = str(config.get("base_branch", "main"))
    base_branch = args.base_branch or runtime["default_base_branch"] or config_base_branch

    branch_prefix = f"{slugify(project_id)}-" if project_id else ""
    branch_name = args.branch_name or (
        f"agent/{branch_prefix}issue-{issue['number']}-{slugify(issue['title'])}"
    )
    max_attempts = int(config.get("max_attempts", 3))
    quality_gates = config.get("quality_gates", [])
    quality_gate_list = "\n".join(f"- `{item}`" for item in quality_gates) or "- (none)"

    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = control_root / ".agent" / "runs" / runtime["run_namespace"] / f"{timestamp}-issue-{issue['number']}"
    run_dir.mkdir(parents=True, exist_ok=False)

    task_file = run_dir / "task.md"
    plan_file = run_dir / "plan.md"
    review_file = run_dir / "review.md"
    pr_body_file = run_dir / "pr_body.md"

    context: dict[str, Any] = {
        "issue_number": issue["number"],
        "issue_title": issue["title"],
        "issue_body": issue["body"],
        "issue_url": issue["url"],
        "run_timestamp": timestamp,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "repo_root": str(target_repo_root),
        "control_root": str(control_root),
        "run_dir": str(run_dir),
        "project_id": project_id,
        "target_repo": repo_slug,
        "task_file": str(task_file),
        "plan_file": str(plan_file),
        "review_file": str(review_file),
        "output_file": "",
        "quality_gate_list": quality_gate_list,
        "max_attempts": max_attempts,
        "attempt": 1,
        "feedback": "None",
        "instruction_markdown": "",
        "validation_commands_markdown": "",
        "log_location_markdown": "",
        "codex_commit_summary_markdown": "_Codex判断ログは未生成です。_",
        "plan_markdown": "",
        "validation_summary": "",
        "review_markdown": "_Reviewer step skipped._",
        "entire_status": "disabled",
        "entire_trailer_key": "Entire-Checkpoint",
        "entire_checkpoint": "未検出",
        "entire_trace_status": "skipped",
        "entire_trace_file": "未登録",
        "entire_trace_sha256": "",
        "entire_trace_attempts": 0,
        "entire_trace_verify_status": "skipped",
        "entire_explain_status": "skipped",
        "entire_explain_log": "",
        "ai_logs_required": True,
        "ai_logs_status": "skipped",
        "ai_logs_dir": "未保存",
        "ai_logs_index_file": "未保存",
        "ai_logs_index_url": "",
        "ai_logs_publish_mode": "same-branch",
        "ai_logs_publish_required": True,
        "ai_logs_publish_branch": "",
        "ai_logs_publish_status": "skipped",
        "ai_logs_publish_commit": "",
        "ai_logs_file_count": 0,
        "ai_logs_paths": [],
        "codex_commit_summary_required": True,
        "codex_commit_summary_status": "skipped",
        "codex_commit_summary_appendix": "",
        "head_commit": "",
    }

    context["instruction_markdown"] = render_issue_instruction_markdown(
        issue_number=issue["number"],
        issue_title=issue["title"],
        issue_url=issue["url"],
        issue_body=issue["body"],
    )
    context["validation_commands_markdown"] = render_validation_commands_markdown(quality_gates)
    context["log_location_markdown"] = render_log_location_markdown(context)

    write_text(
        task_file,
        (
            f"# Issue #{issue['number']}: {issue['title']}\n\n"
            f"Project: {project_id or '(default)'}\n"
            f"Target repo: {repo_slug or '(inferred from local git)'}\n"
            f"Target path: {target_repo_root}\n"
            f"URL: {issue['url'] or '(local file)'}\n\n"
            f"## Body\n\n{issue['body']}\n"
        ),
    )

    ensure_branch(
        target_repo_root,
        base_branch,
        branch_name,
        sync_base=not args.no_sync,
    )

    entire_state = setup_entire_trace(
        repo_root=target_repo_root,
        run_dir=run_dir,
        config=config,
    )
    context.update(entire_state)

    commands = config["commands"]
    planner_cmd = resolve_command(commands.get("planner", ""), required=True)
    coder_cmd = resolve_command(commands.get("coder", ""), required=True)
    reviewer_cmd = resolve_command(commands.get("reviewer", ""), required=False)

    templates = config["templates"]
    planner_template = resolve_path(templates["planner"], base_dir=config_base_dir)
    coder_template = resolve_path(templates["coder"], base_dir=config_base_dir)
    reviewer_template = resolve_path(templates["reviewer"], base_dir=config_base_dir)
    pr_template = resolve_path(templates["pr_body"], base_dir=config_base_dir)

    planner_prompt = run_dir / "planner_prompt.md"
    planner_output = run_dir / "planner_output.md"
    context["output_file"] = str(planner_output)
    write_text(planner_prompt, render_template_file(planner_template, context))

    run_agent_command(
        step_name="planner",
        command_template=planner_cmd,
        context={
            **context,
            "prompt_file": str(planner_prompt),
            "output_file": str(planner_output),
        },
        repo_root=target_repo_root,
        prompt_file=planner_prompt,
        output_file=planner_output,
        log_file=run_dir / "planner_command.log",
        required_output=True,
    )
    write_text(plan_file, read_text(planner_output))
    context["plan_markdown"] = read_text(plan_file)

    last_validation = ""
    feedback = "None"
    success = False

    for attempt in range(1, max_attempts + 1):
        context["attempt"] = attempt
        context["feedback"] = feedback
        coder_prompt = run_dir / f"coder_prompt_attempt_{attempt}.md"
        coder_output = run_dir / f"coder_output_attempt_{attempt}.md"
        context["output_file"] = str(coder_output)

        write_text(coder_prompt, render_template_file(coder_template, context))
        run_agent_command(
            step_name=f"coder-attempt-{attempt}",
            command_template=coder_cmd,
            context={
                **context,
                "prompt_file": str(coder_prompt),
                "output_file": str(coder_output),
            },
            repo_root=target_repo_root,
            prompt_file=coder_prompt,
            output_file=coder_output,
            log_file=run_dir / f"coder_command_attempt_{attempt}.log",
            required_output=False,
        )

        passed, summary = run_quality_gates(
            gates=quality_gates,
            repo_root=target_repo_root,
            run_dir=run_dir,
            attempt=attempt,
        )
        write_text(run_dir / f"validation_attempt_{attempt}.md", summary + "\n")
        last_validation = summary
        if passed:
            success = True
            break

        feedback = (
            "Quality gates failed on previous attempt.\n\n"
            f"{summary}\n\n"
            "Fix the failing points and retry."
        )

    if not success:
        raise RuntimeError(
            f"All coder attempts failed quality gates. See {run_dir} for logs."
        )

    context["validation_summary"] = last_validation or "- No validation summary available."

    if reviewer_cmd:
        reviewer_prompt = run_dir / "reviewer_prompt.md"
        context["output_file"] = str(review_file)
        write_text(reviewer_prompt, render_template_file(reviewer_template, context))
        run_agent_command(
            step_name="reviewer",
            command_template=reviewer_cmd,
            context={
                **context,
                "prompt_file": str(reviewer_prompt),
                "output_file": str(review_file),
            },
            repo_root=target_repo_root,
            prompt_file=reviewer_prompt,
            output_file=review_file,
            log_file=run_dir / "reviewer_command.log",
            required_output=False,
        )
        if review_file.exists() and read_text(review_file).strip():
            context["review_markdown"] = read_text(review_file)
    else:
        write_text(review_file, "_Reviewer command is not configured._\n")

    codex_summary_state = build_codex_commit_summary(
        run_dir=run_dir,
        context=context,
        config=config,
    )
    context.update(codex_summary_state)

    explicit_registration_state = prepare_entire_explicit_registration(
        repo_root=target_repo_root,
        run_dir=run_dir,
        context=context,
    )
    context.update(explicit_registration_state)
    ai_logs_state = save_ai_logs_bundle(
        repo_root=target_repo_root,
        run_dir=run_dir,
        config=config,
        context=context,
    )
    context.update(ai_logs_state)
    ai_logs_publish_state = publish_ai_logs_to_dedicated_branch(
        repo_root=target_repo_root,
        run_dir=run_dir,
        config=config,
        context=context,
        repo_slug=repo_slug,
    )
    context.update(ai_logs_publish_state)
    context["log_location_markdown"] = render_log_location_markdown(context)

    commit_message = format_template(
        config.get("commit_message", "feat(agent): resolve issue #{issue_number}"),
        context,
        "commit_message",
    )
    commit_appendix_parts: list[str] = []
    codex_commit_appendix = str(context.get("codex_commit_summary_appendix", "")).strip()
    if codex_commit_appendix:
        commit_appendix_parts.append(codex_commit_appendix)
    entire_trace_appendix = str(context.get("entire_trace_commit_appendix", "")).strip()
    if entire_trace_appendix:
        commit_appendix_parts.append(entire_trace_appendix)
    commit_message = build_commit_message(
        commit_message,
        "\n\n".join(commit_appendix_parts).strip(),
    )
    ignored_paths: list[str] = []
    force_add_paths: list[str] = []
    required_paths: list[str] = []
    if context.get("entire_trace_status") == "registered":
        trace_path_value = str(context.get("entire_trace_file", "")).strip()
        if trace_path_value:
            ignored_paths.append(trace_path_value)
            force_add_paths.append(trace_path_value)
            required_paths.append(trace_path_value)
    ai_log_paths = context.get("ai_logs_paths", [])
    if isinstance(ai_log_paths, list):
        for path_value in ai_log_paths:
            text = str(path_value).strip()
            if not text:
                continue
            ignored_paths.append(text)
            force_add_paths.append(text)
            if bool(context.get("ai_logs_required", True)):
                required_paths.append(text)
    commit_changes(
        target_repo_root,
        commit_message,
        ignore_paths=ignored_paths,
        force_add_paths=force_add_paths,
        required_paths=required_paths,
    )
    head_commit = get_head_commit_sha(target_repo_root)
    context["head_commit"] = head_commit
    if context.get("ai_logs_status") == "saved" and repo_slug:
        ai_logs_index_file = str(context.get("ai_logs_index_file", "")).strip()
        if ai_logs_index_file:
            ai_logs_publish_mode = str(context.get("ai_logs_publish_mode", "same-branch")).strip()
            ai_logs_publish_branch = str(context.get("ai_logs_publish_branch", "")).strip()
            if ai_logs_publish_mode == "dedicated-branch" and ai_logs_publish_branch:
                context["ai_logs_index_url"] = (
                    f"https://github.com/{repo_slug}/blob/{ai_logs_publish_branch}/{ai_logs_index_file}"
                )
            else:
                context["ai_logs_index_url"] = (
                    f"https://github.com/{repo_slug}/blob/{head_commit}/{ai_logs_index_file}"
                )
    context["log_location_markdown"] = render_log_location_markdown(context)

    entire_checkpoint = ""
    if context.get("entire_status") == "enabled" and context.get("entire_verify_trailer"):
        commit_body = get_head_commit_message(target_repo_root)
        trailer_key = str(context.get("entire_trailer_key", "Entire-Checkpoint"))
        entire_checkpoint = extract_commit_trailer(commit_body, trailer_key)
        if not entire_checkpoint:
            message = (
                "コミットメッセージに Entire 証跡トレーラーが見つかりません。"
                f" trailer_key={trailer_key}"
            )
            if bool(context.get("entire_required")):
                raise RuntimeError(message)
            log(f"WARNING: {message}")

    explicit_verify_state = verify_entire_explicit_registration(
        repo_root=target_repo_root,
        run_dir=run_dir,
        context=context,
    )
    context.update(explicit_verify_state)

    explain_state = generate_entire_explain(
        repo_root=target_repo_root,
        run_dir=run_dir,
        context=context,
    )
    context.update(explain_state)

    context["entire_checkpoint"] = entire_checkpoint or "未検出"
    write_text(
        run_dir / "entire_trace.md",
        (
            "# Entire 証跡\n\n"
            f"- status: `{context.get('entire_status')}`\n"
            f"- trailer_key: `{context.get('entire_trailer_key')}`\n"
            f"- checkpoint: `{context.get('entire_checkpoint')}`\n"
            f"- commit: `{head_commit}`\n"
            f"- trace_status: `{context.get('entire_trace_status')}`\n"
            f"- trace_file: `{context.get('entire_trace_file')}`\n"
            f"- trace_sha256: `{context.get('entire_trace_sha256')}`\n"
            f"- trace_verify_status: `{context.get('entire_trace_verify_status')}`\n"
            f"- explain_status: `{context.get('entire_explain_status')}`\n"
            f"- explain_log: `{context.get('entire_explain_log')}`\n"
        ),
    )
    log(f"Committed changes on {branch_name}")

    if args.push:
        push_branch(target_repo_root, branch_name)
        log("Pushed branch to origin")

    if args.create_pr:
        if not args.push:
            raise RuntimeError("--create-pr requires --push.")

        pr_conf = config.get("pr", {})
        pr_title = format_template(
            pr_conf.get("title", "[agent] {issue_title}"),
            context,
            "pr.title",
        )
        pr_labels = pr_conf.get("labels", [])
        pr_draft = bool(pr_conf.get("draft", True))

        validate_required_pr_context(context)
        write_text(pr_body_file, render_template_file(pr_template, context))
        pr_url = create_or_update_pr(
            repo_root=target_repo_root,
            repo_slug=repo_slug,
            base_branch=base_branch,
            branch_name=branch_name,
            title=pr_title,
            body_file=pr_body_file,
            labels=pr_labels,
            draft=pr_draft,
        )
        write_text(run_dir / "pr_url.txt", pr_url + "\n")

    write_text(
        run_dir / "summary.md",
        (
            f"# Agent Pipeline Summary\n\n"
            f"- Project: `{project_id or 'default'}`\n"
            f"- Target repo: `{repo_slug or '(inferred local git)'}`\n"
            f"- Target path: `{target_repo_root}`\n"
            f"- Issue: `#{issue['number']}`\n"
            f"- Branch: `{branch_name}`\n"
            f"- Commit: `{context['head_commit']}`\n"
            f"- Entire checkpoint: `{context['entire_checkpoint']}`\n"
            f"- Entire trace file: `{context['entire_trace_file']}`\n"
            f"- Entire trace sha256: `{context['entire_trace_sha256']}`\n"
            f"- Entire trace verify: `{context['entire_trace_verify_status']}`\n"
            f"- Entire explain: `{context['entire_explain_status']}`\n"
            f"- AI logs status: `{context['ai_logs_status']}`\n"
            f"- AI logs publish mode: `{context['ai_logs_publish_mode']}`\n"
            f"- AI logs publish branch: `{context['ai_logs_publish_branch']}`\n"
            f"- AI logs publish status: `{context['ai_logs_publish_status']}`\n"
            f"- AI logs publish commit: `{context['ai_logs_publish_commit']}`\n"
            f"- AI logs index: `{context['ai_logs_index_file']}`\n"
            f"- AI logs files: `{context['ai_logs_file_count']}`\n"
            f"- Codex commit summary: `{context['codex_commit_summary_status']}`\n"
            f"- Validation:\n{context['validation_summary']}\n"
        ),
    )
    log(f"Completed successfully. Logs: {run_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as err:
        print(f"[agent-pipeline] ERROR: {err}", file=sys.stderr)
        raise SystemExit(1)
