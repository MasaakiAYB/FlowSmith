## 変更内容

- 対応Issue: `#{issue_number}` `{issue_title}`
- 対象リポジトリ: `{target_repo}`
- プロジェクト: `{project_id}`
- コミット: `{head_commit}`

## レビュー要約

### 変更の種類（自動判定）
{pr_change_type_checklist_markdown}

### チェックリスト（自動）
{pr_auto_checklist_markdown}

### チェックリスト（手動）
{pr_manual_checklist_markdown}

## 実装計画
{plan_markdown}

## 関連 Issue

{related_issue_markdown}

## レビュー指摘（自動抽出）
{external_feedback_markdown}

## スクリーンショット（UI変更時）

- UI証跡状態: `{ui_evidence_status}`
- UI証跡artifact名: `{ui_evidence_artifact_name}`
- UI証跡artifactリンク: {ui_evidence_artifact_url}
- UI証跡ai-logsブランチ: `{ui_evidence_ai_logs_branch}`
- UI証跡ai-logsリンク:
{ui_evidence_ai_logs_links_markdown}

### UI画像プレビュー（ai-logs）
{ui_evidence_ai_logs_embeds_markdown}

## AIエージェント実行ログ

### 指示内容（必須）
{instruction_markdown}

### 検証コマンド（必須）
{validation_commands_markdown}

### ログの場所（必須）
{log_location_markdown}

### Codex判断ログ
{codex_commit_summary_markdown}

## 検証結果
{validation_summary}

## レビューレポート
{review_markdown}

---

- AIエージェントのPRでは `agent/` ラベルを付与します。
- マージ前に人間レビューが必要です。
