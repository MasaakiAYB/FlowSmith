#!/usr/bin/env python3
"""Runtime/config resolution for agent pipeline."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


class PipelineRuntimeService:
    """Encapsulates target-repo preparation and runtime config resolution."""

    def __init__(
        self,
        *,
        default_config_path: Path,
        default_projects_path: Path,
        resolve_path: Callable[..., Path],
        load_json: Callable[[Path], dict[str, Any]],
        validate_config: Callable[[dict[str, Any], Path], None],
        merge_dict: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        slugify: Callable[..., str],
        normalize_repo_slug: Callable[[str], str],
        detect_repo_slug: Callable[[Path], str],
        git: Callable[..., Any],
        run_process: Callable[..., Any],
    ) -> None:
        self._default_config_path = default_config_path
        self._default_projects_path = default_projects_path
        self._resolve_path = resolve_path
        self._load_json = load_json
        self._validate_config = validate_config
        self._merge_dict = merge_dict
        self._slugify = slugify
        self._normalize_repo_slug = normalize_repo_slug
        self._detect_repo_slug = detect_repo_slug
        self._git = git
        self._run_process = run_process

    def prepare_target_repo(
        self,
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
                self._git(["fetch", "--all", "--prune"], cwd=target_repo_root, check=False)
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
        self._run_process(
            ["git", "clone", effective_clone_url, str(target_repo_root)],
            check=True,
        )

    def load_project_manifest(self, path: Path) -> dict[str, Any]:
        payload = self._load_json(path)
        projects = payload.get("projects")
        if not isinstance(projects, dict):
            raise RuntimeError(f"Invalid projects manifest ({path}): 'projects' must be an object.")
        return payload

    def resolve_runtime(
        self,
        *,
        control_root: Path,
        args: argparse.Namespace,
    ) -> dict[str, Any]:
        base_config_path = self._resolve_path(args.config, base_dir=control_root)
        base_config = self._load_json(base_config_path)
        self._validate_config(base_config, base_config_path)

        target_repo_root = (
            self._resolve_path(args.target_path, base_dir=control_root)
            if args.target_path
            else control_root
        )
        project_id = ""
        repo_slug = self._normalize_repo_slug(args.target_repo or "")
        default_base_branch = ""
        config_base_dir = base_config_path.parent
        config_validation_path = base_config_path
        config = deepcopy(base_config)

        if args.project:
            project_id = args.project
            manifest_path = self._resolve_path(args.projects_file, base_dir=control_root)
            manifest = self.load_project_manifest(manifest_path)

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
            workspace_root = self._resolve_path(workspace_root_value, base_dir=manifest_path.parent)

            if not args.target_path:
                local_path_value = project.get("local_path")
                if local_path_value:
                    target_repo_root = self._resolve_path(local_path_value, base_dir=manifest_path.parent)
                else:
                    target_repo_root = (workspace_root / self._slugify(project_id, max_len=80)).resolve()

            repo_slug = self._normalize_repo_slug(args.target_repo or project.get("repo", ""))
            clone_url = str(project.get("clone_url", "")).strip()

            self.prepare_target_repo(
                target_repo_root=target_repo_root,
                clone_url=clone_url,
                repo_slug=repo_slug,
                sync_target=not args.no_sync,
            )

            if not repo_slug:
                repo_slug = self._detect_repo_slug(target_repo_root)

            project_config_value = project.get("config")
            if project_config_value:
                project_config_path = self._resolve_path(project_config_value, base_dir=manifest_path.parent)
                project_config = self._load_json(project_config_path)
                config = self._merge_dict(config, project_config)
                config_base_dir = project_config_path.parent
                config_validation_path = project_config_path

            inline_overrides = project.get("overrides")
            if isinstance(inline_overrides, dict):
                config = self._merge_dict(config, inline_overrides)

            default_base_branch = str(project.get("base_branch", "")).strip()
        else:
            if repo_slug:
                if not args.target_path:
                    target_repo_root = (
                        control_root / ".agent" / "workspaces" / self._slugify(repo_slug, max_len=80)
                    ).resolve()
                self.prepare_target_repo(
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
                repo_slug = self._detect_repo_slug(target_repo_root)

            target_defaults_raw = config.get("target_repo_defaults")
            if target_defaults_raw is None:
                target_defaults_raw = {}
            if not isinstance(target_defaults_raw, dict):
                raise RuntimeError("Config 'target_repo_defaults' must be an object when specified.")

            if target_defaults_raw and (
                bool(args.target_repo) or bool(args.target_path and target_repo_root != control_root)
            ):
                config = self._merge_dict(config, target_defaults_raw)

        self._validate_config(config, config_validation_path)

        run_namespace = self._slugify(project_id or repo_slug or target_repo_root.name, max_len=80)
        return {
            "config": config,
            "config_base_dir": config_base_dir,
            "target_repo_root": target_repo_root,
            "project_id": project_id,
            "repo_slug": repo_slug,
            "default_base_branch": default_base_branch,
            "run_namespace": run_namespace,
        }

    def parse_args(self) -> argparse.Namespace:
        parser = argparse.ArgumentParser(description="Run autonomous issue-to-PR pipeline.")
        parser.add_argument("--issue-number", type=int, required=True)
        parser.add_argument("--issue-file", type=Path, default=None)
        parser.add_argument(
            "--feedback-pr-number",
            type=int,
            default=0,
            help="Optional: PR number to extract actionable feedback from",
        )
        parser.add_argument(
            "--feedback-max-items",
            type=int,
            default=20,
            help="Maximum actionable feedback items to import from PR comments/reviews",
        )
        parser.add_argument(
            "--feedback-file",
            type=Path,
            default=None,
            help="Optional markdown/text file with additional feedback",
        )
        parser.add_argument(
            "--feedback-text",
            default="",
            help="Optional direct feedback text",
        )
        parser.add_argument("--config", type=Path, default=self._default_config_path)
        parser.add_argument("--project", default=None, help="Project id in projects manifest")
        parser.add_argument("--projects-file", type=Path, default=self._default_projects_path)
        parser.add_argument("--target-repo", default=None, help="GitHub repo slug (owner/repo)")
        parser.add_argument("--target-path", type=Path, default=None, help="Local target repository path")
        parser.add_argument("--no-sync", action="store_true", help="Skip fetch/pull before branch work")
        parser.add_argument("--base-branch", default=None)
        parser.add_argument("--branch-name", default=None)
        parser.add_argument("--push", action="store_true")
        parser.add_argument("--create-pr", action="store_true")
        parser.add_argument(
            "--allow-no-changes",
            action="store_true",
            help="No-op（差分なし）を成功扱いにし、commit/push/PRをスキップする",
        )
        return parser.parse_args()
