## 概要
- 対応Issue: #{issue_number}
- 元Issue URL: {issue_url}
- プロジェクト: {project_id}
- 対象リポジトリ: {target_repo}
- コミット: {head_commit}
- AIログ保存状態: {ai_logs_status}
- AIログインデックス: {ai_logs_index_file}
- AIログリンク: {ai_logs_index_url}
- エージェント実行ディレクトリ: `{run_dir}`

## 指示内容（必須）
{instruction_markdown}

## 検証コマンド（必須）
{validation_commands_markdown}

## ログの場所（必須）
{log_location_markdown}

## Codex判断ログ
{codex_commit_summary_markdown}

## 実装計画
{plan_markdown}

## 検証結果
{validation_summary}

## レビューレポート
{review_markdown}

## 備考
- このPRは自律エージェントパイプラインで作成されました。
- マージ前に人間レビューが必要です。
