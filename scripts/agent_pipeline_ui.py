#!/usr/bin/env python3
"""UI evidence operations for agent pipeline."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable


class PipelineUiEvidenceService:
    """Encapsulates UI evidence discovery, packaging, and rendering."""

    def __init__(
        self,
        *,
        normalize_repo_path: Callable[[str], str],
        parse_string_list: Callable[..., list[str]],
        parse_positive_int: Callable[..., int],
        resolve_repo_relative_path: Callable[..., Path],
        normalize_repo_slug: Callable[[str], str],
        slugify: Callable[..., str],
        git: Callable[..., Any],
        log: Callable[[str], None],
    ) -> None:
        self._normalize_repo_path = normalize_repo_path
        self._parse_string_list = parse_string_list
        self._parse_positive_int = parse_positive_int
        self._resolve_repo_relative_path = resolve_repo_relative_path
        self._normalize_repo_slug = normalize_repo_slug
        self._slugify = slugify
        self._git = git
        self._log = log

    def normalize_extensions(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for item in values:
            text = str(item).strip().lower()
            if not text:
                continue
            if not text.startswith("."):
                text = "." + text
            result.append(text)
        return sorted(set(result))

    def resolve_run_dir_subpath(
        self,
        *,
        run_dir: Path,
        value: str,
        setting_name: str,
    ) -> tuple[str, Path]:
        relative = Path(value)
        if relative.is_absolute():
            raise RuntimeError(f"Config '{setting_name}' must be a relative path.")
        normalized = self._normalize_repo_path(relative.as_posix())
        if not normalized:
            raise RuntimeError(f"Config '{setting_name}' must not be empty.")
        resolved = (run_dir / normalized).resolve()
        try:
            resolved.relative_to(run_dir)
        except ValueError as err:
            raise RuntimeError(
                f"Config '{setting_name}' points outside run_dir: {value}"
            ) from err
        return normalized, resolved

    def resolve_ui_repo_evidence_dir(
        self,
        *,
        repo_root: Path,
        ui_conf_raw: dict[str, Any],
    ) -> tuple[str, Path]:
        raw_value = str(ui_conf_raw.get("repo_dir") or ".flowsmith/ui-evidence").strip()
        if not raw_value:
            raw_value = ".flowsmith/ui-evidence"
        relative = Path(raw_value)
        if relative.is_absolute():
            raise RuntimeError("Config 'ui_evidence.repo_dir' must be a relative path.")
        normalized = self._normalize_repo_path(relative.as_posix())
        if not normalized:
            raise RuntimeError("Config 'ui_evidence.repo_dir' must not be empty.")
        resolved = self._resolve_repo_relative_path(
            normalized,
            repo_root=repo_root,
            setting_name="ui_evidence.repo_dir",
        )
        return normalized, resolved

    def resolve_ui_artifact_dir_from_config(self, config: dict[str, Any]) -> str:
        ui_conf_raw = config.get("ui_evidence", {})
        if ui_conf_raw is None:
            ui_conf_raw = {}
        if not isinstance(ui_conf_raw, dict):
            return "ui-evidence"
        raw_value = str(ui_conf_raw.get("artifact_dir") or "ui-evidence").strip()
        if not raw_value:
            raw_value = "ui-evidence"
        return self._normalize_repo_path(Path(raw_value).as_posix()).strip("/")

    def resolve_ui_image_extensions_from_config(self, config: dict[str, Any]) -> list[str]:
        ui_conf_raw = config.get("ui_evidence", {})
        if ui_conf_raw is None:
            ui_conf_raw = {}
        if not isinstance(ui_conf_raw, dict):
            return [".png", ".jpg", ".jpeg", ".webp", ".gif"]
        return self.normalize_extensions(
            self._parse_string_list(
                ui_conf_raw.get("image_extensions"),
                default=[".png", ".jpg", ".jpeg", ".webp", ".gif"],
                name="ui_evidence.image_extensions",
            )
            or [".png", ".jpg", ".jpeg", ".webp", ".gif"]
        )

    def build_blob_url(self, *, repo_slug: str, ref: str, path: str) -> str:
        normalized_repo = self._normalize_repo_slug(repo_slug)
        normalized_ref = str(ref).strip()
        normalized_path = self._normalize_repo_path(path).strip()
        if not normalized_repo or not normalized_ref or not normalized_path:
            return ""
        return f"https://github.com/{normalized_repo}/blob/{normalized_ref}/{normalized_path}"

    @staticmethod
    def to_raw_blob_url(url: str) -> str:
        text = str(url).strip()
        if not text:
            return ""
        if "?" in text:
            return text + "&raw=1"
        return text + "?raw=1"

    def build_ui_evidence_ai_logs_context(
        self,
        *,
        context: dict[str, Any],
        config: dict[str, Any],
        repo_slug: str,
    ) -> dict[str, Any]:
        default_state = {
            "ui_evidence_ai_logs_branch": "",
            "ui_evidence_ai_logs_paths": [],
            "ui_evidence_ai_logs_urls": [],
            "ui_evidence_ai_logs_links_markdown": "- `(なし)`",
            "ui_evidence_ai_logs_embeds_markdown": "_画像はありません。_",
        }

        ai_logs_dir = self._normalize_repo_path(str(context.get("ai_logs_dir", "")).strip())
        if not ai_logs_dir or ai_logs_dir == "未保存":
            return default_state

        source_paths: list[str] = []
        for key in ("ai_logs_published_paths", "ai_logs_paths"):
            raw = context.get(key, [])
            if not isinstance(raw, list):
                continue
            for item in raw:
                text = self._normalize_repo_path(str(item).strip())
                if text:
                    source_paths.append(text)
        if not source_paths:
            return default_state

        ui_artifact_dir = self.resolve_ui_artifact_dir_from_config(config)
        prefix = self._normalize_repo_path(str(Path(ai_logs_dir) / ui_artifact_dir)).rstrip("/") + "/"
        prefix_lower = prefix.lower()
        allowed_extensions = set(self.resolve_ui_image_extensions_from_config(config))
        ui_paths = sorted(
            {
                path
                for path in source_paths
                if path.lower().startswith(prefix_lower)
                and Path(path).suffix.lower() in allowed_extensions
            }
        )
        if not ui_paths:
            return default_state

        ai_logs_publish_mode = str(context.get("ai_logs_publish_mode", "")).strip().lower()
        ai_logs_branch = (
            str(context.get("ai_logs_publish_branch", "")).strip()
            if ai_logs_publish_mode == "dedicated-branch"
            else ""
        )
        if not ai_logs_branch:
            ai_logs_branch = str(context.get("head_commit", "")).strip()

        urls: list[str] = []
        if repo_slug and ai_logs_branch:
            for path in ui_paths:
                url = self.build_blob_url(repo_slug=repo_slug, ref=ai_logs_branch, path=path)
                if url:
                    urls.append(url)

        links_markdown = (
            "\n".join(f"- [{Path(path).name}]({url})" for path, url in zip(ui_paths, urls))
            if urls
            else "\n".join(f"- `{path}`" for path in ui_paths)
        )
        if not links_markdown:
            links_markdown = "- `(なし)`"

        embed_urls = [converted for converted in (self.to_raw_blob_url(url) for url in urls[:4]) if converted]
        embeds_markdown = "\n".join(f"![UI Evidence]({url})" for url in embed_urls)
        if not embeds_markdown:
            embeds_markdown = "_画像はありません。_"

        return {
            **default_state,
            "ui_evidence_ai_logs_branch": ai_logs_branch,
            "ui_evidence_ai_logs_paths": ui_paths,
            "ui_evidence_ai_logs_urls": urls,
            "ui_evidence_ai_logs_links_markdown": links_markdown,
            "ui_evidence_ai_logs_embeds_markdown": embeds_markdown,
        }

    @staticmethod
    def matches_any_keyword(value: str, keywords: list[str]) -> bool:
        text = value.lower()
        return any(keyword in text for keyword in keywords if keyword)

    @staticmethod
    def detect_workflow_artifact_metadata() -> dict[str, str]:
        repository = os.getenv("GITHUB_REPOSITORY", "").strip()
        run_id = os.getenv("GITHUB_RUN_ID", "").strip()
        run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "").strip()
        server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com").strip() or "https://github.com"
        run_url = ""
        if repository and run_id:
            run_url = f"{server_url}/{repository}/actions/runs/{run_id}"
        artifact_name = os.getenv("FLOWSMITH_RUN_ARTIFACT_NAME", "").strip()
        if not artifact_name and run_id:
            suffix = run_attempt or "1"
            artifact_name = f"agent-run-{run_id}-{suffix}"
        artifact_url = f"{run_url}#artifacts" if run_url else ""
        return {
            "workflow_run_url": run_url,
            "run_artifact_name": artifact_name,
            "run_artifact_url": artifact_url,
        }

    def collect_run_dir_evidence_images(
        self,
        *,
        evidence_dir: Path,
        evidence_dir_relative: str,
        image_extensions: list[str],
    ) -> list[str]:
        if not evidence_dir.exists():
            return []
        allowed = set(image_extensions)
        relative_paths: list[str] = []
        for file_path in sorted(evidence_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in allowed:
                continue
            relative_tail = file_path.relative_to(evidence_dir)
            relative_paths.append(
                self._normalize_repo_path(str(Path(evidence_dir_relative) / relative_tail))
            )
        return relative_paths

    def collect_repo_evidence_images(
        self,
        *,
        changed_image_paths: list[str],
        evidence_path_keywords: list[str],
        evidence_name_keywords: list[str],
    ) -> list[str]:
        evidence_paths: list[str] = []
        for path in changed_image_paths:
            lowered = path.lower()
            file_name = Path(lowered).name
            if self.matches_any_keyword(lowered, evidence_path_keywords) or self.matches_any_keyword(
                file_name, evidence_name_keywords
            ):
                evidence_paths.append(path)
        return sorted(set(evidence_paths))

    def collect_repo_dir_evidence_images(
        self,
        *,
        repo_root: Path,
        repo_evidence_dir: Path,
        image_extensions: list[str],
    ) -> list[str]:
        if not repo_evidence_dir.exists():
            return []
        allowed = set(image_extensions)
        evidence_paths: list[str] = []
        for file_path in sorted(repo_evidence_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in allowed:
                continue
            try:
                relative = file_path.resolve().relative_to(repo_root.resolve())
            except ValueError:
                continue
            evidence_paths.append(self._normalize_repo_path(relative.as_posix()))
        return sorted(set(evidence_paths))

    def to_evidence_filename(self, path: str, *, used_names: set[str]) -> str:
        raw = Path(path)
        suffix = raw.suffix.lower()
        stem = self._slugify(raw.stem or raw.name, max_len=50)
        candidate = f"{stem}{suffix}"
        index = 2
        while candidate in used_names:
            candidate = f"{stem}-{index}{suffix}"
            index += 1
        used_names.add(candidate)
        return candidate

    def copy_repo_evidence_images_to_run_dir(
        self,
        *,
        repo_root: Path,
        source_paths: list[str],
        evidence_dir: Path,
        evidence_dir_relative: str,
    ) -> list[str]:
        if not source_paths:
            return []
        evidence_dir.mkdir(parents=True, exist_ok=True)
        used_names = {item.name for item in evidence_dir.iterdir() if item.is_file()}
        copied_relative_paths: list[str] = []
        for relative_source in source_paths:
            source = self._resolve_repo_relative_path(
                relative_source,
                repo_root=repo_root,
                setting_name="ui_evidence.evidence_images",
            )
            if not source.is_file():
                continue
            name = self.to_evidence_filename(relative_source, used_names=used_names)
            destination = evidence_dir / name
            shutil.copy2(source, destination)
            copied_relative_paths.append(
                self._normalize_repo_path(str(Path(evidence_dir_relative) / name))
            )
        return copied_relative_paths

    def restore_paths_after_evidence_copy(
        self,
        *,
        repo_root: Path,
        relative_paths: list[str],
    ) -> list[str]:
        removed: list[str] = []
        for relative_path in sorted(
            {
                self._normalize_repo_path(str(item))
                for item in relative_paths
                if str(item).strip()
            }
        ):
            self._git(
                ["restore", "--staged", "--worktree", "--source=HEAD", "--", relative_path],
                cwd=repo_root,
                check=False,
            )
            tracked = (
                self._git(
                    ["ls-files", "--error-unmatch", "--", relative_path],
                    cwd=repo_root,
                    check=False,
                ).returncode
                == 0
            )
            if tracked:
                removed.append(relative_path)
                continue
            resolved = self._resolve_repo_relative_path(
                relative_path,
                repo_root=repo_root,
                setting_name="ui_evidence.evidence_images",
            )
            if resolved.is_file():
                resolved.unlink()
                removed.append(relative_path)
                continue
            if resolved.is_dir():
                shutil.rmtree(resolved, ignore_errors=True)
                removed.append(relative_path)
        return removed

    def build_ui_evidence_state(
        self,
        *,
        repo_root: Path,
        run_dir: Path,
        changed_paths: list[str],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ui_conf_raw = config.get("ui_evidence", {})
        if ui_conf_raw is None:
            ui_conf_raw = {}
        if not isinstance(ui_conf_raw, dict):
            raise RuntimeError("Config 'ui_evidence' must be an object when specified.")
        repo_evidence_dir_relative, repo_evidence_dir = self.resolve_ui_repo_evidence_dir(
            repo_root=repo_root,
            ui_conf_raw=ui_conf_raw,
        )

        enabled = bool(ui_conf_raw.get("enabled", True))
        required = bool(ui_conf_raw.get("required", True))
        default_ui_extensions = [
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".jsx",
            ".tsx",
            ".vue",
            ".svelte",
            ".astro",
        ]
        default_ui_path_keywords = [
            "/components/",
            "/pages/",
            "/ui/",
            "/frontend/",
            "/web/",
            "/client/",
        ]
        default_image_extensions = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
        default_evidence_path_keywords = [
            "/ui-evidence/",
            "/screenshots/",
            "/docs/images/ui",
            "/docs/images/screenshot",
            "/docs/images/capture",
        ]
        default_evidence_name_keywords = [
            "screenshot",
            "snapshot",
            "capture",
            "screen-",
            "screen_",
            "ui-",
            "ui_",
        ]

        ui_extensions = self.normalize_extensions(
            self._parse_string_list(
                ui_conf_raw.get("ui_extensions"),
                default=default_ui_extensions,
                name="ui_evidence.ui_extensions",
            )
            or default_ui_extensions
        )
        ui_path_keywords = [
            self._normalize_repo_path(item).lower()
            for item in self._parse_string_list(
                ui_conf_raw.get("ui_path_keywords"),
                default=default_ui_path_keywords,
                name="ui_evidence.ui_path_keywords",
            )
        ]
        image_extensions = self.normalize_extensions(
            self._parse_string_list(
                ui_conf_raw.get("image_extensions"),
                default=default_image_extensions,
                name="ui_evidence.image_extensions",
            )
            or default_image_extensions
        )
        evidence_path_keywords = [
            self._normalize_repo_path(item).lower()
            for item in self._parse_string_list(
                ui_conf_raw.get("evidence_path_keywords"),
                default=default_evidence_path_keywords,
                name="ui_evidence.evidence_path_keywords",
            )
        ]
        evidence_name_keywords = [
            str(item).strip().lower()
            for item in self._parse_string_list(
                ui_conf_raw.get("evidence_name_keywords"),
                default=default_evidence_name_keywords,
                name="ui_evidence.evidence_name_keywords",
            )
            if str(item).strip()
        ]
        max_ui_files = self._parse_positive_int(
            ui_conf_raw.get("max_ui_files"),
            default=8,
            name="ui_evidence.max_ui_files",
        )
        max_images = self._parse_positive_int(
            ui_conf_raw.get("max_images"),
            default=5,
            name="ui_evidence.max_images",
        )
        delivery_mode = str(
            ui_conf_raw.get("delivery_mode", "artifact-only")
        ).strip().lower() or "artifact-only"
        if delivery_mode not in {"artifact-only", "commit"}:
            raise RuntimeError(
                "Config 'ui_evidence.delivery_mode' must be one of: artifact-only, commit."
            )
        evidence_dir_relative, evidence_dir = self.resolve_run_dir_subpath(
            run_dir=run_dir,
            value=str(ui_conf_raw.get("artifact_dir") or "ui-evidence"),
            setting_name="ui_evidence.artifact_dir",
        )

        default_state = {
            "ui_evidence_enabled": enabled,
            "ui_evidence_required": required,
            "ui_evidence_status": "skipped",
            "ui_evidence_delivery_mode": delivery_mode,
            "ui_evidence_artifact_dir": evidence_dir_relative,
            "ui_evidence_repo_dir": repo_evidence_dir_relative,
            "ui_evidence_artifact_name": "",
            "ui_evidence_artifact_url": "",
            "ui_evidence_workflow_run_url": "",
            "ui_evidence_ui_files": [],
            "ui_evidence_image_files": [],
            "ui_evidence_commit_image_files": [],
            "ui_evidence_restored_paths": [],
            "ui_evidence_appendix": "",
        }
        if not enabled:
            return default_state

        normalized_paths = sorted(
            {
                self._normalize_repo_path(item)
                for item in changed_paths
                if str(item).strip()
            }
        )
        lowered_paths = [path.lower() for path in normalized_paths]

        def is_ui_path(path_lower: str) -> bool:
            suffix = Path(path_lower).suffix
            if suffix in ui_extensions:
                return True
            return any(keyword in path_lower for keyword in ui_path_keywords)

        def is_image_path(path_lower: str) -> bool:
            suffix = Path(path_lower).suffix
            return suffix in image_extensions

        ui_files = [
            path
            for path, lowered in zip(normalized_paths, lowered_paths)
            if is_ui_path(lowered)
        ]
        image_files = [
            path
            for path, lowered in zip(normalized_paths, lowered_paths)
            if is_image_path(lowered)
        ]
        if not ui_files:
            return {
                **default_state,
                "ui_evidence_status": "not-required",
            }

        evidence_from_repo = self.collect_repo_evidence_images(
            changed_image_paths=image_files,
            evidence_path_keywords=evidence_path_keywords,
            evidence_name_keywords=evidence_name_keywords,
        )
        repo_dir_evidence = self.collect_repo_dir_evidence_images(
            repo_root=repo_root,
            repo_evidence_dir=repo_evidence_dir,
            image_extensions=image_extensions,
        )
        evidence_from_repo = sorted(set(evidence_from_repo + repo_dir_evidence))
        self.copy_repo_evidence_images_to_run_dir(
            repo_root=repo_root,
            source_paths=evidence_from_repo,
            evidence_dir=evidence_dir,
            evidence_dir_relative=evidence_dir_relative,
        )
        collected_evidence_files = self.collect_run_dir_evidence_images(
            evidence_dir=evidence_dir,
            evidence_dir_relative=evidence_dir_relative,
            image_extensions=image_extensions,
        )
        if not collected_evidence_files:
            message = (
                "UI変更が検出されましたが、証跡画像が見つかりません。 "
                f"証跡画像（{', '.join(image_extensions)}）を "
                f"`{repo_evidence_dir}` または `{evidence_dir}` に配置するか、"
                "evidence_path_keywords/evidence_name_keywords に一致する画像ファイルを追加してください。"
            )
            if required:
                raise RuntimeError(message)
            self._log(f"WARNING: {message}")
            return {
                **default_state,
                "ui_evidence_status": "missing",
                "ui_evidence_ui_files": ui_files,
            }

        restored_paths: list[str] = []
        if delivery_mode == "artifact-only" and evidence_from_repo:
            restored_paths = self.restore_paths_after_evidence_copy(
                repo_root=repo_root,
                relative_paths=evidence_from_repo,
            )

        artifact_meta = self.detect_workflow_artifact_metadata()
        ui_lines: list[str] = [
            "UI-Evidence:",
            "- UI Files:",
        ]
        for path in ui_files[:max_ui_files]:
            ui_lines.append(f"  - `{path}`")
        if len(ui_files) > max_ui_files:
            ui_lines.append(f"  - ... ({len(ui_files) - max_ui_files} files omitted)")

        ui_lines.append(f"- Evidence Delivery: `{delivery_mode}`")
        if artifact_meta["run_artifact_name"]:
            ui_lines.append(f"- Workflow Artifact: `{artifact_meta['run_artifact_name']}`")
        if artifact_meta["run_artifact_url"]:
            ui_lines.append(f"- Workflow Artifact URL: {artifact_meta['run_artifact_url']}")
        ui_lines.append(f"- Evidence Dir (artifact): `{evidence_dir_relative}`")
        ui_lines.append("- Screenshots or GIF:")
        for path in collected_evidence_files[:max_images]:
            ui_lines.append(f"  - `{path}`")
        if len(collected_evidence_files) > max_images:
            ui_lines.append(
                f"  - ... ({len(collected_evidence_files) - max_images} files omitted)"
            )
        ai_logs_urls: list[str] = []
        ai_logs_branch = ""
        if isinstance(context, dict):
            ai_logs_branch = str(context.get("ui_evidence_ai_logs_branch", "")).strip()
            urls_raw = context.get("ui_evidence_ai_logs_urls", [])
            if isinstance(urls_raw, list):
                ai_logs_urls = [str(item).strip() for item in urls_raw if str(item).strip()]
        if ai_logs_branch:
            ui_lines.append(f"- AI Logs Branch: `{ai_logs_branch}`")
        if ai_logs_urls:
            ui_lines.append("- Evidence Images (ai-logs):")
            for url in ai_logs_urls[:max_images]:
                ui_lines.append(f"  - {url}")

        return {
            **default_state,
            "ui_evidence_status": "attached",
            "ui_evidence_ui_files": ui_files,
            "ui_evidence_image_files": collected_evidence_files,
            "ui_evidence_commit_image_files": evidence_from_repo,
            "ui_evidence_restored_paths": restored_paths,
            "ui_evidence_artifact_name": artifact_meta["run_artifact_name"],
            "ui_evidence_artifact_url": artifact_meta["run_artifact_url"],
            "ui_evidence_workflow_run_url": artifact_meta["workflow_run_url"],
            "ui_evidence_appendix": "\n".join(ui_lines).strip(),
        }
