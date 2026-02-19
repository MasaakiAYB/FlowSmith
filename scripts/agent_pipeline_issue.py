#!/usr/bin/env python3
"""Issue and PR feedback operations for agent pipeline."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


class PipelineIssueService:
    """Encapsulates issue loading and PR feedback extraction operations."""

    def __init__(
        self,
        *,
        run_process: Callable[..., subprocess.CompletedProcess[str]],
        read_text: Callable[[Path], str],
        write_text: Callable[[Path, str], None],
        resolve_path: Callable[..., Path],
        normalize_inline_text: Callable[[str], str],
        clip_inline_text: Callable[..., str],
        clip_text: Callable[..., str],
    ) -> None:
        self._run_process = run_process
        self._read_text = read_text
        self._write_text = write_text
        self._resolve_path = resolve_path
        self._normalize_inline_text = normalize_inline_text
        self._clip_inline_text = clip_inline_text
        self._clip_text = clip_text

    def load_issue_from_file(self, path: Path, issue_number: int) -> dict[str, Any]:
        body = self._read_text(path).strip()
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        title = lines[0].lstrip("# ").strip() if lines else f"Issue {issue_number}"
        return {
            "number": issue_number,
            "title": title or f"Issue {issue_number}",
            "body": body,
            "url": "",
            "labels": [],
            "state": "open",
        }

    def load_issue_from_gh(self, issue_number: int, *, repo_slug: str, cwd: Path) -> dict[str, Any]:
        cmd = [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--json",
            "number,title,body,url,labels,state",
        ]
        if repo_slug:
            cmd.extend(["--repo", repo_slug])

        proc = self._run_process(cmd, cwd=cwd, check=False)
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
            "state": str(payload.get("state") or "open").strip().lower(),
        }

    def gh_api_json(self, *, endpoint: str, cwd: Path) -> Any:
        proc = self._run_process(
            ["gh", "api", endpoint],
            cwd=cwd,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"GitHub API call failed: {endpoint}\n"
                + (f"detail:\n{detail}" if detail else "")
            )
        try:
            return json.loads(proc.stdout or "null")
        except json.JSONDecodeError as err:
            raise RuntimeError(f"GitHub API returned invalid JSON: {endpoint}") from err

    @staticmethod
    def is_bot_login(login: str) -> bool:
        normalized = str(login or "").strip().lower()
        if not normalized:
            return True
        if normalized.endswith("[bot]"):
            return True
        if normalized in {"github-actions", "github-actions[bot]", "dependabot[bot]"}:
            return True
        return False

    def is_agent_command_comment(self, text: str) -> bool:
        normalized = self._normalize_inline_text(text).lower()
        if not normalized:
            return True
        return bool(re.fullmatch(r"/agent(?:\s+[a-z0-9_-]+)?", normalized))

    def build_pr_feedback_digest(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        pr_number: int,
        max_items: int,
        max_chars_per_item: int = 500,
    ) -> dict[str, Any]:
        if not repo_slug:
            raise RuntimeError("PRフィードバック抽出には target repo slug が必要です。")
        if pr_number <= 0:
            raise RuntimeError("feedback_pr_number must be a positive integer.")

        pull_payload = self.gh_api_json(
            endpoint=f"repos/{repo_slug}/pulls/{pr_number}",
            cwd=repo_root,
        )
        if not isinstance(pull_payload, dict):
            raise RuntimeError("PR情報の取得結果が不正です。")
        pr_url = str(pull_payload.get("html_url", "")).strip()

        reviews_payload = self.gh_api_json(
            endpoint=f"repos/{repo_slug}/pulls/{pr_number}/reviews?per_page=100",
            cwd=repo_root,
        )
        comments_payload = self.gh_api_json(
            endpoint=f"repos/{repo_slug}/pulls/{pr_number}/comments?per_page=100",
            cwd=repo_root,
        )
        issue_comments_payload = self.gh_api_json(
            endpoint=f"repos/{repo_slug}/issues/{pr_number}/comments?per_page=100",
            cwd=repo_root,
        )

        reviews = reviews_payload if isinstance(reviews_payload, list) else []
        review_comments = comments_payload if isinstance(comments_payload, list) else []
        issue_comments = issue_comments_payload if isinstance(issue_comments_payload, list) else []

        items: list[dict[str, Any]] = []

        def add_item(
            *,
            source: str,
            text: str,
            author: str,
            url: str,
            created_at: str,
            priority: int,
        ) -> None:
            normalized_text = self._normalize_inline_text(text)
            if not normalized_text or self.is_agent_command_comment(normalized_text):
                return
            if self.is_bot_login(author):
                return
            items.append(
                {
                    "source": source,
                    "text": self._clip_inline_text(normalized_text, max_chars=max_chars_per_item),
                    "author": str(author).strip(),
                    "url": str(url).strip(),
                    "created_at": str(created_at).strip(),
                    "priority": priority,
                }
            )

        for review in reviews:
            if not isinstance(review, dict):
                continue
            state = str(review.get("state", "")).strip().upper()
            if state not in {"CHANGES_REQUESTED", "COMMENTED"}:
                continue
            user = review.get("user", {})
            author = str(user.get("login", "")).strip() if isinstance(user, dict) else ""
            priority = 100 if state == "CHANGES_REQUESTED" else 60
            add_item(
                source=f"review:{state.lower()}",
                text=str(review.get("body") or ""),
                author=author,
                url=str(review.get("html_url") or ""),
                created_at=str(review.get("submitted_at") or review.get("created_at") or ""),
                priority=priority,
            )

        for comment in review_comments:
            if not isinstance(comment, dict):
                continue
            user = comment.get("user", {})
            author = str(user.get("login", "")).strip() if isinstance(user, dict) else ""
            path = str(comment.get("path") or "").strip()
            line = comment.get("line")
            location = path
            if isinstance(line, int):
                location = f"{path}:{line}" if path else str(line)
            source = "review-comment"
            if location:
                source = f"{source}:{location}"
            add_item(
                source=source,
                text=str(comment.get("body") or ""),
                author=author,
                url=str(comment.get("html_url") or ""),
                created_at=str(comment.get("created_at") or ""),
                priority=90,
            )

        for comment in issue_comments:
            if not isinstance(comment, dict):
                continue
            user = comment.get("user", {})
            author = str(user.get("login", "")).strip() if isinstance(user, dict) else ""
            add_item(
                source="pr-comment",
                text=str(comment.get("body") or ""),
                author=author,
                url=str(comment.get("html_url") or ""),
                created_at=str(comment.get("created_at") or ""),
                priority=70,
            )

        sorted_items = sorted(
            items,
            key=lambda item: (-int(item.get("priority", 0)), str(item.get("created_at", ""))),
        )
        unique_items: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        for item in sorted_items:
            dedupe_key = self._normalize_inline_text(str(item.get("text", "")).lower())
            if not dedupe_key or dedupe_key in seen_texts:
                continue
            seen_texts.add(dedupe_key)
            unique_items.append(item)
            if len(unique_items) >= max_items:
                break

        if not unique_items:
            markdown = (
                "## PRレビュー指摘（自動抽出）\n\n"
                f"- PR: {pr_url or f'https://github.com/{repo_slug}/pull/{pr_number}'}\n"
                "- 抽出件数: `0`\n"
                "- 有効な改善指摘は見つかりませんでした。\n"
            )
            return {
                "count": 0,
                "url": pr_url,
                "markdown": markdown,
                "text": "",
            }

        lines = [
            "## PRレビュー指摘（自動抽出）",
            "",
            f"- PR: {pr_url or f'https://github.com/{repo_slug}/pull/{pr_number}'}",
            f"- 抽出件数: `{len(unique_items)}`",
            "",
        ]
        text_lines: list[str] = []
        for idx, item in enumerate(unique_items, start=1):
            source = str(item.get("source", "feedback")).strip()
            author = str(item.get("author", "")).strip() or "unknown"
            text = str(item.get("text", "")).strip()
            url = str(item.get("url", "")).strip()
            lines.append(f"{idx}. `[{source}] @{author}` {text}")
            if url:
                lines.append(f"   - 参照: {url}")
            text_lines.append(f"- [{source}] {text}")

        markdown = "\n".join(lines).strip() + "\n"
        text = "\n".join(text_lines).strip()
        return {
            "count": len(unique_items),
            "url": pr_url,
            "markdown": markdown,
            "text": text,
        }

    def resolve_feedback_pr_context(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        pr_number: int,
    ) -> dict[str, str]:
        if not repo_slug:
            raise RuntimeError("feedback_pr_number を使う場合は target repo slug が必要です。")
        if pr_number <= 0:
            return {"head_ref": "", "base_ref": "", "url": ""}

        payload = self.gh_api_json(
            endpoint=f"repos/{repo_slug}/pulls/{pr_number}",
            cwd=repo_root,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("PR情報の取得結果が不正です。")

        head_ref = ""
        head_raw = payload.get("head")
        if isinstance(head_raw, dict):
            head_ref = str(head_raw.get("ref") or "").strip()

        base_ref = ""
        base_raw = payload.get("base")
        if isinstance(base_raw, dict):
            base_ref = str(base_raw.get("ref") or "").strip()

        pr_url = str(payload.get("html_url") or "").strip()

        if not head_ref:
            raise RuntimeError(
                "feedback_pr_number に対応するPRの head ブランチを取得できませんでした。"
            )

        return {
            "head_ref": head_ref,
            "base_ref": base_ref,
            "url": pr_url,
        }

    def load_feedback_text(
        self,
        *,
        control_root: Path,
        run_dir: Path,
        repo_root: Path,
        repo_slug: str,
        feedback_file: Path | None,
        feedback_text: str,
        feedback_pr_number: int,
        feedback_max_items: int,
    ) -> dict[str, Any]:
        parts: list[str] = []
        sections: list[str] = []
        feedback_source_lines: list[str] = []
        pr_url = ""
        feedback_count = 0

        direct_text = str(feedback_text or "").strip()
        if direct_text:
            normalized = self._clip_text(direct_text, max_chars=8000).strip()
            parts.append(normalized)
            sections.append("## 追加フィードバック（外部指定）\n\n" + normalized)
            feedback_source_lines.append("- source: `feedback_text`")

        if feedback_file:
            resolved_feedback_file = self._resolve_path(feedback_file, base_dir=control_root)
            if not resolved_feedback_file.exists():
                raise RuntimeError(f"feedback file not found: {resolved_feedback_file}")
            loaded = self._read_text(resolved_feedback_file).strip()
            if loaded:
                normalized = self._clip_text(loaded, max_chars=8000).strip()
                parts.append(normalized)
                sections.append(
                    "## 追加フィードバック（feedback_file）\n\n"
                    f"- file: `{resolved_feedback_file}`\n\n{normalized}"
                )
                feedback_source_lines.append(f"- source: `feedback_file` ({resolved_feedback_file})")

        if feedback_pr_number > 0:
            digest = self.build_pr_feedback_digest(
                repo_root=repo_root,
                repo_slug=repo_slug,
                pr_number=feedback_pr_number,
                max_items=feedback_max_items,
            )
            pr_url = str(digest.get("url", "")).strip()
            feedback_count = int(digest.get("count", 0))
            markdown = str(digest.get("markdown", "")).strip()
            text = str(digest.get("text", "")).strip()

            self._write_text(run_dir / "external_feedback_pr.md", markdown + ("\n" if markdown else ""))
            if text:
                parts.append(self._clip_text(text, max_chars=8000).strip())
                sections.append(markdown)
            feedback_source_lines.append(
                f"- source: `pr_feedback` (pr=#{feedback_pr_number}, items={feedback_count})"
            )

        merged_text = "\n".join(item for item in parts if item).strip()
        merged_markdown = "\n\n".join(section for section in sections if section).strip()
        if not merged_markdown:
            merged_markdown = "_追加フィードバックなし_"
        if not feedback_source_lines:
            feedback_source_lines.append("- source: `(none)`")

        self._write_text(
            run_dir / "external_feedback_status.md",
            "\n".join(
                [
                    "# External Feedback",
                    "",
                    *feedback_source_lines,
                    f"- merged_length: `{len(merged_text)}`",
                    f"- pr_feedback_url: `{pr_url or 'none'}`",
                ]
            )
            + "\n",
        )

        return {
            "external_feedback_text": merged_text,
            "external_feedback_markdown": merged_markdown,
            "external_feedback_pr_number": feedback_pr_number if feedback_pr_number > 0 else 0,
            "external_feedback_pr_url": pr_url,
            "external_feedback_item_count": feedback_count,
        }
