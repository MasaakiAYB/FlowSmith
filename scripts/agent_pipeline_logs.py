#!/usr/bin/env python3
"""AI logs save/publish operations for agent pipeline."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable


class PipelineAiLogsService:
    """Encapsulates ai-logs bundle and dedicated-branch publish operations."""

    def __init__(
        self,
        *,
        normalize_repo_path: Callable[[str], str],
        format_template: Callable[..., str],
        resolve_repo_relative_path: Callable[..., Path],
        resolve_ui_artifact_dir_from_config: Callable[[dict[str, Any]], str],
        resolve_ui_repo_evidence_dir: Callable[..., tuple[str, Path]],
        resolve_ui_image_extensions_from_config: Callable[[dict[str, Any]], list[str]],
        to_evidence_filename: Callable[..., str],
        write_text: Callable[[Path, str], None],
        log: Callable[[str], None],
        git: Callable[..., Any],
    ) -> None:
        self._normalize_repo_path = normalize_repo_path
        self._format_template = format_template
        self._resolve_repo_relative_path = resolve_repo_relative_path
        self._resolve_ui_artifact_dir_from_config = resolve_ui_artifact_dir_from_config
        self._resolve_ui_repo_evidence_dir = resolve_ui_repo_evidence_dir
        self._resolve_ui_image_extensions_from_config = resolve_ui_image_extensions_from_config
        self._to_evidence_filename = to_evidence_filename
        self._write_text = write_text
        self._log = log
        self._git = git

    def save_ai_logs_bundle(
        self,
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
            "ai_logs_published_paths": [],
        }
        if not enabled:
            self._write_text(run_dir / "ai_logs_status.md", "- ai-logs 保存は無効です。\n")
            return default_state

        try:
            dir_relative_path = self._format_template(
                dir_template,
                context,
                "ai_logs.path",
            ).strip().rstrip("/")
            if not dir_relative_path:
                raise RuntimeError("ai_logs.path が空です。")
            logs_dir_path = self._resolve_repo_relative_path(
                dir_relative_path,
                repo_root=repo_root,
                setting_name="ai_logs.path",
            )

            source_files = sorted(path for path in run_dir.rglob("*") if path.is_file())
            if not source_files:
                raise RuntimeError(f"ai-logs に保存するソースファイルがありません: {run_dir}")

            copied_relative_paths: list[str] = []
            for source in source_files:
                relative_tail = source.relative_to(run_dir)
                destination = logs_dir_path / relative_tail
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied_relative_paths.append(
                    self._normalize_repo_path(str(Path(dir_relative_path) / relative_tail))
                )

            # commit前に run_dir/ui-evidence が未生成のケースに備え、repo側のUI証跡も ai-logs に取り込む。
            ui_artifact_dir = self._resolve_ui_artifact_dir_from_config(config)
            ui_artifact_prefix = (
                self._normalize_repo_path(str(Path(dir_relative_path) / ui_artifact_dir)).rstrip("/") + "/"
            ).lower()
            has_ui_evidence_in_logs = any(
                path.lower().startswith(ui_artifact_prefix) for path in copied_relative_paths
            )
            if not has_ui_evidence_in_logs:
                ui_conf_raw = config.get("ui_evidence", {})
                if ui_conf_raw is None:
                    ui_conf_raw = {}
                if not isinstance(ui_conf_raw, dict):
                    raise RuntimeError("Config 'ui_evidence' must be an object when specified.")
                _, ui_repo_evidence_dir = self._resolve_ui_repo_evidence_dir(
                    repo_root=repo_root,
                    ui_conf_raw=ui_conf_raw,
                )
                allowed_extensions = set(self._resolve_ui_image_extensions_from_config(config))
                used_names: set[str] = set()
                ui_logs_dir = logs_dir_path / ui_artifact_dir
                for file_path in sorted(ui_logs_dir.glob("*")):
                    if file_path.is_file():
                        used_names.add(file_path.name)

                if ui_repo_evidence_dir.exists():
                    for source in sorted(ui_repo_evidence_dir.rglob("*")):
                        if not source.is_file():
                            continue
                        if source.suffix.lower() not in allowed_extensions:
                            continue
                        relative_source = self._normalize_repo_path(source.relative_to(repo_root).as_posix())
                        file_name = self._to_evidence_filename(relative_source, used_names=used_names)
                        destination = ui_logs_dir / file_name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
                        copied_relative_paths.append(
                            self._normalize_repo_path(str(Path(dir_relative_path) / ui_artifact_dir / file_name))
                        )

            index_relative_path = self._normalize_repo_path(str(Path(dir_relative_path) / index_file_name))
            index_path = self._resolve_repo_relative_path(
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
            self._write_text(index_path, index_content)
            if index_relative_path not in copied_relative_paths:
                copied_relative_paths.append(index_relative_path)

        except (RuntimeError, OSError) as err:
            if required:
                raise RuntimeError(f"ai-logs 保存に失敗しました: {err}") from err
            message = f"ai-logs 保存をスキップしました: {err}"
            self._log(f"WARNING: {message}")
            self._write_text(run_dir / "ai_logs_status.md", f"- {message}\n")
            return default_state

        copied_relative_paths = sorted(set(copied_relative_paths))
        self._write_text(
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
            "ai_logs_dir": self._normalize_repo_path(dir_relative_path),
            "ai_logs_index_file": index_relative_path,
            "ai_logs_file_count": len(copied_relative_paths),
            "ai_logs_paths": copied_relative_paths,
        }

    def resolve_ai_logs_publish_settings(
        self,
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
        self,
        *,
        repo_root: Path,
        relative_paths: list[str],
    ) -> None:
        normalized_paths = sorted(
            {
                self._normalize_repo_path(str(item))
                for item in relative_paths
                if str(item).strip()
            }
        )
        for relative_path in normalized_paths:
            resolved = self._resolve_repo_relative_path(
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
        self,
        *,
        repo_root: Path,
        run_dir: Path,
        config: dict[str, Any],
        context: dict[str, Any],
        repo_slug: str,
    ) -> dict[str, Any]:
        ai_logs_required = bool(context.get("ai_logs_required", True))
        publish_settings = self.resolve_ai_logs_publish_settings(
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
            "ai_logs_published_paths": [],
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
                self._normalize_repo_path(str(item))
                for item in ai_logs_paths_raw
                if str(item).strip()
            }
        )
        if not ai_logs_paths:
            message = "ai-logs ファイル一覧が空のため dedicated-branch へ保存できません。"
            if required:
                raise RuntimeError(message)
            self._log(f"WARNING: {message}")
            return {
                **default_state,
                "ai_logs_publish_status": "failed",
            }

        worktree_dir = Path(tempfile.mkdtemp(prefix="flowsmith-ai-logs-"))
        worktree_added = False
        published_commit = ""
        try:
            self._git(["fetch", "origin", branch_name], cwd=repo_root, check=False)
            self._git(["worktree", "add", "--detach", str(worktree_dir), "HEAD"], cwd=repo_root)
            worktree_added = True

            remote_exists = (
                self._git(
                    ["ls-remote", "--exit-code", "--heads", "origin", branch_name],
                    cwd=worktree_dir,
                    check=False,
                ).returncode
                == 0
            )
            if remote_exists:
                self._git(["fetch", "origin", branch_name], cwd=worktree_dir, check=False)
                self._git(["checkout", "-B", branch_name, f"origin/{branch_name}"], cwd=worktree_dir)
            else:
                self._git(["checkout", "-B", branch_name], cwd=worktree_dir)

            for relative_path in ai_logs_paths:
                source = self._resolve_repo_relative_path(
                    relative_path,
                    repo_root=repo_root,
                    setting_name="ai_logs.path",
                )
                if not source.exists():
                    raise RuntimeError(
                        f"ai-logs 保存対象ファイルが見つかりません: {relative_path}"
                    )
                destination = self._resolve_repo_relative_path(
                    relative_path,
                    repo_root=worktree_dir,
                    setting_name="ai_logs.path",
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            # ai-logs は対象リポジトリで ignore されている場合があるため強制追加する。
            self._git(["add", "-f", "--", *ai_logs_paths], cwd=worktree_dir)
            has_changes = self._git(["diff", "--cached", "--quiet"], cwd=worktree_dir, check=False)
            if has_changes.returncode != 0:
                publish_message = self._format_template(
                    commit_message_template,
                    context,
                    "ai_logs.publish.commit_message",
                )
                self._git(["commit", "-m", publish_message], cwd=worktree_dir)

            push_proc = self._git(
                ["push", "-u", "origin", branch_name],
                cwd=worktree_dir,
                check=False,
            )
            if push_proc.returncode != 0:
                push_stderr = push_proc.stderr or ""
                if "non-fast-forward" in push_stderr:
                    self._git(["pull", "--rebase", "origin", branch_name], cwd=worktree_dir, check=True)
                    self._git(["push", "-u", "origin", branch_name], cwd=worktree_dir, check=True)
                else:
                    raise RuntimeError(
                        "ai-logs dedicated-branch への push に失敗しました。\n"
                        f"stderr:\n{push_stderr}"
                    )

            published_commit = self._git(["rev-parse", "HEAD"], cwd=worktree_dir).stdout.strip()
        except (RuntimeError, OSError) as err:
            if required:
                raise RuntimeError(
                    f"ai-logs dedicated-branch 保存に失敗しました: {err}"
                ) from err
            message = f"ai-logs dedicated-branch 保存をスキップしました: {err}"
            self._log(f"WARNING: {message}")
            self._write_text(run_dir / "ai_logs_publish_status.md", f"- {message}\n")
            return {
                **default_state,
                "ai_logs_publish_status": "failed",
            }
        finally:
            if worktree_added:
                self._git(["worktree", "remove", "--force", str(worktree_dir)], cwd=repo_root, check=False)
            shutil.rmtree(worktree_dir, ignore_errors=True)

        self.remove_ai_log_paths_from_worktree(
            repo_root=repo_root,
            relative_paths=ai_logs_paths,
        )
        self._write_text(
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
            "ai_logs_published_paths": ai_logs_paths,
            # コード変更用ブランチには ai-logs を含めない。
            "ai_logs_paths": [],
        }
