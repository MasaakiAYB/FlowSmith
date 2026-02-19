#!/usr/bin/env python3
"""Entire trace registration and verification operations."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable


class PipelineEntireService:
    """Encapsulates Entire CLI integration and explicit trace handling."""

    def __init__(
        self,
        *,
        parse_positive_int: Callable[..., int],
        format_template: Callable[..., str],
        resolve_repo_relative_path: Callable[..., Path],
        resolve_command: Callable[..., str],
        split_command: Callable[..., list[str]],
        run_logged_process: Callable[..., Any],
        read_text: Callable[[Path], str],
        write_text: Callable[[Path, str], None],
        sha256_text: Callable[[str], str],
        clip_text: Callable[..., str],
        git: Callable[..., Any],
        log: Callable[[str], None],
    ) -> None:
        self._parse_positive_int = parse_positive_int
        self._format_template = format_template
        self._resolve_repo_relative_path = resolve_repo_relative_path
        self._resolve_command = resolve_command
        self._split_command = split_command
        self._run_logged_process = run_logged_process
        self._read_text = read_text
        self._write_text = write_text
        self._sha256_text = sha256_text
        self._clip_text = clip_text
        self._git = git
        self._log = log

    @staticmethod
    def extract_attempt_index(file_name: str) -> int:
        match = re.search(r"_attempt_(\d+)\.md$", file_name)
        if not match:
            return sys.maxsize
        return int(match.group(1))

    @staticmethod
    def extract_commit_trailer(commit_message: str, trailer_key: str) -> str:
        pattern = re.compile(rf"(?mi)^{re.escape(trailer_key)}:\s*(.+)$")
        match = pattern.search(commit_message)
        if not match:
            return ""
        return match.group(1).strip()

    def get_head_commit_message(self, repo_root: Path) -> str:
        return self._git(["log", "-1", "--pretty=%B"], cwd=repo_root).stdout

    def setup_entire_trace(
        self,
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
        explicit_max_chars = self._parse_positive_int(
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
            self._write_text(run_dir / "entire_status.md", "- Entire 連携は無効です。\n")
            return default_state

        raw_command = str(entire_conf_raw.get("command", "entire")).strip() or "entire"
        resolved_command = self._resolve_command(raw_command, required=False)
        if not resolved_command:
            message = "Entire コマンドが設定されていないため、証跡連携をスキップします。"
            if required:
                raise RuntimeError(message)
            self._log(message)
            self._write_text(run_dir / "entire_status.md", f"- {message}\n")
            return {
                **default_state,
                "entire_status": "skipped",
            }

        command_parts = self._split_command(resolved_command, name="entire.command")
        version_log = run_dir / "entire_version.log"
        version_proc = self._run_logged_process(
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
            self._log(message)
            self._write_text(run_dir / "entire_status.md", f"- {message}\n")
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
        strategy_proc = self._run_logged_process(
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
        enable_proc = self._run_logged_process(
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
            self._log(message)
            self._write_text(run_dir / "entire_status.md", f"- {message}\n")
            return {
                **default_state,
                "entire_status": "skipped",
                "entire_agent": agent,
                "entire_strategy": strategy,
                "entire_command": resolved_command,
                "entire_setup_log": str(enable_log),
            }

        self._write_text(
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

    def _render_trace_file_section(
        self,
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

        raw_text = self._read_text(path)
        digest = self._sha256_text(raw_text)
        clipped = self._clip_text(raw_text.strip(), max_chars=max_chars).strip()
        lines.append(f"- sha256: `{digest}`")
        lines.append("")
        lines.append("~~~text")
        lines.append(clipped or "(empty)")
        lines.append("~~~")
        lines.append("")
        return "\n".join(lines)

    def _build_entire_registration_markdown(
        self,
        *,
        run_dir: Path,
        context: dict[str, Any],
        max_chars: int,
    ) -> tuple[str, int]:
        prompt_paths = [run_dir / "planner_prompt.md"]
        prompt_paths.extend(
            sorted(
                run_dir.glob("coder_prompt_attempt_*.md"),
                key=lambda item: self.extract_attempt_index(item.name),
            )
        )
        prompt_paths.append(run_dir / "reviewer_prompt.md")

        attempt_numbers: set[int] = set()
        for path in run_dir.glob("*_attempt_*.md"):
            index = self.extract_attempt_index(path.name)
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
            lines.append(self._render_trace_file_section(title=title, path=path, max_chars=max_chars))

        lines.extend(["## 2. 試行錯誤", ""])
        for attempt in sorted(attempt_numbers):
            lines.append(f"### attempt {attempt}")
            lines.append(
                self._render_trace_file_section(
                    title=f"coder_output_attempt_{attempt}.md",
                    path=run_dir / f"coder_output_attempt_{attempt}.md",
                    max_chars=max_chars,
                )
            )
            lines.append(
                self._render_trace_file_section(
                    title=f"validation_attempt_{attempt}.md",
                    path=run_dir / f"validation_attempt_{attempt}.md",
                    max_chars=max_chars,
                )
            )

        lines.extend(["## 3. 設計根拠", ""])
        plan_file = Path(str(context.get("plan_file", run_dir / "plan.md")))
        review_file = Path(str(context.get("review_file", run_dir / "review.md")))
        lines.append(self._render_trace_file_section(title=plan_file.name, path=plan_file, max_chars=max_chars))
        lines.append(self._render_trace_file_section(title=review_file.name, path=review_file, max_chars=max_chars))

        content = "\n".join(lines).strip() + "\n"
        return content, len(attempt_numbers)

    def prepare_entire_explicit_registration(
        self,
        *,
        repo_root: Path,
        run_dir: Path,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        explicit_enabled = bool(context.get("entire_explicit_enabled"))
        explicit_required = bool(context.get("entire_explicit_required"))
        append_trailers = bool(context.get("entire_explicit_append_commit_trailers"))
        max_chars = self._parse_positive_int(
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
            self._write_text(run_dir / "entire_registration_status.md", "- 明示登録は無効です。\n")
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
            self._log(f"WARNING: {message}")
            self._write_text(run_dir / "entire_registration_status.md", f"- {message}\n")
            return default_state

        try:
            artifact_relative_path = self._format_template(
                artifact_template,
                context,
                "entire.explicit_registration.artifact_path",
            ).strip()
            artifact_path = self._resolve_repo_relative_path(
                artifact_relative_path,
                repo_root=repo_root,
                setting_name="entire.explicit_registration.artifact_path",
            )
            artifact_content, attempt_count = self._build_entire_registration_markdown(
                run_dir=run_dir,
                context=context,
                max_chars=max_chars,
            )
            artifact_sha = self._sha256_text(artifact_content)
        except RuntimeError as err:
            if explicit_required:
                raise
            message = f"明示登録バンドル生成をスキップしました: {err}"
            self._log(f"WARNING: {message}")
            self._write_text(run_dir / "entire_registration_status.md", f"- {message}\n")
            return default_state

        self._write_text(run_dir / "entire_registration_bundle.md", artifact_content)
        self._write_text(artifact_path, artifact_content)

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

        self._write_text(
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
        self,
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
            self._write_text(run_dir / "entire_registration_check.md", "- 明示登録検証は無効です。\n")
            return default_state

        checks: list[str] = []
        errors: list[str] = []
        commit_message = self.get_head_commit_message(repo_root)

        trace_file = str(context.get("entire_trace_file", "")).strip()
        trace_hash = str(context.get("entire_trace_sha256", "")).strip()
        if append_trailers:
            trailer_file = self.extract_commit_trailer(commit_message, "Entire-Trace-File")
            trailer_hash = self.extract_commit_trailer(commit_message, "Entire-Trace-SHA256")
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
                trace_path = self._resolve_repo_relative_path(
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
                    actual_hash = self._sha256_text(self._read_text(trace_path))
                    checks.append(f"- artifact_hash: `{actual_hash}`")
                    in_head = (
                        self._git(
                            ["cat-file", "-e", f"HEAD:{trace_file}"],
                            cwd=repo_root,
                            check=False,
                        ).returncode
                        == 0
                    )
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
        self._write_text(run_dir / "entire_registration_check.md", "\n".join(report_lines).strip() + "\n")

        if errors:
            if explicit_required:
                raise RuntimeError("\n".join(errors))
            self._log("WARNING: Entire 明示登録の検証でエラーが発生しました。")
            for message in errors:
                self._log(f"WARNING: {message}")
            return {
                **default_state,
                "entire_trace_verify_status": "failed",
            }

        return {
            **default_state,
            "entire_trace_verify_status": "passed",
        }

    def generate_entire_explain(
        self,
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
            self._log(f"WARNING: {message}")
            return default_state

        command_parts = self._split_command(raw_command, name="entire.command")
        explain_log = run_dir / "entire_explain_generate.log"
        explain_proc = self._run_logged_process(
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
            self._log(f"WARNING: {message}")
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
