#!/usr/bin/env python3
"""Core utility helpers shared by agent pipeline modules."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any


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


def parse_string_list(value: Any, *, default: list[str], name: str) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise RuntimeError(f"Config '{name}' must be a list.")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


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


def normalize_repo_slug(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    https_match = re.match(
        r"https?://[^/]+/([^/]+/[^/]+?)(?:\\.git)?/?$",
        value,
        flags=re.IGNORECASE,
    )
    if https_match:
        return https_match.group(1)

    ssh_match = re.match(r"git@[^:]+:([^/]+/[^/]+?)(?:\\.git)?$", value)
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
