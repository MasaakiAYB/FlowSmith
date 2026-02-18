## 変更内容

- 対応Issue: `#{issue_number}` `{issue_title}`
- 対象リポジトリ: `{target_repo}`
- プロジェクト: `{project_id}`
- コミット: `{head_commit}`

{plan_markdown}

## 関連 Issue

- Closes #{issue_number}
- 元Issue URL: {issue_url}

## 変更の種類

- [ ] バグ修正
- [ ] 新機能
- [ ] ドキュメント更新
- [ ] リファクタリング
- [ ] CI/CD・インフラ
- [ ] テスト追加・修正

## チェックリスト

- [ ] 検証コマンドの結果を確認した
- [ ] 既存挙動への影響を確認した
- [ ] 人間レビューを実施した

## スクリーンショット（UI変更時）

- UI証跡状態: `{ui_evidence_status}`
- UI証跡artifact名: `{ui_evidence_artifact_name}`
- UI証跡artifactリンク: {ui_evidence_artifact_url}

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
