# 自律エージェント開発フロー

## エンドツーエンドの流れ

1. 対象リポジトリで構造化された Issue を作成する
2. Issue番号と project id を指定して `自律エージェント PR` ワークフローを起動する
3. `scripts/agent_pipeline.py` が `.agent/projects.json` から対象リポジトリを解決する
4. 対象リポジトリを準備（clone/fetch）し、`gh issue view --repo` で Issue 情報を取得する
5. Planner コマンドが実装計画を作成する
6. Coder コマンドがコード変更を行い、各試行後に品質ゲートを実行する
7. 失敗したゲート結果を次回試行のフィードバックとして渡す
8. Codex への入力（Issue本文 / planner prompt）と検討結果（plan/review/coder出力/品質ゲート）を抽出し、コミットメッセージ要約を生成する
9. `run_dir` の実行ログを対象リポジトリ `ai-logs/issue-<番号>-<timestamp>/` に保存する
10. 変更を `agent/<project>-issue-...` ブランチにコミットし、`push` する
11. `.agent/templates/pr_body.md` から PR 本文を生成する（指示内容/検証コマンド/ログの場所を必須出力）
12. 対象リポジトリに PR を作成または更新する
13. 人間レビューでマージ可否を判断する

## 外部呼び出し受け口（ディスパッチ）

外部トリガーの受け口は `.github/workflows/autonomous-agent-dispatch.yml`（`自律エージェント ディスパッチ受け口`）です。

- トリガー: `repository_dispatch`
- イベント種別: `autonomous-agent-request`
バリデーション項目:
1. `client_payload.issue_number` は整数であること
2. `client_payload.project_id` または `client_payload.target_repo` のどちらかが必要
3. リポジトリ Secret `DISPATCH_SHARED_SECRET` が設定されている場合は、`client_payload.dispatch_secret` の一致が必要

最小ペイロード:

```json
{
  "event_type": "autonomous-agent-request",
  "client_payload": {
    "issue_number": 123,
    "project_id": "sample-webapp"
  }
}
```

任意ペイロード項目:

- `base_branch`
- `branch_name`
- `no_sync`
- `source_repository`（呼び出し元メタデータ）
- `request_id`（リクエスト識別子）

## マルチプロジェクト設定

`.agent/projects.json` でプロジェクトの振り分けを定義します。

- `workspace_root`: 対象リポジトリの clone 先
- `projects.<id>.repo`: 対象 GitHub リポジトリ（`owner/repo`）
- `projects.<id>.local_path`: 既存ローカルチェックアウトの任意指定
- `projects.<id>.config`: プロジェクト別パイプライン設定の任意指定
- `projects.<id>.overrides`: インライン上書き設定の任意指定
- `projects.<id>.base_branch`: プロジェクト別ベースブランチの任意指定

プロジェクト設定ファイルは部分定義で構いません。`.agent/pipeline.json` に対してマージされます。

`--project` を使わず `--target-repo` / `--target-path` で外部リポジトリを直接指定した場合は、
`.agent/pipeline.json` の `target_repo_defaults` が自動でマージされます。
これにより、対象リポジトリが増えても共通の既定挙動を一元管理できます。

## Codexコミット要約設定

`.agent/pipeline.json` の `codex_commit_summary` セクションで制御します。

- `enabled`: Codex要約の有効/無効
- `required`: 要約生成失敗時に処理を失敗させるか
- `max_chars_per_item`: 各要約項目の最大文字数
- `max_attempts`: 要約対象の最大試行回数
- `max_total_chars`: コミット追記要約の最大文字数

既定では有効で、コミットメッセージへ `Codex-Input-Summary` / `Codex-Consideration-Summary` を追記します。

## Entire CLI証跡設定（任意）

`.agent/pipeline.json` の `entire` セクションは残していますが、既定では `enabled: false` です。
必要時のみ有効化してください。

## ai-logs 設定

`.agent/pipeline.json` の `ai_logs` セクションで制御します。

- `enabled`: `ai-logs` 保存の有効/無効
- `required`: `ai-logs` 保存失敗時に処理を失敗させるか
- `path`: 保存先ディレクトリ（リポジトリ相対）
- `index_file`: インデックスファイル名

`required: true` の場合、`ai-logs` が保存できないと PR 作成前に失敗します。
PR本文には `ai-logs` のインデックスファイルへのリンクを埋め込みます。

## 差し替え可能なエージェントコマンド

`pipeline.json` では環境変数経由でコマンドを参照します。

- `AGENT_PLANNER_CMD`
- `AGENT_CODER_CMD`
- `AGENT_REVIEWER_CMD`（任意）

## Actions実行前の標準セットアップ

FlowSmith の workflow（`autonomous-agent-pr.yml` / `autonomous-agent-dispatch.yml`）では、パイプライン実行前に次を実施します。

1. `actions/setup-python@v5` で Python 3.12 をセットアップ
2. `actions/setup-node@v4` で Node.js 22 をセットアップ
3. `npm install -g @openai/codex` で `codex` CLI を導入（既存なら再インストールしない）
4. `CODEX_AUTH_JSON_B64` があれば `~/.codex/auth.json` を復元し、なければ `OPENAI_API_KEY` で `codex login --with-api-key` を実行
5. `AGENT_SETUP_SCRIPT` が設定されていれば実行
6. `AGENT_PLANNER_CMD` / `AGENT_CODER_CMD` / `AGENT_REVIEWER_CMD` を `shlex` で解析し、実行コマンドが `PATH` 上に存在するか事前検証
7. `FLOWSMITH_ENABLE_ENTIRE=true` のときのみ `Entire CLI` をインストール

関連 Secrets:

- `AGENT_PLANNER_CMD`（必須）
- `AGENT_CODER_CMD`（必須）
- `AGENT_REVIEWER_CMD`（任意）
- `OPENAI_API_KEY`（任意。`codex` の API キー認証で使う場合）
- `CODEX_AUTH_JSON_B64`（任意。`~/.codex/auth.json` の base64 文字列を使う暫定手段）
- `AGENT_SETUP_SCRIPT`（任意。追加依存の導入スクリプト）
- `CROSS_REPO_GH_TOKEN`（任意。クロスリポジトリ更新時に推奨）

`CODEX_AUTH_JSON_B64` は一時検証向けです。安定運用は API キー認証を推奨します。

各コマンドで使えるプレースホルダー:

- `{prompt_file}`
- `{output_file}`
- `{repo_root}`
- `{branch_name}`
- `{issue_number}`
- `{project_id}`
- `{target_repo}`

## 再試行ポリシー

- `max_attempts` で Coder の最大試行回数を制御
- 失敗したゲートログは `.agent/runs/<project>/*` に保存
- 次回試行には構造化された失敗フィードバックを渡す

## ハードニングチェックリスト

- 既定の品質ゲートをプロジェクト固有のゲートに置き換える
- ブランチ保護で CI とコードレビューを必須化する
- ワークフローにセキュリティスキャンとライセンスポリシー検査を追加する
- 必要なら `.agent/runs/` を artifact として保管する
- クロスリポジトリ操作では `CROSS_REPO_GH_TOKEN` の利用を優先する
- dispatch 受け口は `DISPATCH_SHARED_SECRET` で保護する
