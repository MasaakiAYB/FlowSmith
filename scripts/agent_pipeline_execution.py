#!/usr/bin/env python3
"""Execution orchestration for agent pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import Any, Callable


DependencyMap = dict[str, Callable[..., Any]]


def _dep(deps: DependencyMap, name: str) -> Callable[..., Any]:
    try:
        return deps[name]
    except KeyError as err:
        raise RuntimeError(f"Missing execution dependency: {name}") from err


def run_pipeline(
    *,
    args: argparse.Namespace,
    control_root: Path,
    deps: DependencyMap,
) -> int:
    resolve_runtime = _dep(deps, "resolve_runtime")
    require_clean_worktree = _dep(deps, "require_clean_worktree")
    load_issue_from_file = _dep(deps, "load_issue_from_file")
    load_issue_from_gh = _dep(deps, "load_issue_from_gh")
    build_default_pr_title = _dep(deps, "build_default_pr_title")
    resolve_feedback_pr_context = _dep(deps, "resolve_feedback_pr_context")
    slugify = _dep(deps, "slugify")
    detect_workflow_artifact_metadata = _dep(deps, "detect_workflow_artifact_metadata")
    resolve_ui_repo_evidence_dir = _dep(deps, "resolve_ui_repo_evidence_dir")
    render_issue_instruction_markdown = _dep(deps, "render_issue_instruction_markdown")
    render_related_issue_markdown = _dep(deps, "render_related_issue_markdown")
    render_validation_commands_markdown = _dep(deps, "render_validation_commands_markdown")
    render_log_location_markdown = _dep(deps, "render_log_location_markdown")
    load_feedback_text = _dep(deps, "load_feedback_text")
    parse_positive_int = _dep(deps, "parse_positive_int")
    write_text = _dep(deps, "write_text")
    ensure_branch = _dep(deps, "ensure_branch")
    setup_entire_trace = _dep(deps, "setup_entire_trace")
    resolve_command = _dep(deps, "resolve_command")
    resolve_path = _dep(deps, "resolve_path")
    render_template_file = _dep(deps, "render_template_file")
    run_agent_command = _dep(deps, "run_agent_command")
    read_text = _dep(deps, "read_text")
    run_quality_gates = _dep(deps, "run_quality_gates")
    clip_text = _dep(deps, "clip_text")
    build_codex_commit_summary = _dep(deps, "build_codex_commit_summary")
    prepare_entire_explicit_registration = _dep(deps, "prepare_entire_explicit_registration")
    save_ai_logs_bundle = _dep(deps, "save_ai_logs_bundle")
    publish_ai_logs_to_dedicated_branch = _dep(deps, "publish_ai_logs_to_dedicated_branch")
    build_ui_evidence_ai_logs_context = _dep(deps, "build_ui_evidence_ai_logs_context")
    cleanup_untracked_coder_outputs = _dep(deps, "cleanup_untracked_coder_outputs")
    format_template = _dep(deps, "format_template")
    build_commit_message = _dep(deps, "build_commit_message")
    commit_changes = _dep(deps, "commit_changes")
    is_no_change_runtime_error = _dep(deps, "is_no_change_runtime_error")
    get_head_commit_sha = _dep(deps, "get_head_commit_sha")
    get_head_commit_message = _dep(deps, "get_head_commit_message")
    extract_commit_trailer = _dep(deps, "extract_commit_trailer")
    verify_entire_explicit_registration = _dep(deps, "verify_entire_explicit_registration")
    generate_entire_explain = _dep(deps, "generate_entire_explain")
    push_branch = _dep(deps, "push_branch")
    parse_string_list = _dep(deps, "parse_string_list")
    build_pr_change_type_checklist_markdown = _dep(deps, "build_pr_change_type_checklist_markdown")
    build_pr_auto_checklist_markdown = _dep(deps, "build_pr_auto_checklist_markdown")
    build_pr_manual_checklist_markdown = _dep(deps, "build_pr_manual_checklist_markdown")
    validate_required_pr_context = _dep(deps, "validate_required_pr_context")
    create_or_update_pr = _dep(deps, "create_or_update_pr")
    resolve_pr_number = _dep(deps, "resolve_pr_number")
    extract_trigger_reason_from_feedback_text = _dep(deps, "extract_trigger_reason_from_feedback_text")
    is_comment_feedback_trigger = _dep(deps, "is_comment_feedback_trigger")
    build_feedback_update_comment = _dep(deps, "build_feedback_update_comment")
    post_pr_issue_comment = _dep(deps, "post_pr_issue_comment")
    log = _dep(deps, "log")
    
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
    issue_labels_raw = issue.get("labels", [])
    if not isinstance(issue_labels_raw, list):
        issue_labels_raw = []
    issue_labels = [str(item).strip() for item in issue_labels_raw if str(item).strip()]
    issue_state = str(issue.get("state") or "open").strip().lower() or "open"
    pr_title_default = build_default_pr_title(
        issue_title=str(issue.get("title", "")),
        issue_labels=issue_labels,
    )
    
    feedback_pr_number = max(int(args.feedback_pr_number or 0), 0)
    feedback_pr_context = {"head_ref": "", "base_ref": "", "url": ""}
    if feedback_pr_number > 0:
        feedback_pr_context = resolve_feedback_pr_context(
            repo_root=target_repo_root,
            repo_slug=repo_slug,
            pr_number=feedback_pr_number,
        )
    
    config_base_branch = str(config.get("base_branch", "main"))
    base_branch = (
        args.base_branch
        or runtime["default_base_branch"]
        or feedback_pr_context["base_ref"]
        or config_base_branch
    )
    
    branch_prefix = f"{slugify(project_id)}-" if project_id else ""
    branch_name = (
        args.branch_name
        or feedback_pr_context["head_ref"]
        or f"agent/{branch_prefix}issue-{issue['number']}-{slugify(issue['title'])}"
    )
    max_attempts = int(config.get("max_attempts", 3))
    quality_gates = config.get("quality_gates", [])
    quality_gate_list = "\n".join(f"- `{item}`" for item in quality_gates) or "- (none)"
    
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = control_root / ".agent" / "runs" / runtime["run_namespace"] / f"{timestamp}-issue-{issue['number']}"
    run_dir.mkdir(parents=True, exist_ok=False)
    workflow_artifact_meta = detect_workflow_artifact_metadata()
    ui_conf_for_context = config.get("ui_evidence", {})
    if ui_conf_for_context is None:
        ui_conf_for_context = {}
    if not isinstance(ui_conf_for_context, dict):
        raise RuntimeError("Config 'ui_evidence' must be an object when specified.")
    ui_repo_evidence_relative, ui_repo_evidence_dir = resolve_ui_repo_evidence_dir(
        repo_root=target_repo_root,
        ui_conf_raw=ui_conf_for_context,
    )
    
    task_file = run_dir / "task.md"
    plan_file = run_dir / "plan.md"
    review_file = run_dir / "review.md"
    pr_body_file = run_dir / "pr_body.md"
    
    context: dict[str, Any] = {
        "issue_number": issue["number"],
        "issue_title": issue["title"],
        "issue_labels": ", ".join(issue_labels),
        "issue_state": issue_state,
        "issue_body": issue["body"],
        "issue_url": issue["url"],
        "related_issue_markdown": "",
        "pr_title_default": pr_title_default,
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
        "external_feedback_markdown": "_追加フィードバックなし_",
        "external_feedback_text": "",
        "external_feedback_pr_number": 0,
        "external_feedback_pr_url": "",
        "external_feedback_item_count": 0,
        "feedback_trigger_reason": "",
        "feedback_update_comment_status": "skipped",
        "feedback_update_comment_reason": "",
        "pr_change_type_checklist_markdown": (
            "- [ ] バグ修正\n"
            "- [ ] 新機能\n"
            "- [ ] ドキュメント更新\n"
            "- [ ] リファクタリング\n"
            "- [ ] CI/CD・インフラ\n"
            "- [ ] テスト追加・修正\n"
            "- 判定種別: `feat`"
        ),
        "pr_auto_checklist_markdown": (
            "- [ ] 品質ゲート結果を確認済み\n"
            "- [ ] UI証跡状態を確認済み\n"
            "- [ ] AIログリンクを確認可能"
        ),
        "pr_manual_checklist_markdown": (
            "- [ ] 受け入れ条件と実装差分が一致していることを確認した\n"
            "- [ ] 破壊的変更・移行手順の有無を確認した\n"
            "- [ ] 人間レビューを実施した"
        ),
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
        "ai_logs_published_paths": [],
        "codex_commit_summary_required": True,
        "codex_commit_summary_status": "skipped",
        "codex_commit_summary_appendix": "",
        "ui_evidence_enabled": True,
        "ui_evidence_required": True,
        "ui_evidence_status": "skipped",
        "ui_evidence_delivery_mode": "artifact-only",
        "ui_evidence_artifact_dir": "ui-evidence",
        "ui_evidence_artifact_name": workflow_artifact_meta["run_artifact_name"],
        "ui_evidence_artifact_url": workflow_artifact_meta["run_artifact_url"],
        "ui_evidence_workflow_run_url": workflow_artifact_meta["workflow_run_url"],
        "ui_evidence_ui_files": [],
        "ui_evidence_image_files": [],
        "ui_evidence_commit_image_files": [],
        "ui_evidence_restored_paths": [],
        "ui_evidence_ai_logs_branch": "",
        "ui_evidence_ai_logs_paths": [],
        "ui_evidence_ai_logs_urls": [],
        "ui_evidence_ai_logs_links_markdown": "- `(なし)`",
        "ui_evidence_ai_logs_embeds_markdown": "_画像はありません。_",
        "ui_evidence_repo_dir": ui_repo_evidence_relative,
        "ui_evidence_dir": str(ui_repo_evidence_dir.resolve()),
        "ui_evidence_appendix": "",
        "committed_paths": [],
        "head_commit": "",
        "commit_status": "pending",
        "pr_status": "pending",
        "pr_action": "skipped",
        "no_change_reason": "",
    }
    ui_repo_evidence_dir.mkdir(parents=True, exist_ok=True)
    Path(run_dir / "ui-evidence").mkdir(parents=True, exist_ok=True)
    
    context["instruction_markdown"] = render_issue_instruction_markdown(
        issue_number=issue["number"],
        issue_title=issue["title"],
        issue_url=issue["url"],
        issue_body=issue["body"],
    )
    context["related_issue_markdown"] = render_related_issue_markdown(
        issue_number=issue["number"],
        issue_url=issue["url"],
        issue_state=issue_state,
    )
    context["validation_commands_markdown"] = render_validation_commands_markdown(quality_gates)
    context["log_location_markdown"] = render_log_location_markdown(context)
    context.update(
        load_feedback_text(
            control_root=control_root,
            run_dir=run_dir,
            repo_root=target_repo_root,
            repo_slug=repo_slug,
            feedback_file=args.feedback_file,
            feedback_text=str(args.feedback_text or ""),
            feedback_pr_number=feedback_pr_number,
            feedback_max_items=parse_positive_int(
                args.feedback_max_items,
                default=20,
                name="feedback_max_items",
            ),
        )
    )
    
    write_text(
        task_file,
        (
            f"# Issue #{issue['number']}: {issue['title']}\n\n"
            f"Project: {project_id or '(default)'}\n"
            f"Target repo: {repo_slug or '(inferred from local git)'}\n"
            f"Target path: {target_repo_root}\n"
            f"URL: {issue['url'] or '(local file)'}\n\n"
            f"## Body\n\n{issue['body']}\n\n"
            f"## External Feedback\n\n{context['external_feedback_markdown']}\n"
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
    external_feedback_text = clip_text(
        str(context.get("external_feedback_text", "")).strip(),
        max_chars=6000,
    ).strip()
    feedback = external_feedback_text or "None"
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
    
        quality_feedback = (
            "Quality gates failed on previous attempt.\n\n"
            f"{summary}\n\n"
            "Fix the failing points and retry."
        )
        if external_feedback_text:
            feedback = clip_text(
                (
                    "PRレビュー/コメントの指摘（継続対応）:\n\n"
                    f"{external_feedback_text}\n\n"
                    f"{quality_feedback}"
                ),
                max_chars=8000,
            )
        else:
            feedback = quality_feedback
    
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
    context.update(
        build_ui_evidence_ai_logs_context(
            context=context,
            config=config,
            repo_slug=repo_slug,
        )
    )
    context["log_location_markdown"] = render_log_location_markdown(context)
    
    removed_stray_outputs = cleanup_untracked_coder_outputs(target_repo_root)
    if removed_stray_outputs:
        write_text(
            run_dir / "coder_output_cleanup.md",
            "\n".join(f"- removed: `{path}`" for path in removed_stray_outputs) + "\n",
        )
    
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
    commit_skipped_no_change = False
    try:
        commit_state = commit_changes(
            target_repo_root,
            commit_message,
            run_dir=run_dir,
            config=config,
            context=context,
            ignore_paths=ignored_paths,
            force_add_paths=force_add_paths,
            required_paths=required_paths,
        )
        context.update(commit_state)
        context["commit_status"] = "committed"
    except RuntimeError as err:
        if args.allow_no_changes and is_no_change_runtime_error(err):
            commit_skipped_no_change = True
            no_change_reason = str(err).strip()
            context["commit_status"] = "skipped-no-change"
            context["pr_status"] = "skipped-no-change"
            context["no_change_reason"] = no_change_reason
            context["head_commit"] = "(no-change)"
            context["entire_checkpoint"] = "no-change"
            context["entire_trace_verify_status"] = "skipped-no-change"
            context["entire_explain_status"] = "skipped-no-change"
            write_text(
                run_dir / "no_change.md",
                (
                    "# No Change\n\n"
                    "- status: `skipped-no-change`\n"
                    f"- reason: `{no_change_reason}`\n"
                    "- note: `--allow-no-changes` により成功扱いで終了\n"
                ),
            )
            write_text(
                run_dir / "entire_trace.md",
                (
                    "# Entire 証跡\n\n"
                    f"- status: `{context.get('entire_status')}`\n"
                    f"- trailer_key: `{context.get('entire_trailer_key')}`\n"
                    "- checkpoint: `no-change`\n"
                    "- commit: `(no-change)`\n"
                    f"- trace_status: `{context.get('entire_trace_status')}`\n"
                    f"- trace_file: `{context.get('entire_trace_file')}`\n"
                    f"- trace_sha256: `{context.get('entire_trace_sha256')}`\n"
                    "- trace_verify_status: `skipped-no-change`\n"
                    "- explain_status: `skipped-no-change`\n"
                    f"- explain_log: `{context.get('entire_explain_log')}`\n"
                ),
            )
            log("No meaningful changes were detected. Skipped commit/push/pr.")
        else:
            raise
    
    if not commit_skipped_no_change:
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
            pr_title_template = str(
                pr_conf.get("title", "{pr_title_default}")
            ).strip() or "{pr_title_default}"
            pr_title = format_template(
                pr_title_template,
                context,
                "pr.title",
            ).strip()
            if not pr_title:
                pr_title = str(context.get("pr_title_default", "")).strip() or str(issue["title"]).strip()
            pr_labels = parse_string_list(
                pr_conf.get("labels"),
                default=[],
                name="pr.labels",
            )
            pr_labels_required = bool(pr_conf.get("labels_required", True))
            pr_draft = bool(pr_conf.get("draft", False))
    
            committed_paths_raw = context.get("committed_paths", [])
            committed_paths = (
                [str(item).strip() for item in committed_paths_raw if str(item).strip()]
                if isinstance(committed_paths_raw, list)
                else []
            )
            context["pr_change_type_checklist_markdown"] = build_pr_change_type_checklist_markdown(
                issue_title=str(issue.get("title", "")),
                issue_labels=issue_labels,
                pr_title=pr_title,
                committed_paths=committed_paths,
            )
            context["pr_auto_checklist_markdown"] = build_pr_auto_checklist_markdown(context)
            context["pr_manual_checklist_markdown"] = build_pr_manual_checklist_markdown()
    
            validate_required_pr_context(context)
            write_text(pr_body_file, render_template_file(pr_template, context))
            pr_result = create_or_update_pr(
                repo_root=target_repo_root,
                repo_slug=repo_slug,
                base_branch=base_branch,
                branch_name=branch_name,
                title=pr_title,
                body_file=pr_body_file,
                labels=pr_labels,
                labels_required=pr_labels_required,
                draft=pr_draft,
            )
            pr_url = str(pr_result.get("url", "")).strip()
            pr_number = resolve_pr_number(str(pr_result.get("number", "")).strip() or pr_url)
            pr_action = str(pr_result.get("action", "")).strip() or "created"
            context["pr_action"] = pr_action
            write_text(run_dir / "pr_url.txt", pr_url + "\n")
            trigger_reason = extract_trigger_reason_from_feedback_text(
                str(context.get("external_feedback_text", ""))
            )
            context["feedback_trigger_reason"] = trigger_reason
    
            feedback_comment_status = "skipped"
            feedback_comment_reason = ""
            if (
                feedback_pr_number > 0
                and context.get("commit_status") == "committed"
                and pr_action == "updated"
            ):
                if is_comment_feedback_trigger(trigger_reason):
                    comment_body = build_feedback_update_comment(
                        head_commit=str(context.get("head_commit", "")),
                        ai_logs_index_url=str(context.get("ai_logs_index_url", "")),
                    )
                    comment_pr_number = pr_number or str(feedback_pr_number)
                    posted = post_pr_issue_comment(
                        repo_root=target_repo_root,
                        repo_slug=repo_slug,
                        pr_number=comment_pr_number,
                        body=comment_body,
                    )
                    if posted:
                        feedback_comment_status = "posted"
                    else:
                        feedback_comment_status = "failed"
                        feedback_comment_reason = "PRコメントの投稿に失敗しました。"
                else:
                    feedback_comment_reason = (
                        "コメント起点ではないため返信コメントを省略しました。"
                        f" trigger={trigger_reason or 'unknown'}"
                    )
            context["feedback_update_comment_status"] = feedback_comment_status
            context["feedback_update_comment_reason"] = feedback_comment_reason
            write_text(
                run_dir / "feedback_update_comment.md",
                (
                    "# Feedback Update Comment\n\n"
                    f"- trigger_reason: `{trigger_reason or 'unknown'}`\n"
                    f"- pr_action: `{pr_action}`\n"
                    f"- status: `{feedback_comment_status}`\n"
                    f"- reason: `{feedback_comment_reason or 'N/A'}`\n"
                ),
            )
            context["pr_status"] = "created-or-updated"
        else:
            context["pr_status"] = "skipped"
    
    write_text(
        run_dir / "summary.md",
        (
            f"# Agent Pipeline Summary\n\n"
            f"- Project: `{project_id or 'default'}`\n"
            f"- Target repo: `{repo_slug or '(inferred local git)'}`\n"
            f"- Target path: `{target_repo_root}`\n"
            f"- Issue: `#{issue['number']}`\n"
            f"- Branch: `{branch_name}`\n"
            f"- Commit status: `{context['commit_status']}`\n"
            f"- Commit: `{context['head_commit']}`\n"
            f"- PR status: `{context['pr_status']}`\n"
            f"- PR action: `{context['pr_action']}`\n"
            f"- Feedback trigger: `{context['feedback_trigger_reason'] or 'N/A'}`\n"
            f"- Feedback update comment: `{context['feedback_update_comment_status']}`\n"
            f"- Feedback update comment reason: `{context['feedback_update_comment_reason'] or 'N/A'}`\n"
            f"- No change reason: `{context['no_change_reason'] or 'N/A'}`\n"
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
            f"- UI evidence status: `{context['ui_evidence_status']}`\n"
            f"- UI evidence delivery mode: `{context['ui_evidence_delivery_mode']}`\n"
            f"- UI evidence artifact dir: `{context['ui_evidence_artifact_dir']}`\n"
            f"- UI evidence artifact: `{context['ui_evidence_artifact_name']}`\n"
            f"- UI evidence files: `{len(context.get('ui_evidence_image_files', []))}`\n"
            f"- Codex commit summary: `{context['codex_commit_summary_status']}`\n"
            f"- Validation:\n{context['validation_summary']}\n"
        ),
    )
    log(f"Completed successfully. Logs: {run_dir}")
    return 0
    
    
