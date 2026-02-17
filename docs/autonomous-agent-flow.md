# 自律エージェント開発フロー

## エンドツーエンドの流れ

1. 対象リポジトリで構造化された Issue を作成する
2. Issue番号と project id を指定して `自律エージェント PR` ワークフローを起動する
3. `scripts/agent_pipeline.py` が `.agent/projects.json` から対象リポジトリを解決する
4. 対象リポジトリを準備（clone/fetch）し、`gh issue view --repo` で Issue 情報を取得する
5. Planner コマンドが実装計画を作成する
6. Coder コマンドがコード変更を行い、各試行後に品質ゲートを実行する
7. 失敗したゲート結果を次回試行のフィードバックとして渡す
8. コミット前に Entire CLI を有効化し、コミットメッセージへ証跡トレーラーを残す
9. プロンプト/試行錯誤/設計根拠を `.entire/evidence/...` に明示登録し、`Entire-Trace-*` トレーラーをコミットメッセージへ追記する
10. 変更を `agent/<project>-issue-...` ブランチにコミットし、`push` する
11. コミット後に `Entire-Checkpoint` と `Entire-Trace-*` トレーラー、および証跡ファイルハッシュを検証する
12. 必要に応じて `entire explain --commit HEAD --generate` を実行する
13. `.agent/templates/pr_body.md` から PR 本文を生成する
14. 対象リポジトリに PR を作成または更新する
15. 人間レビューでマージ可否を判断する

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

## Entire CLI証跡設定

`.agent/pipeline.json` の `entire` セクションで制御します。

- `enabled`: Entire連携の有効/無効
- `required`: Entire連携失敗時に処理を失敗させるか
- `command`: Entire CLI コマンド（例: `entire`）
- `agent`: `entire enable --agent` に渡す識別子
- `strategy`: `manual-commit` / `auto-commit` / `none`
- `scope`: `project` / `global`
- `verify_trailer`: コミット後トレーラー検証の有効/無効
- `trailer_key`: 検証するトレーラーキー
- `explicit_registration.enabled`: 明示登録の有効/無効
- `explicit_registration.required`: 明示登録失敗時に処理を失敗させるか
- `explicit_registration.artifact_path`: 証跡ファイル出力先（リポジトリ相対）
- `explicit_registration.append_commit_trailers`: `Entire-Trace-File` / `Entire-Trace-SHA256` を付与するか
- `explicit_registration.max_chars_per_section`: 各証跡セクションの最大文字数
- `explicit_registration.generate_explain`: `entire explain --commit HEAD --generate` を実行するか

パイプラインは `entire version` / `entire strategy set` / `entire enable` を順に実行し、必要に応じて `entire explain --commit HEAD --generate` を実行します。  
明示登録が有効な場合は `.entire/evidence/...` を生成してコミットに含め、`Entire-Trace-*` トレーラーとハッシュ整合性を検証します。
既定では `required: true` のため、Entire連携に失敗した場合はコミット前後で処理を停止します。
GitHub Actions 側では workflow で `Entire CLI` をインストールしてからパイプラインを起動します。

## 差し替え可能なエージェントコマンド

`pipeline.json` では環境変数経由でコマンドを参照します。

- `AGENT_PLANNER_CMD`
- `AGENT_CODER_CMD`
- `AGENT_REVIEWER_CMD`（任意）

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
