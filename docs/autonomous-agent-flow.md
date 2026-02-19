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
9. `run_dir` の実行ログを `ai-logs/issue-<番号>-<timestamp>/` に保存し、専用ブランチ（既定: `agent-ai-logs`）へ集約する
10. 変更を `agent/<project>-issue-...` ブランチにコミットし、`push` する
11. `.agent/templates/pr_body.md` から PR 本文を生成する（OJPP準拠の章立て + 指示内容/検証コマンド/ログの場所を必須出力）
12. PRタイトルを装飾プレフィックス除去 + Conventional形式で自動整形し、`agent/` 系ラベルを付与したうえで PR を作成または更新する（付与できない場合は失敗）
13. 人間レビューでマージ可否を判断する
14. `feedback_pr_number` 指定時はPRレビュー/コメントを抽出して次回の Planner/Coder/Reviewer 入力へ反映する（`branch_name` 未指定時は対象PRの head ブランチへ自動追従）

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
- `feedback_pr_number`（対象リポジトリのPR番号。指定時は改善指摘を自動抽出）
- `feedback_text`（追加で与える改善指摘テキスト）
- `no_sync`
- `source_repository`（呼び出し元メタデータ）
- `request_id`（リクエスト識別子）

呼び出し側（対象リポジトリ）でのPRレビュー起点の自動再実行例:

- `docs/examples/trigger-flowsmith-on-pr-feedback.yml`

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
- `max_points`: PR本文に展開する要点数
- `max_total_chars`: コミット追記要約の最大文字数

既定では有効で、コミットメッセージへ `Codex-Context`（`指示/試行錯誤/設計根拠` の固定3見出し）を追記し、
末尾に `Codex-Log-Reference` としてログの場所を保存します。
PR本文には `TL;DR / 要求の再解釈 / Decision Log / 試行ログ / 検証結果 / 残リスク・未解決 / 証跡リンク` を表示します。

## UI画像証跡設定

`.agent/pipeline.json` の `ui_evidence` セクションで制御します。

- `enabled`: UI証跡チェックの有効/無効
- `required`: UI変更時に画像証跡が無い場合に失敗させるか
- `delivery_mode`: `artifact-only`（既定）または `commit`
- `repo_dir`: 証跡画像を投入する対象リポジトリ内ディレクトリ（既定: `.flowsmith/ui-evidence`）
- `artifact_dir`: 実行ログ配下での証跡画像ディレクトリ
- `ui_extensions`: UI変更として判定する拡張子
- `ui_path_keywords`: UI変更として判定するパスキーワード
- `image_extensions`: 証跡画像として許可する拡張子（スクリーンショット/GIF）
- `evidence_path_keywords`: リポジトリ内画像を証跡として扱うパスキーワード
- `evidence_name_keywords`: リポジトリ内画像を証跡として扱うファイル名キーワード
- `max_ui_files`: コミットメッセージに列挙するUI変更ファイルの最大件数
- `max_images`: コミットメッセージに列挙する証跡画像の最大件数

既定では有効です。UI変更が検出された場合、証跡画像が `repo_dir` または `run_dir/ui-evidence/`（artifact-only）に無いとコミット前に失敗します。
条件を満たすと、コミットメッセージ末尾へ `UI-Evidence` セクションを自動追記し、`ai-logs` ブランチ上の画像リンクを記載します。
PR本文には `ai-logs` ブランチ上の画像をインライン表示し、Workflow Artifact は補助情報として記載します。

## Entire CLI証跡設定（任意）

`.agent/pipeline.json` の `entire` セクションは残していますが、既定では `enabled: false` です。
必要時のみ有効化してください。

## ai-logs 設定

`.agent/pipeline.json` の `ai_logs` セクションで制御します。

- `enabled`: `ai-logs` 保存の有効/無効
- `required`: `ai-logs` 保存失敗時に処理を失敗させるか
- `path`: 保存先ディレクトリ（リポジトリ相対）
- `index_file`: インデックスファイル名
- `publish.mode`: `same-branch` または `dedicated-branch`
- `publish.branch`: `dedicated-branch` 利用時の集約先ブランチ名
- `publish.required`: 集約ブランチ反映失敗時に処理を失敗させるか
- `publish.commit_message`: 集約ブランチ用コミットメッセージテンプレート

既定は `publish.mode: dedicated-branch` です。`ai-logs` は専用ブランチへ集約され、実装PRブランチには含めません。
`required: true` または `publish.required: true` の場合、保存/集約に失敗すると PR 作成前に失敗します。
PR本文には専用ブランチ上の `ai-logs` インデックスファイルへのリンクを埋め込みます。
UI証跡画像も `ai-logs/.../ui-evidence/` 配下へ同時保存され、PR本文で直接表示できるURLを生成します。

## 差し替え可能なエージェントコマンド

`pipeline.json` では環境変数経由でコマンドを参照します。

- `AGENT_PLANNER_CMD`
- `AGENT_CODER_CMD`
- `AGENT_REVIEWER_CMD`（任意）

## Actions実行前の標準セットアップ

FlowSmith の workflow（`autonomous-agent-pr.yml` / `autonomous-agent-dispatch.yml`）では、パイプライン実行前に次を実施します。

1. `actions/setup-python@v5` で Python 3.12 をセットアップ
2. `actions/setup-node@v4` で Node.js 22 をセットアップ
3. `fonts-noto-cjk` / `fonts-ipafont-*` を導入して日本語フォントをセットアップ（UI証跡の文字化け防止）
4. `npm install -g @openai/codex` で `codex` CLI を導入（既存なら再インストールしない）
5. `CODEX_AUTH_JSON_B64` があれば `~/.codex/auth.json` を復元し、なければ `OPENAI_API_KEY` で `codex login --with-api-key` を実行
6. `AGENT_SETUP_SCRIPT` が設定されていれば実行
7. `AGENT_PLANNER_CMD` / `AGENT_CODER_CMD` / `AGENT_REVIEWER_CMD` を `shlex` で解析し、実行コマンドが `PATH` 上に存在するか事前検証
8. `FLOWSMITH_ENABLE_ENTIRE=true` のときのみ `Entire CLI` をインストール

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
- `{ui_evidence_dir}`（UI証跡画像の投入先。既定: `<repo_root>/.flowsmith/ui-evidence`）

## 再試行ポリシー

- `max_attempts` で Coder の最大試行回数を制御
- 失敗したゲートログは `.agent/runs/<project>/*` に保存
- 次回試行には構造化された失敗フィードバックを渡す

## ハードニングチェックリスト

- 既定の品質ゲートをプロジェクト固有のゲートに置き換える
- ブランチ保護で CI とコードレビューを必須化する
- ワークフローにセキュリティスキャンとライセンスポリシー検査を追加する
- `.agent/runs/` の artifact 保存を有効化し、UI証跡を含めて追跡可能にする
- クロスリポジトリ操作では `CROSS_REPO_GH_TOKEN` の利用を優先する
- dispatch 受け口は `DISPATCH_SHARED_SECRET` で保護する
