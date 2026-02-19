#!/usr/bin/env python3
"""Autonomous issue-to-PR pipeline with multi-project support."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from agent_pipeline_core import (
        clip_inline_text,
        clip_text,
        detect_repo_slug,
        format_template,
        git,
        load_json,
        merge_dict,
        normalize_inline_text,
        normalize_repo_path,
        normalize_repo_slug,
        parse_positive_int,
        parse_string_list,
        read_text,
        require_clean_worktree,
        resolve_path,
        resolve_repo_relative_path,
        run_logged_process,
        run_process,
        run_shell,
        sha256_text,
        slugify,
        validate_config,
        write_text,
    )
except ModuleNotFoundError:
    from scripts.agent_pipeline_core import (
        clip_inline_text,
        clip_text,
        detect_repo_slug,
        format_template,
        git,
        load_json,
        merge_dict,
        normalize_inline_text,
        normalize_repo_path,
        normalize_repo_slug,
        parse_positive_int,
        parse_string_list,
        read_text,
        require_clean_worktree,
        resolve_path,
        resolve_repo_relative_path,
        run_logged_process,
        run_process,
        run_shell,
        sha256_text,
        slugify,
        validate_config,
        write_text,
    )

try:
    from agent_pipeline_issue import PipelineIssueService
except ModuleNotFoundError:
    from scripts.agent_pipeline_issue import PipelineIssueService

try:
    from agent_pipeline_summary import PipelineCommitSummaryService
except ModuleNotFoundError:
    from scripts.agent_pipeline_summary import PipelineCommitSummaryService

try:
    from agent_pipeline_runtime import PipelineRuntimeService
except ModuleNotFoundError:
    from scripts.agent_pipeline_runtime import PipelineRuntimeService

try:
    from agent_pipeline_logs import PipelineAiLogsService
except ModuleNotFoundError:
    from scripts.agent_pipeline_logs import PipelineAiLogsService

try:
    from agent_pipeline_ui import PipelineUiEvidenceService
except ModuleNotFoundError:
    from scripts.agent_pipeline_ui import PipelineUiEvidenceService

try:
    from agent_pipeline_execution import run_pipeline
except ModuleNotFoundError:
    from scripts.agent_pipeline_execution import run_pipeline

try:
    from agent_pipeline_entire import PipelineEntireService
except ModuleNotFoundError:
    from scripts.agent_pipeline_entire import PipelineEntireService

try:
    from agent_pipeline_pr import PipelinePullRequestService
except ModuleNotFoundError:
    from scripts.agent_pipeline_pr import PipelinePullRequestService


DEFAULT_CONFIG_PATH = Path(".agent/pipeline.json")
DEFAULT_PROJECTS_PATH = Path(".agent/projects.json")


def log(message: str) -> None:
    print(f"[agent-pipeline] {message}")


def is_coder_output_filename(name: str) -> bool:
    return bool(re.fullmatch(r"coder_output_attempt_[0-9]+\.md", name))


def recover_coder_output_file(
    *,
    repo_root: Path,
    output_file: Path,
) -> None:
    if not is_coder_output_filename(output_file.name):
        return
    root_fallback = repo_root / output_file.name
    if not root_fallback.is_file():
        return

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if not output_file.exists():
        shutil.move(str(root_fallback), str(output_file))
        log(
            "Recovered misplaced coder output file from repository root: "
            f"{root_fallback} -> {output_file}"
        )
        return

    # run_dir 側が既にある場合は repository root 側を削除して混入を防ぐ。
    root_fallback.unlink(missing_ok=True)
    log(
        "Removed duplicate misplaced coder output file at repository root: "
        f"{root_fallback}"
    )


def cleanup_untracked_coder_outputs(repo_root: Path) -> list[str]:
    removed: list[str] = []
    for file_path in sorted(repo_root.glob("coder_output_attempt_*.md")):
        if not file_path.is_file() or not is_coder_output_filename(file_path.name):
            continue
        relative = normalize_repo_path(file_path.relative_to(repo_root).as_posix())
        tracked = (
            git(
                ["ls-files", "--error-unmatch", "--", relative],
                cwd=repo_root,
                check=False,
            ).returncode
            == 0
        )
        if tracked:
            continue
        file_path.unlink(missing_ok=True)
        removed.append(relative)
    return removed


CONVENTIONAL_PR_TYPES = (
    "feat",
    "fix",
    "docs",
    "chore",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "revert",
)


def strip_issue_title_prefixes(title: str) -> str:
    cleaned = normalize_inline_text(title)
    if not cleaned:
        return ""

    while True:
        updated = re.sub(
            r"^(?:\[[^\]]+\]|【[^】]+】|\([^)]+\))\s*",
            "",
            cleaned,
        ).strip()
        if updated == cleaned:
            break
        cleaned = updated

    cleaned = re.sub(
        r"^(?:エージェント作業|agent task|agent)\s*[:：\-]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


def has_conventional_pr_prefix(title: str) -> bool:
    pattern = (
        r"^(?:"
        + "|".join(CONVENTIONAL_PR_TYPES)
        + r")(?:\([^)]+\))?:\s+\S"
    )
    return bool(re.match(pattern, title, flags=re.IGNORECASE))


def infer_pr_type_from_issue(*, issue_title: str, issue_labels: list[str]) -> str:
    corpus_parts = [issue_title]
    corpus_parts.extend(issue_labels)
    corpus = " ".join(str(item) for item in corpus_parts if str(item).strip()).lower()

    if any(token in corpus for token in ("bug", "fix", "hotfix", "不具合", "バグ", "障害")):
        return "fix"
    if any(token in corpus for token in ("doc", "docs", "documentation", "ドキュメント")):
        return "docs"
    if any(token in corpus for token in ("refactor", "リファクタ")):
        return "refactor"
    if any(token in corpus for token in ("test", "テスト")):
        return "test"
    if any(token in corpus for token in ("perf", "performance", "最適化")):
        return "perf"
    if any(token in corpus for token in ("ci", "build", "infra", "devops")):
        return "build"
    if any(token in corpus for token in ("chore", "maintain", "maintenance", "運用")):
        return "chore"
    return "feat"


def build_default_pr_title(*, issue_title: str, issue_labels: list[str]) -> str:
    normalized = strip_issue_title_prefixes(issue_title)
    if not normalized:
        normalized = normalize_inline_text(issue_title) or "update"
    if has_conventional_pr_prefix(normalized):
        return normalized
    pr_type = infer_pr_type_from_issue(
        issue_title=normalized,
        issue_labels=issue_labels,
    )
    return f"{pr_type}: {normalized}"


def extract_conventional_pr_type(title: str) -> str:
    pattern = (
        r"^(?P<type>"
        + "|".join(CONVENTIONAL_PR_TYPES)
        + r")(?:\([^)]+\))?:\s+\S"
    )
    match = re.match(pattern, title, flags=re.IGNORECASE)
    if not match:
        return ""
    return str(match.group("type")).lower()


def build_pr_change_type_checklist_markdown(
    *,
    issue_title: str,
    issue_labels: list[str],
    pr_title: str,
    committed_paths: list[str],
) -> str:
    primary_type = extract_conventional_pr_type(pr_title) or infer_pr_type_from_issue(
        issue_title=issue_title,
        issue_labels=issue_labels,
    )

    lowered_paths = [normalize_repo_path(path).lower() for path in committed_paths]
    docs_changed = any(path == "readme.md" or path.startswith("docs/") for path in lowered_paths)
    ci_changed = any(path.startswith(".github/") for path in lowered_paths)
    test_changed = any(
        (
            "/tests/" in f"/{path}"
            or "/test/" in f"/{path}"
            or re.search(r"(?:^|/).*(?:_test\.|\.spec\.|\.test\.)", path)
        )
        for path in lowered_paths
    )

    bug_fix = primary_type == "fix"
    feature = primary_type == "feat"
    docs = primary_type == "docs" or docs_changed
    refactor = primary_type in {"refactor", "perf", "revert"}
    ci_infra = primary_type in {"ci", "build", "chore"} or ci_changed
    tests = primary_type == "test" or test_changed

    def mark(value: bool) -> str:
        return "x" if value else " "

    return "\n".join(
        [
            f"- [{mark(bug_fix)}] バグ修正",
            f"- [{mark(feature)}] 新機能",
            f"- [{mark(docs)}] ドキュメント更新",
            f"- [{mark(refactor)}] リファクタリング",
            f"- [{mark(ci_infra)}] CI/CD・インフラ",
            f"- [{mark(tests)}] テスト追加・修正",
            f"- 判定種別: `{primary_type}`",
        ]
    )


def is_validation_summary_passed(summary: str) -> bool:
    normalized = str(summary or "").strip()
    if not normalized:
        return False
    upper = normalized.upper()
    if "FAIL" in upper:
        return False
    return "PASS" in upper or "NO QUALITY GATES" in upper


def build_pr_auto_checklist_markdown(context: dict[str, Any]) -> str:
    quality_passed = is_validation_summary_passed(str(context.get("validation_summary", "")))
    ui_status = str(context.get("ui_evidence_status", "unknown")).strip() or "unknown"
    ui_count = len(context.get("ui_evidence_image_files", []))
    ai_logs_status = str(context.get("ai_logs_status", "unknown")).strip() or "unknown"
    ai_logs_url = str(context.get("ai_logs_index_url", "")).strip()

    if ui_status == "attached":
        ui_checked = True
        ui_note = f"UI証跡あり（{ui_count}件）"
    elif ui_status in {"not-required", "skipped"}:
        ui_checked = True
        ui_note = "UI変更なし / 証跡不要"
    else:
        ui_checked = False
        ui_note = f"UI証跡状態: {ui_status}"

    ai_logs_checked = ai_logs_status == "saved" and bool(ai_logs_url)
    ai_logs_note = ai_logs_url or "AIログリンク未確定"

    def mark(value: bool) -> str:
        return "x" if value else " "

    return "\n".join(
        [
            f"- [{mark(quality_passed)}] 品質ゲート結果を確認済み",
            f"- [{mark(ui_checked)}] UI証跡状態を確認済み（{ui_note}）",
            f"- [{mark(ai_logs_checked)}] AIログリンクを確認可能（{ai_logs_note}）",
        ]
    )


def build_pr_manual_checklist_markdown() -> str:
    return "\n".join(
        [
            "- [ ] 受け入れ条件と実装差分が一致していることを確認した",
            "- [ ] 破壊的変更・移行手順の有無を確認した",
            "- [ ] 人間レビューを実施した",
        ]
    )


_SUMMARY_SERVICE: PipelineCommitSummaryService | None = None


def summary_service() -> PipelineCommitSummaryService:
    global _SUMMARY_SERVICE
    if _SUMMARY_SERVICE is None:
        _SUMMARY_SERVICE = PipelineCommitSummaryService(
            normalize_inline_text=normalize_inline_text,
            clip_inline_text=lambda value, *, max_chars: clip_inline_text(value, max_chars=max_chars),
            clip_text=lambda value, *, max_chars: clip_text(value, max_chars=max_chars),
            parse_positive_int=lambda value, *, default, name: parse_positive_int(
                value,
                default=default,
                name=name,
            ),
            format_template=lambda template, context, template_name: format_template(
                template,
                context,
                template_name,
            ),
            normalize_repo_path=normalize_repo_path,
            extract_attempt_index=extract_attempt_index,
            read_text=read_text,
            write_text=write_text,
            log=log,
        )
    return _SUMMARY_SERVICE


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


def render_related_issue_markdown(
    *,
    issue_number: int,
    issue_url: str,
    issue_state: str,
) -> str:
    normalized_state = str(issue_state or "").strip().lower()
    relation_line = f"- Closes #{issue_number}"
    if normalized_state and normalized_state != "open":
        relation_line = f"- Related to #{issue_number} (already {normalized_state})"
    url_value = issue_url.strip() if issue_url and issue_url.strip() else "(local file)"
    return f"{relation_line}\n- 元Issue URL: {url_value}"


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
    ui_evidence_status = str(context.get("ui_evidence_status", "unknown")).strip() or "unknown"
    ui_evidence_delivery_mode = str(context.get("ui_evidence_delivery_mode", "")).strip() or "commit"
    ui_evidence_artifact_dir = str(context.get("ui_evidence_artifact_dir", "")).strip()
    ui_evidence_artifact_name = str(context.get("ui_evidence_artifact_name", "")).strip()
    ui_evidence_artifact_url = str(context.get("ui_evidence_artifact_url", "")).strip()
    ui_evidence_file_count = len(context.get("ui_evidence_image_files", []))
    ui_evidence_restored_paths = context.get("ui_evidence_restored_paths", [])
    ui_evidence_ai_logs_branch = str(context.get("ui_evidence_ai_logs_branch", "")).strip()
    ui_evidence_ai_logs_urls_raw = context.get("ui_evidence_ai_logs_urls", [])
    ui_evidence_ai_logs_urls = [
        str(item).strip()
        for item in (ui_evidence_ai_logs_urls_raw if isinstance(ui_evidence_ai_logs_urls_raw, list) else [])
        if str(item).strip()
    ]
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
    lines.append(f"- UI証跡状態: `{ui_evidence_status}`")
    lines.append(f"- UI証跡モード: `{ui_evidence_delivery_mode}`")
    if ui_evidence_artifact_dir:
        lines.append(f"- UI証跡ディレクトリ(artifact): `{ui_evidence_artifact_dir}`")
    lines.append(f"- UI証跡ファイル数: `{ui_evidence_file_count}`")
    if ui_evidence_artifact_name:
        lines.append(f"- UI証跡artifact名: `{ui_evidence_artifact_name}`")
    if ui_evidence_artifact_url:
        lines.append(f"- UI証跡artifactリンク: {ui_evidence_artifact_url}")
    if ui_evidence_ai_logs_branch:
        lines.append(f"- UI証跡保存ブランチ(ai-logs): `{ui_evidence_ai_logs_branch}`")
    if ui_evidence_ai_logs_urls:
        lines.append(
            "- UI証跡リンク(ai-logs): "
            + ", ".join(ui_evidence_ai_logs_urls[:4])
        )
    if isinstance(ui_evidence_restored_paths, list) and ui_evidence_restored_paths:
        lines.append(
            "- UI証跡のためコミットから除外した画像: "
            + ", ".join(f"`{normalize_repo_path(str(item))}`" for item in ui_evidence_restored_paths[:8])
        )
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


_ISSUE_SERVICE: PipelineIssueService | None = None


def issue_service() -> PipelineIssueService:
    global _ISSUE_SERVICE
    if _ISSUE_SERVICE is None:
        _ISSUE_SERVICE = PipelineIssueService(
            run_process=run_process,
            read_text=read_text,
            write_text=write_text,
            resolve_path=lambda value, *, base_dir: resolve_path(value, base_dir=base_dir),
            normalize_inline_text=normalize_inline_text,
            clip_inline_text=lambda value, *, max_chars: clip_inline_text(value, max_chars=max_chars),
            clip_text=lambda value, *, max_chars: clip_text(value, max_chars=max_chars),
        )
    return _ISSUE_SERVICE


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

    recover_coder_output_file(
        repo_root=repo_root,
        output_file=output_file,
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
    git(["fetch", "origin", branch_name], cwd=repo_root, check=False)

    git(["checkout", base_branch], cwd=repo_root)

    if sync_base:
        git(["pull", "--ff-only", "origin", base_branch], cwd=repo_root, check=False)

    remote_branch_exists = (
        git(
            ["ls-remote", "--exit-code", "--heads", "origin", branch_name],
            cwd=repo_root,
            check=False,
        ).returncode
        == 0
    )
    if remote_branch_exists:
        git(["checkout", "-B", branch_name, f"origin/{branch_name}"], cwd=repo_root)
        return

    git(["checkout", "-B", branch_name, base_branch], cwd=repo_root)


_ENTIRE_SERVICE: PipelineEntireService | None = None


def entire_service() -> PipelineEntireService:
    global _ENTIRE_SERVICE
    if _ENTIRE_SERVICE is None:
        _ENTIRE_SERVICE = PipelineEntireService(
            parse_positive_int=lambda value, *, default, name: parse_positive_int(
                value,
                default=default,
                name=name,
            ),
            format_template=lambda template, context, template_name: format_template(
                template,
                context,
                template_name,
            ),
            resolve_repo_relative_path=resolve_repo_relative_path,
            resolve_command=lambda raw, *, required: resolve_command(raw, required=required),
            split_command=lambda value, *, name: split_command(value, name=name),
            run_logged_process=lambda args, *, cwd, log_file, check, error_message: run_logged_process(
                args,
                cwd=cwd,
                log_file=log_file,
                check=check,
                error_message=error_message,
            ),
            read_text=read_text,
            write_text=write_text,
            sha256_text=sha256_text,
            clip_text=lambda content, *, max_chars: clip_text(content, max_chars=max_chars),
            git=git,
            log=log,
        )
    return _ENTIRE_SERVICE


def setup_entire_trace(
    *,
    repo_root: Path,
    run_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    return entire_service().setup_entire_trace(
        repo_root=repo_root,
        run_dir=run_dir,
        config=config,
    )
_UI_SERVICE: PipelineUiEvidenceService | None = None


def ui_service() -> PipelineUiEvidenceService:
    global _UI_SERVICE
    if _UI_SERVICE is None:
        _UI_SERVICE = PipelineUiEvidenceService(
            normalize_repo_path=normalize_repo_path,
            parse_string_list=parse_string_list,
            parse_positive_int=parse_positive_int,
            resolve_repo_relative_path=resolve_repo_relative_path,
            normalize_repo_slug=normalize_repo_slug,
            slugify=slugify,
            git=git,
            log=log,
        )
    return _UI_SERVICE


def commit_changes(
    repo_root: Path,
    message: str,
    *,
    run_dir: Path | None = None,
    config: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    ignore_paths: list[str] | None = None,
    force_add_paths: list[str] | None = None,
    required_paths: list[str] | None = None,
) -> dict[str, Any]:
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

    ui_evidence_state = {
        "ui_evidence_enabled": False,
        "ui_evidence_required": False,
        "ui_evidence_status": "skipped",
        "ui_evidence_delivery_mode": "commit",
        "ui_evidence_artifact_dir": "",
        "ui_evidence_artifact_name": "",
        "ui_evidence_artifact_url": "",
        "ui_evidence_workflow_run_url": "",
        "ui_evidence_ui_files": [],
        "ui_evidence_image_files": [],
        "ui_evidence_commit_image_files": [],
        "ui_evidence_restored_paths": [],
        "ui_evidence_appendix": "",
    }
    final_message = message
    if config is not None:
        if run_dir is None:
            raise RuntimeError("Internal error: run_dir is required for UI evidence processing.")
        ui_evidence_state = ui_service().build_ui_evidence_state(
            repo_root=repo_root,
            run_dir=run_dir,
            changed_paths=meaningful_changes,
            config=config,
            context=context,
        )

        staged_names = git(["diff", "--cached", "--name-only"], cwd=repo_root)
        staged_paths = [line.strip() for line in staged_names.stdout.splitlines() if line.strip()]
        if not staged_paths:
            raise RuntimeError("No file changes were created by the coder agent.")
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
        missing_required = sorted(path for path in required_set if path not in staged_paths)
        if missing_required:
            joined = ", ".join(missing_required)
            raise RuntimeError(
                "Required trace artifact files are not staged for commit: "
                f"{joined}"
            )

        ui_appendix = str(ui_evidence_state.get("ui_evidence_appendix", "")).strip()
        if ui_appendix:
            final_message = build_commit_message(final_message, ui_appendix)

    git(["commit", "-m", final_message], cwd=repo_root)
    return {
        **ui_evidence_state,
        "commit_message_final": final_message,
        "committed_paths": sorted(set(meaningful_changes)),
    }


def get_head_commit_sha(repo_root: Path) -> str:
    return git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()


def get_head_commit_message(repo_root: Path) -> str:
    return entire_service().get_head_commit_message(repo_root)


def extract_commit_trailer(commit_message: str, trailer_key: str) -> str:
    return entire_service().extract_commit_trailer(commit_message, trailer_key)


def is_no_change_runtime_error(error: RuntimeError) -> bool:
    message = str(error).strip()
    if not message:
        return False
    return message.startswith("No file changes were created by the coder agent.")


def extract_attempt_index(file_name: str) -> int:
    match = re.search(r"_attempt_(\d+)\.md$", file_name)
    if not match:
        return sys.maxsize
    return int(match.group(1))


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
    return entire_service().prepare_entire_explicit_registration(
        repo_root=repo_root,
        run_dir=run_dir,
        context=context,
    )


def verify_entire_explicit_registration(
    *,
    repo_root: Path,
    run_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    return entire_service().verify_entire_explicit_registration(
        repo_root=repo_root,
        run_dir=run_dir,
        context=context,
    )


def generate_entire_explain(
    *,
    repo_root: Path,
    run_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    return entire_service().generate_entire_explain(
        repo_root=repo_root,
        run_dir=run_dir,
        context=context,
    )


_LOGS_SERVICE: PipelineAiLogsService | None = None


def logs_service() -> PipelineAiLogsService:
    global _LOGS_SERVICE
    if _LOGS_SERVICE is None:
        ui = ui_service()
        _LOGS_SERVICE = PipelineAiLogsService(
            normalize_repo_path=normalize_repo_path,
            format_template=format_template,
            resolve_repo_relative_path=resolve_repo_relative_path,
            resolve_ui_artifact_dir_from_config=ui.resolve_ui_artifact_dir_from_config,
            resolve_ui_repo_evidence_dir=ui.resolve_ui_repo_evidence_dir,
            resolve_ui_image_extensions_from_config=ui.resolve_ui_image_extensions_from_config,
            to_evidence_filename=ui.to_evidence_filename,
            write_text=write_text,
            log=log,
            git=git,
        )
    return _LOGS_SERVICE


def push_branch(repo_root: Path, branch_name: str) -> None:
    push_proc = git(
        ["push", "-u", "origin", branch_name],
        cwd=repo_root,
        check=False,
    )
    if push_proc.returncode == 0:
        return

    push_stderr = push_proc.stderr or ""
    if "non-fast-forward" in push_stderr:
        log(
            "INFO: Push rejected by non-fast-forward. "
            f"Rebasing `{branch_name}` onto `origin/{branch_name}` and retrying."
        )
        git(["pull", "--rebase", "origin", branch_name], cwd=repo_root, check=True)
        git(["push", "-u", "origin", branch_name], cwd=repo_root, check=True)
        return

    raise RuntimeError(
        "コード変更ブランチの push に失敗しました。\n"
        f"stderr:\n{push_stderr}"
    )


_PR_SERVICE: PipelinePullRequestService | None = None


def pr_service() -> PipelinePullRequestService:
    global _PR_SERVICE
    if _PR_SERVICE is None:
        _PR_SERVICE = PipelinePullRequestService(
            run_process=run_process,
            read_text=read_text,
            log=log,
        )
    return _PR_SERVICE


_RUNTIME_SERVICE: PipelineRuntimeService | None = None


def runtime_service() -> PipelineRuntimeService:
    global _RUNTIME_SERVICE
    if _RUNTIME_SERVICE is None:
        _RUNTIME_SERVICE = PipelineRuntimeService(
            default_config_path=DEFAULT_CONFIG_PATH,
            default_projects_path=DEFAULT_PROJECTS_PATH,
            resolve_path=lambda value, *, base_dir: resolve_path(value, base_dir=base_dir),
            load_json=load_json,
            validate_config=validate_config,
            merge_dict=merge_dict,
            slugify=lambda value, max_len=40: slugify(value, max_len=max_len),
            normalize_repo_slug=normalize_repo_slug,
            detect_repo_slug=detect_repo_slug,
            git=lambda args, *, cwd, check=True: git(args, cwd=cwd, check=check),
            run_process=lambda args, *, check=True: run_process(args, check=check),
        )
    return _RUNTIME_SERVICE


def resolve_runtime(
    *,
    control_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return runtime_service().resolve_runtime(
        control_root=control_root,
        args=args,
    )


def parse_args() -> argparse.Namespace:
    return runtime_service().parse_args()



def build_execution_dependencies() -> dict[str, Any]:
    issue = issue_service()
    summary = summary_service()
    ui = ui_service()
    logs = logs_service()
    pr = pr_service()

    return {
        "resolve_runtime": resolve_runtime,
        "require_clean_worktree": require_clean_worktree,
        "load_issue_from_file": issue.load_issue_from_file,
        "load_issue_from_gh": issue.load_issue_from_gh,
        "build_default_pr_title": build_default_pr_title,
        "resolve_feedback_pr_context": issue.resolve_feedback_pr_context,
        "slugify": slugify,
        "detect_workflow_artifact_metadata": ui.detect_workflow_artifact_metadata,
        "resolve_ui_repo_evidence_dir": ui.resolve_ui_repo_evidence_dir,
        "render_issue_instruction_markdown": render_issue_instruction_markdown,
        "render_related_issue_markdown": render_related_issue_markdown,
        "render_validation_commands_markdown": render_validation_commands_markdown,
        "render_log_location_markdown": render_log_location_markdown,
        "load_feedback_text": issue.load_feedback_text,
        "parse_positive_int": parse_positive_int,
        "write_text": write_text,
        "ensure_branch": ensure_branch,
        "setup_entire_trace": setup_entire_trace,
        "resolve_command": resolve_command,
        "resolve_path": resolve_path,
        "render_template_file": render_template_file,
        "run_agent_command": run_agent_command,
        "read_text": read_text,
        "run_quality_gates": run_quality_gates,
        "clip_text": clip_text,
        "build_codex_commit_summary": summary.build_codex_commit_summary,
        "prepare_entire_explicit_registration": prepare_entire_explicit_registration,
        "save_ai_logs_bundle": logs.save_ai_logs_bundle,
        "publish_ai_logs_to_dedicated_branch": logs.publish_ai_logs_to_dedicated_branch,
        "build_ui_evidence_ai_logs_context": ui.build_ui_evidence_ai_logs_context,
        "cleanup_untracked_coder_outputs": cleanup_untracked_coder_outputs,
        "format_template": format_template,
        "build_commit_message": build_commit_message,
        "commit_changes": commit_changes,
        "is_no_change_runtime_error": is_no_change_runtime_error,
        "get_head_commit_sha": get_head_commit_sha,
        "get_head_commit_message": get_head_commit_message,
        "extract_commit_trailer": extract_commit_trailer,
        "verify_entire_explicit_registration": verify_entire_explicit_registration,
        "generate_entire_explain": generate_entire_explain,
        "push_branch": push_branch,
        "parse_string_list": parse_string_list,
        "build_pr_change_type_checklist_markdown": build_pr_change_type_checklist_markdown,
        "build_pr_auto_checklist_markdown": build_pr_auto_checklist_markdown,
        "build_pr_manual_checklist_markdown": build_pr_manual_checklist_markdown,
        "validate_required_pr_context": validate_required_pr_context,
        "create_or_update_pr": pr.create_or_update_pr,
        "resolve_pr_number": pr.resolve_pr_number,
        "extract_trigger_reason_from_feedback_text": pr.extract_trigger_reason_from_feedback_text,
        "is_comment_feedback_trigger": pr.is_comment_feedback_trigger,
        "build_feedback_update_comment": pr.build_feedback_update_comment,
        "post_pr_issue_comment": pr.post_pr_issue_comment,
        "log": log,
    }


def main() -> int:
    args = parse_args()
    control_root = Path.cwd().resolve()
    return run_pipeline(
        args=args,
        control_root=control_root,
        deps=build_execution_dependencies(),
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as err:
        print(f"[agent-pipeline] ERROR: {err}", file=sys.stderr)
        raise SystemExit(1)
