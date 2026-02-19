#!/usr/bin/env python3
"""Pull request and label operations for agent pipeline."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote


class PipelinePullRequestService:
    """Encapsulates GitHub PR/label/comment operations."""

    def __init__(
        self,
        *,
        run_process: Callable[..., subprocess.CompletedProcess[str]],
        read_text: Callable[[Path], str],
        log: Callable[[str], None],
    ) -> None:
        self._run_process = run_process
        self._read_text = read_text
        self._log = log

    @staticmethod
    def normalize_repo_slug(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"^https?://github\.com/", "", text)
        text = re.sub(r"^git@github\.com:", "", text)
        text = text.removesuffix(".git")
        text = text.strip("/")
        parts = [part.strip() for part in text.split("/") if part.strip()]
        if len(parts) < 2:
            return text
        return f"{parts[-2]}/{parts[-1]}"

    @classmethod
    def split_repo_slug(cls, repo_slug: str) -> tuple[str, str]:
        normalized = cls.normalize_repo_slug(repo_slug)
        if not normalized or "/" not in normalized:
            raise RuntimeError(f"Invalid repository slug: {repo_slug}")
        owner, repo = normalized.split("/", 1)
        if not owner or not repo:
            raise RuntimeError(f"Invalid repository slug: {repo_slug}")
        return owner, repo

    def _gh_api_json(self, *, endpoint: str, cwd: Path) -> Any:
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
    def normalize_label_list(labels: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for label in labels:
            item = str(label).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    @staticmethod
    def build_default_label_spec(label_name: str) -> tuple[str, str]:
        specs: dict[str, tuple[str, str]] = {
            "agent/": ("0E8A16", "FlowSmith autonomous agent PR"),
            "agent-task": ("0E8A16", "FlowSmith autonomous agent task"),
            "agent": ("0E8A16", "FlowSmith autonomous agent work"),
        }
        if label_name in specs:
            return specs[label_name]
        return "1D76DB", f"FlowSmith label: {label_name}"

    def resolve_repo_label_names(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
    ) -> set[str]:
        normalized_repo = self.normalize_repo_slug(repo_slug)
        if not normalized_repo:
            return set()
        proc = self._run_process(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{normalized_repo}/labels",
                "--jq",
                ".[].name",
            ],
            cwd=repo_root,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            self._log(
                "WARNING: リポジトリラベル一覧の取得に失敗しました。"
                + (f" detail={detail}" if detail else "")
            )
            return set()
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    def ensure_repo_label_exists(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        label_name: str,
    ) -> bool:
        normalized_repo = self.normalize_repo_slug(repo_slug)
        if not normalized_repo:
            return False
        color, description = self.build_default_label_spec(label_name)
        create_proc = self._run_process(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{normalized_repo}/labels",
                "-f",
                f"name={label_name}",
                "-f",
                f"color={color}",
                "-f",
                f"description={description}",
            ],
            cwd=repo_root,
            check=False,
        )
        if create_proc.returncode == 0:
            self._log(f"INFO: PRラベルを作成しました: `{label_name}`")
            return True

        detail = (create_proc.stderr or create_proc.stdout or "").strip()
        lowered = detail.lower()
        if "already_exists" in lowered or "already exists" in lowered:
            return True

        patch_proc = self._run_process(
            [
                "gh",
                "api",
                "-X",
                "PATCH",
                f"repos/{normalized_repo}/labels/{quote(label_name, safe='')}",
                "-f",
                f"new_name={label_name}",
                "-f",
                f"color={color}",
                "-f",
                f"description={description}",
            ],
            cwd=repo_root,
            check=False,
        )
        if patch_proc.returncode == 0:
            return True

        patch_detail = (patch_proc.stderr or patch_proc.stdout or "").strip()
        self._log(
            "WARNING: PRラベルの作成に失敗しました。"
            f" label={label_name}"
            + (f" detail={detail}" if detail else "")
            + (f" patch_detail={patch_detail}" if patch_detail else "")
        )
        return False

    def resolve_pr_labels_for_repo(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        labels: list[str],
    ) -> list[str]:
        requested = self.normalize_label_list(labels)
        if not requested:
            return []

        available = self.resolve_repo_label_names(repo_root=repo_root, repo_slug=repo_slug)

        fallback_map: dict[str, list[str]] = {
            "agent/": ["agent-task", "agent"],
            "agent-task": ["agent/", "agent"],
        }
        resolved: list[str] = []
        seen_resolved: set[str] = set()
        for label in requested:
            if label in available:
                if label not in seen_resolved:
                    seen_resolved.add(label)
                    resolved.append(label)
                continue

            replacement = ""
            for candidate in fallback_map.get(label, []):
                if candidate in available:
                    replacement = candidate
                    break
            if replacement:
                if replacement not in seen_resolved:
                    seen_resolved.add(replacement)
                    resolved.append(replacement)
                self._log(f"INFO: PRラベルをフォールバックします: `{label}` -> `{replacement}`")
                continue

            if self.ensure_repo_label_exists(
                repo_root=repo_root,
                repo_slug=repo_slug,
                label_name=label,
            ):
                available.add(label)
                if label not in seen_resolved:
                    seen_resolved.add(label)
                    resolved.append(label)
                continue

            created_replacement = ""
            for candidate in fallback_map.get(label, []):
                if self.ensure_repo_label_exists(
                    repo_root=repo_root,
                    repo_slug=repo_slug,
                    label_name=candidate,
                ):
                    created_replacement = candidate
                    break
            if created_replacement:
                available.add(created_replacement)
                if created_replacement not in seen_resolved:
                    seen_resolved.add(created_replacement)
                    resolved.append(created_replacement)
                self._log(
                    "INFO: PRラベルをフォールバック作成しました: "
                    f"`{label}` -> `{created_replacement}`"
                )
                continue

            self._log(f"WARNING: PRラベルが見つからないためスキップします: `{label}`")

        return resolved

    def fetch_pr_label_names(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        pr_ref: str,
    ) -> set[str]:
        pr_number = self.resolve_pr_number(pr_ref)
        normalized_repo = self.normalize_repo_slug(repo_slug)
        if not normalized_repo or not pr_number:
            return set()
        proc = self._run_process(
            [
                "gh",
                "api",
                f"repos/{normalized_repo}/issues/{pr_number}/labels",
                "--jq",
                ".[].name",
            ],
            cwd=repo_root,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            self._log(
                "WARNING: PRラベル一覧の取得に失敗しました。"
                f" pr={pr_ref} number={pr_number}"
                + (f" detail={detail}" if detail else "")
            )
            return set()
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    @staticmethod
    def resolve_pr_number(pr_ref: str) -> str:
        text = str(pr_ref).strip()
        if re.fullmatch(r"\d+", text):
            return text
        match = re.search(r"/pull/(\d+)", text)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def extract_trigger_reason_from_feedback_text(feedback_text: str) -> str:
        content = str(feedback_text or "")
        match = re.search(r"(?im)^Triggered by:\s*(.+?)\s*$", content)
        if not match:
            return ""
        return match.group(1).strip().lower()

    @staticmethod
    def is_comment_feedback_trigger(trigger_reason: str) -> bool:
        normalized = str(trigger_reason or "").strip().lower()
        if not normalized:
            return False
        if normalized in {"pr-comment", "review-comment", "comment-command", "review:commented"}:
            return True
        return (
            normalized.startswith("pr-comment")
            or normalized.startswith("review-comment")
            or normalized.startswith("comment-command")
        )

    @staticmethod
    def build_feedback_update_comment(
        *,
        head_commit: str,
        ai_logs_index_url: str,
    ) -> str:
        lines = [
            "コメントありがとうございます。ご指摘を反映して、PRを更新しました。",
            "お手すきの際にご確認をお願いします。",
        ]

        short_sha = str(head_commit or "").strip()
        ai_logs_url = str(ai_logs_index_url or "").strip()
        if (short_sha and short_sha != "(no-change)") or ai_logs_url:
            lines.append("")
        if short_sha and short_sha != "(no-change)":
            lines.append(f"- 更新コミット: `{short_sha[:12]}`")
        if ai_logs_url:
            lines.append(f"- AIログ: {ai_logs_url}")
        return "\n".join(lines).strip() + "\n"

    def post_pr_issue_comment(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        pr_number: str,
        body: str,
    ) -> bool:
        normalized_repo = self.normalize_repo_slug(repo_slug)
        normalized_pr = self.resolve_pr_number(pr_number)
        normalized_body = str(body or "").strip()
        if not normalized_repo or not normalized_pr or not normalized_body:
            return False

        proc = self._run_process(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{normalized_repo}/issues/{normalized_pr}/comments",
                "-f",
                f"body={normalized_body}",
            ],
            cwd=repo_root,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            self._log(
                "WARNING: PRコメント投稿に失敗しました。"
                f" repo={normalized_repo} pr={normalized_pr}"
                + (f" detail={detail}" if detail else "")
            )
            return False
        return True

    def add_labels_to_pr(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        pr_ref: str,
        labels: list[str],
        labels_required: bool,
    ) -> None:
        requested_labels = self.normalize_label_list(labels)
        if not requested_labels:
            return

        pr_number = self.resolve_pr_number(pr_ref)
        if not pr_number:
            message = f"PR番号を解決できませんでした: {pr_ref}"
            if labels_required:
                raise RuntimeError(message)
            self._log(f"WARNING: {message}")
            return

        resolved_labels = self.resolve_pr_labels_for_repo(
            repo_root=repo_root,
            repo_slug=repo_slug,
            labels=requested_labels,
        )
        if not resolved_labels:
            if labels_required:
                raise RuntimeError("PRラベルの解決に失敗しました。requested=" + ", ".join(requested_labels))
            return

        normalized_repo = self.normalize_repo_slug(repo_slug)
        for normalized in resolved_labels:
            if normalized_repo:
                proc = self._run_process(
                    [
                        "gh",
                        "api",
                        "-X",
                        "POST",
                        f"repos/{normalized_repo}/issues/{pr_number}/labels",
                        "-f",
                        f"labels[]={normalized}",
                    ],
                    cwd=repo_root,
                    check=False,
                )
            else:
                proc = self._run_process(
                    ["gh", "pr", "edit", pr_number, "--add-label", normalized],
                    cwd=repo_root,
                    check=False,
                )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                self._log(
                    "WARNING: PRラベル追加に失敗しました。"
                    f" pr={pr_ref} number={pr_number} label={normalized}"
                    + (f" detail={detail}" if detail else "")
                )

        current_labels = self.fetch_pr_label_names(
            repo_root=repo_root,
            repo_slug=repo_slug,
            pr_ref=pr_ref,
        )
        applied = [label for label in resolved_labels if label in current_labels]
        if labels_required and not applied:
            raise RuntimeError(
                "PRラベルの付与に失敗しました。"
                f" requested={requested_labels} resolved={resolved_labels}"
            )

    def create_or_update_pr(
        self,
        *,
        repo_root: Path,
        repo_slug: str,
        base_branch: str,
        branch_name: str,
        title: str,
        body_file: Path,
        labels: list[str],
        labels_required: bool,
        draft: bool,
    ) -> dict[str, str]:
        body_text = self._read_text(body_file)
        normalized_repo = self.normalize_repo_slug(repo_slug)

        if normalized_repo:
            owner, _ = self.split_repo_slug(normalized_repo)

            def parse_api_json(proc: subprocess.CompletedProcess[str], endpoint: str) -> Any:
                try:
                    return json.loads(proc.stdout or "null")
                except json.JSONDecodeError as err:
                    raise RuntimeError(f"GitHub API returned invalid JSON: {endpoint}") from err

            def find_open_pr_by_head() -> list[dict[str, Any]]:
                head_ref = quote(f"{owner}:{branch_name}", safe="")
                endpoint = f"repos/{normalized_repo}/pulls?state=open&head={head_ref}&per_page=100"
                payload = self._gh_api_json(endpoint=endpoint, cwd=repo_root)
                if not isinstance(payload, list):
                    return []
                result: list[dict[str, Any]] = []
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    number = item.get("number")
                    if not isinstance(number, int):
                        continue
                    result.append(
                        {
                            "number": str(number),
                            "url": str(item.get("html_url") or ""),
                            "isDraft": bool(item.get("draft", False)),
                        }
                    )
                return result

            def mark_pr_ready_for_review(pr_ref: str) -> None:
                endpoint = f"repos/{normalized_repo}/pulls/{pr_ref}/ready_for_review"
                proc = self._run_process(
                    [
                        "gh",
                        "api",
                        "-X",
                        "POST",
                        endpoint,
                    ],
                    cwd=repo_root,
                    check=False,
                )
                if proc.returncode == 0:
                    return
                detail = (proc.stderr or proc.stdout or "").strip()
                lowered = detail.lower()
                already_ready_markers = (
                    "already marked as ready for review",
                    "not in draft state",
                    "is not a draft",
                    "not a draft pull request",
                )
                if any(marker in lowered for marker in already_ready_markers):
                    return
                raise RuntimeError(
                    "PR を Draft 解除できませんでした。\n"
                    + (f"detail:\n{detail}" if detail else "")
                )

            current = find_open_pr_by_head()

            if current:
                number = str(current[0]["number"])
                endpoint = f"repos/{normalized_repo}/pulls/{number}"
                updated_proc = self._run_process(
                    [
                        "gh",
                        "api",
                        "-X",
                        "PATCH",
                        endpoint,
                        "-f",
                        f"title={title}",
                        "-f",
                        f"body={body_text}",
                    ],
                    cwd=repo_root,
                    check=True,
                )
                updated_payload = parse_api_json(updated_proc, endpoint)
                updated_url = ""
                updated_is_draft = bool(current[0].get("isDraft", False))
                if isinstance(updated_payload, dict):
                    updated_url = str(updated_payload.get("html_url") or "")
                    updated_is_draft = bool(updated_payload.get("draft", updated_is_draft))

                self.add_labels_to_pr(
                    repo_root=repo_root,
                    repo_slug=normalized_repo,
                    pr_ref=number,
                    labels=labels,
                    labels_required=labels_required,
                )
                if not draft and updated_is_draft:
                    mark_pr_ready_for_review(number)
                pr_url = updated_url or str(
                    current[0].get("url") or f"https://github.com/{normalized_repo}/pull/{number}"
                )
                self._log(f"Updated existing PR: {pr_url}")
                return {
                    "url": pr_url,
                    "number": number,
                    "action": "updated",
                }

            endpoint = f"repos/{normalized_repo}/pulls"
            create_cmd = [
                "gh",
                "api",
                "-X",
                "POST",
                endpoint,
                "-f",
                f"title={title}",
                "-f",
                f"head={branch_name}",
                "-f",
                f"base={base_branch}",
                "-f",
                f"body={body_text}",
            ]
            if draft:
                create_cmd.extend(["-F", "draft=true"])

            created_proc = self._run_process(create_cmd, cwd=repo_root, check=True)
            created_payload = parse_api_json(created_proc, endpoint)
            pr_ref_for_label = ""
            created_pr_is_draft = False
            pr_url = ""
            if isinstance(created_payload, dict):
                number = created_payload.get("number")
                if isinstance(number, int):
                    pr_ref_for_label = str(number)
                pr_url = str(created_payload.get("html_url") or "")
                created_pr_is_draft = bool(created_payload.get("draft", False))

            if not pr_ref_for_label:
                current_after_create = find_open_pr_by_head()
                if current_after_create:
                    pr_ref_for_label = str(current_after_create[0]["number"])
                    if not pr_url:
                        pr_url = str(current_after_create[0].get("url", ""))
                    created_pr_is_draft = bool(current_after_create[0].get("isDraft", created_pr_is_draft))

            if not pr_ref_for_label:
                raise RuntimeError("作成したPR番号を解決できませんでした。")

            self.add_labels_to_pr(
                repo_root=repo_root,
                repo_slug=normalized_repo,
                pr_ref=pr_ref_for_label,
                labels=labels,
                labels_required=labels_required,
            )
            if not draft and created_pr_is_draft:
                mark_pr_ready_for_review(pr_ref_for_label)
            if not pr_url:
                pr_url = f"https://github.com/{normalized_repo}/pull/{pr_ref_for_label}"
            self._log(f"Created PR: {pr_url}")
            return {
                "url": pr_url,
                "number": str(pr_ref_for_label),
                "action": "created",
            }

        repo_args = ["--repo", normalized_repo] if normalized_repo else []

        def mark_pr_ready_for_review_legacy(pr_ref: str) -> None:
            proc = self._run_process(
                ["gh", "pr", "ready", pr_ref, *repo_args],
                cwd=repo_root,
                check=False,
            )
            if proc.returncode == 0:
                return
            detail = (proc.stderr or proc.stdout or "").strip()
            lowered = detail.lower()
            already_ready_markers = (
                "already marked as ready for review",
                "not in draft state",
                "is not a draft",
            )
            if any(marker in lowered for marker in already_ready_markers):
                return
            raise RuntimeError(
                "PR を Draft 解除できませんでした。\n"
                + (f"detail:\n{detail}" if detail else "")
            )

        def find_open_pr_by_head_legacy() -> list[dict[str, Any]]:
            existing = self._run_process(
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
                    "number,url,isDraft",
                ],
                cwd=repo_root,
                check=True,
            )
            loaded = json.loads(existing.stdout or "[]")
            if not isinstance(loaded, list):
                return []
            return [item for item in loaded if isinstance(item, dict)]

        current = find_open_pr_by_head_legacy()
        if current:
            number = str(current[0]["number"])
            is_draft = bool(current[0].get("isDraft", False))
            self._run_process(
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
            self.add_labels_to_pr(
                repo_root=repo_root,
                repo_slug=normalized_repo,
                pr_ref=number,
                labels=labels,
                labels_required=labels_required,
            )
            if not draft and is_draft:
                mark_pr_ready_for_review_legacy(number)
            pr_url = str(current[0].get("url") or "")
            self._log(f"Updated existing PR: {pr_url}")
            return {
                "url": pr_url,
                "number": number,
                "action": "updated",
            }

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

        created = self._run_process(cmd, cwd=repo_root, check=True)
        pr_url = created.stdout.strip().splitlines()[-1]
        current_after_create = find_open_pr_by_head_legacy()
        pr_ref_for_label = pr_url
        created_pr_is_draft = False
        if current_after_create:
            pr_ref_for_label = str(current_after_create[0]["number"])
            pr_url = str(current_after_create[0].get("url", pr_url))
            created_pr_is_draft = bool(current_after_create[0].get("isDraft", False))
        self.add_labels_to_pr(
            repo_root=repo_root,
            repo_slug=normalized_repo,
            pr_ref=pr_ref_for_label,
            labels=labels,
            labels_required=labels_required,
        )
        if not draft and created_pr_is_draft:
            mark_pr_ready_for_review_legacy(str(pr_ref_for_label))
        self._log(f"Created PR: {pr_url}")
        return {
            "url": pr_url,
            "number": self.resolve_pr_number(pr_ref_for_label) or self.resolve_pr_number(pr_url),
            "action": "created",
        }

