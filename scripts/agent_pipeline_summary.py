#!/usr/bin/env python3
"""Codex commit summary generation for agent pipeline."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable


class PipelineCommitSummaryService:
    """Encapsulates summary extraction and commit appendix generation."""

    def __init__(
        self,
        *,
        normalize_inline_text: Callable[[str], str],
        clip_inline_text: Callable[..., str],
        clip_text: Callable[..., str],
        parse_positive_int: Callable[..., int],
        format_template: Callable[..., str],
        normalize_repo_path: Callable[[str], str],
        extract_attempt_index: Callable[[str], int],
        read_text: Callable[[Path], str],
        write_text: Callable[[Path, str], None],
        log: Callable[[str], None],
    ) -> None:
        self._normalize_inline_text = normalize_inline_text
        self._clip_inline_text = clip_inline_text
        self._clip_text = clip_text
        self._parse_positive_int = parse_positive_int
        self._format_template = format_template
        self._normalize_repo_path = normalize_repo_path
        self._extract_attempt_index = extract_attempt_index
        self._read_text = read_text
        self._write_text = write_text
        self._log = log

    def strip_markdown_prefix(self, line: str) -> str:
        text = line.strip()
        if not text:
            return ""
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
        text = re.sub(r"^\s*[-*+]\s+", "", text)
        text = re.sub(r"^\s*\d+[.)]\s+", "", text)
        return self._normalize_inline_text(text)

    def is_noninformative_highlight(self, text: str) -> bool:
        normalized = self._normalize_inline_text(text).lower()
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

    def extract_text_highlights(self, raw_text: str, *, max_items: int, max_chars: int) -> list[str]:
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
            normalized = self.strip_markdown_prefix(line)
            if not normalized:
                continue
            if self.is_noninformative_highlight(normalized):
                continue
            highlights.append(self._clip_inline_text(normalized, max_chars=max_chars))
            if len(highlights) >= max_items:
                break

        if highlights:
            return highlights
        fallback = self._clip_inline_text(raw_text or "(empty)", max_chars=max_chars).strip()
        return [fallback or "(empty)"]

    def extract_file_highlights(self, path: Path, *, max_items: int, max_chars: int) -> list[str]:
        if not path.exists():
            return ["(missing)"]
        content = self._read_text(path).strip()
        if not content:
            return ["(empty)"]
        return self.extract_text_highlights(content, max_items=max_items, max_chars=max_chars)

    def first_meaningful(self, items: list[str], *, fallback: str) -> str:
        for item in items:
            value = self._normalize_inline_text(item)
            if value and value not in {"(missing)", "(empty)"}:
                return value
        return fallback

    @staticmethod
    def detect_attempt_status(validation_text: str) -> str:
        upper = validation_text.upper()
        if "FAIL" in upper:
            return "failed"
        if "PASS" in upper:
            return "passed"
        return "unknown"

    def build_codex_commit_summary(
        self,
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
        max_chars = self._parse_positive_int(
            summary_conf_raw.get("max_chars_per_item"),
            default=240,
            name="codex_commit_summary.max_chars_per_item",
        )
        max_attempts = self._parse_positive_int(
            summary_conf_raw.get("max_attempts"),
            default=5,
            name="codex_commit_summary.max_attempts",
        )
        max_points = self._parse_positive_int(
            summary_conf_raw.get("max_points"),
            default=3,
            name="codex_commit_summary.max_points",
        )
        max_total_chars = self._parse_positive_int(
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
            self._write_text(run_dir / "codex_commit_summary_status.md", "- Codex要約は無効です。\n")
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

            issue_points = self.extract_text_highlights(
                issue_body or "(Issue 本文なし)",
                max_items=max_points,
                max_chars=max_chars,
            )
            planner_prompt_points = self.extract_file_highlights(
                planner_prompt,
                max_items=1,
                max_chars=max_chars,
            )
            plan_points = self.extract_file_highlights(
                plan_file,
                max_items=max_points,
                max_chars=max_chars,
            )
            review_points = self.extract_file_highlights(
                review_file,
                max_items=max_points,
                max_chars=max_chars,
            )

            attempt_ids: set[int] = set()
            for path in run_dir.glob("coder_output_attempt_*.md"):
                idx = self._extract_attempt_index(path.name)
                if idx != sys.maxsize:
                    attempt_ids.add(idx)
            for path in run_dir.glob("validation_attempt_*.md"):
                idx = self._extract_attempt_index(path.name)
                if idx != sys.maxsize:
                    attempt_ids.add(idx)

            attempt_rows: list[dict[str, Any]] = []
            last_validation_lines: list[str] = ["(missing)"]
            for idx in sorted(attempt_ids)[:max_attempts]:
                coder_prompt_points = self.extract_file_highlights(
                    run_dir / f"coder_prompt_attempt_{idx}.md",
                    max_items=1,
                    max_chars=max_chars,
                )
                coder_points = self.extract_file_highlights(
                    run_dir / f"coder_output_attempt_{idx}.md",
                    max_items=1,
                    max_chars=max_chars,
                )
                validation_path = run_dir / f"validation_attempt_{idx}.md"
                validation_points = self.extract_file_highlights(
                    validation_path,
                    max_items=max_points,
                    max_chars=max_chars,
                )
                validation_raw = self._read_text(validation_path) if validation_path.exists() else ""
                status = self.detect_attempt_status(validation_raw)
                goal = self.first_meaningful(
                    coder_prompt_points,
                    fallback="要件実装のための変更を実施",
                )
                action = self.first_meaningful(
                    coder_points,
                    fallback="変更内容の詳細は ai-logs を参照",
                )
                validation = self.first_meaningful(
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
            decision_line = self.first_meaningful(
                plan_points,
                fallback="最小変更で要件を満たす方針",
            )
            validation_line = self.first_meaningful(
                last_validation_lines,
                fallback=self._clip_inline_text(
                    str(context.get("validation_summary", "検証結果なし")),
                    max_chars=max_chars,
                ),
            )
            risk_line = self.first_meaningful(
                review_points,
                fallback="重大な未解決リスクは記録されていません。",
            )
            issue_basis = self.first_meaningful(
                issue_points,
                fallback=self._clip_inline_text(issue_title, max_chars=max_chars),
            )
            implementation_basis = self.first_meaningful(
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
            ai_logs_dir = self._format_template(ai_logs_path_template, context, "ai_logs.path")
            evidence_path = self._normalize_repo_path(str(Path(ai_logs_dir) / ai_logs_index_name))

            planner_basis = self.first_meaningful(
                planner_prompt_points,
                fallback="Planner指示",
            )

            def collect_unique_lines(candidates: list[str], *, limit: int) -> list[str]:
                lines: list[str] = []
                seen: set[str] = set()
                for raw in candidates:
                    normalized = self._clip_inline_text(raw, max_chars=max_chars)
                    if not normalized or normalized in seen:
                        continue
                    seen.add(normalized)
                    lines.append(normalized)
                    if len(lines) >= limit:
                        break
                return lines

            instruction_candidates: list[str] = [
                f"Issue: #{issue_number} {issue_title}",
                f"Issue本文の要点: {issue_basis}",
                f"Plannerへ渡した方針: {planner_basis}",
            ]
            instruction_candidates.extend(
                f"Issue要求: {item}"
                for item in issue_points[:max_points]
                if item and item not in {"(missing)", "(empty)"}
            )
            instruction_lines = collect_unique_lines(
                instruction_candidates,
                limit=max(max_points + 1, 3),
            )
            if not instruction_lines:
                instruction_lines = ["Issue指示情報を抽出できませんでした。"]

            trial_candidates: list[str] = []
            for row in attempt_rows:
                trial_candidates.append(
                    "attempt "
                    f"{row['attempt']} [{row['status']}]: "
                    f"目的={self._clip_inline_text(str(row['goal']), max_chars=max_chars)} / "
                    f"実施={self._clip_inline_text(str(row['action']), max_chars=max_chars)} / "
                    f"結果={self._clip_inline_text(str(row['validation']), max_chars=max_chars)}"
                )
            if not trial_candidates:
                trial_candidates.append(
                    f"単一試行で完了。検証結果: {self._clip_inline_text(validation_line, max_chars=max_chars)}"
                )
            trial_lines = collect_unique_lines(
                trial_candidates,
                limit=max(max_attempts, 1),
            )

            design_candidates: list[str] = [
                f"採用設計: {decision_line}",
                f"設計根拠(Issue): {issue_basis}",
                f"設計根拠(Planner): {planner_basis}",
                f"設計根拠(Plan): {implementation_basis}",
                f"検証方針: {quality_basis}",
                f"リスク判断: {risk_line}",
            ]
            design_lines = collect_unique_lines(
                design_candidates,
                limit=max(max_points + 2, 4),
            )
            if not design_lines:
                design_lines = ["設計根拠を抽出できませんでした。"]

            appendix_lines = [
                "Codex-Context:",
                "- 指示:",
            ]
            appendix_lines.extend(f"  - {item}" for item in instruction_lines)
            appendix_lines.append("- 試行錯誤:")
            appendix_lines.extend(f"  - {item}" for item in trial_lines)
            appendix_lines.append("- 設計根拠:")
            appendix_lines.extend(f"  - {item}" for item in design_lines)
            appendix_lines.extend(
                [
                    "",
                    "Codex-Log-Reference:",
                    f"- AI Logs: {evidence_path}",
                ]
            )
            appendix_text = "\n".join(appendix_lines).strip()
            if len(appendix_text) > max_total_chars:
                appendix_text = self._clip_text(appendix_text, max_chars=max_total_chars).strip()

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
                f"- 課題: {self._clip_inline_text(problem_line, max_chars=max_chars)}",
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
                f"| D2 | 実装方式 | 既存構成順守 / 新規構成追加 | {implementation_basis} | {planner_basis} | 保守性と追従性を確保 |",
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
            self._log(f"WARNING: {message}")
            self._write_text(run_dir / "codex_commit_summary_status.md", f"- {message}\n")
            return default_state

        self._write_text(
            run_dir / "codex_commit_summary_status.md",
            (
                "- Codex要約を生成しました（2層構成）。\n"
                f"- max_chars_per_item: `{max_chars}`\n"
                f"- max_attempts: `{max_attempts}`\n"
                f"- max_points: `{max_points}`\n"
            ),
        )
        self._write_text(run_dir / "codex_commit_summary.md", appendix_text + "\n")
        return {
            **default_state,
            "codex_commit_summary_status": "generated",
            "codex_commit_summary_appendix": appendix_text,
            "codex_commit_summary_markdown": markdown_text,
        }
