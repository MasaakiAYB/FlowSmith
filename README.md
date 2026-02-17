# FlowSmith 自律エージェントフレームワーク

このリポジトリは、AIエージェントによる自律開発のための最小構成フレームワークです。主な処理は次のとおりです。

1. GitHub Issue を読み込む
2. 実装計画を作成する
3. コードを変更する
4. 品質ゲートを実行する
5. ブランチにコミットして `push` する
6. PR を作成または更新する

オーケストレーター本体は `scripts/agent_pipeline.py` です。

## リポジトリ構成

- `.agent/pipeline.json`: 全体共通の既定設定（コマンド、再試行、テンプレート、PR設定）
- `.agent/projects.json`: マルチプロジェクト用レジストリ（リポジトリ対応、プロジェクト別上書き）
- `.agent/projects/*.json`: プロジェクトごとの任意の上書き設定
- `.agent/prompts/*.md`: Planner / Coder / Reviewer 用プロンプトテンプレート
- `.agent/templates/pr_body.md`: PR本文テンプレート
- `.github/workflows/autonomous-agent-pr.yml`: 手動実行用ワークフロー
- `.github/workflows/autonomous-agent-dispatch.yml`: 外部呼び出しディスパッチ受け口ワークフロー
- `scripts/agent_pipeline.py`: Issue から PR までを実行するオーケストレーター

## セットアップ

1. エージェント実行コマンドを設定します。

```bash
export AGENT_PLANNER_CMD='codex run --non-interactive --prompt-file {prompt_file} --output-file {output_file}'
export AGENT_CODER_CMD='codex run --non-interactive --prompt-file {prompt_file} --output-file {output_file}'
export AGENT_REVIEWER_CMD='codex run --non-interactive --prompt-file {prompt_file} --output-file {output_file}'
export GH_TOKEN='<your_github_token>'
```

2. `.agent/projects.json` に対象プロジェクトを定義します。
3. `Entire CLI` を使う場合は、事前にインストールと認証を実施します（例: `entire version` / `entire auth login`）。
   GitHub Actions では workflow 内で `Entire CLI` を自動インストールします。

## Entire証跡（コミットログ）

このフレームワークは、コミット前に `Entire CLI` を実行して Git フックを有効化し、コミットメッセージに `Entire-Checkpoint` トレーラーを残します。  
さらに `explicit_registration` を有効にすると、次を明示登録します。

- プロンプト履歴（Planner / Coder各試行 / Reviewer）
- 試行錯誤履歴（Coder出力と品質ゲート結果）
- 設計根拠（plan/review の内容）

登録内容は対象リポジトリの `.entire/evidence/issue-<番号>-<timestamp>.md` に保存され、同一コミットに含まれます。  
同時にコミットメッセージへ `Entire-Trace-File` / `Entire-Trace-SHA256` トレーラーを追加し、コミット後に整合性検証します。
この方式により、Entire がネイティブ収集していないエージェント実行でも、証跡をコミットベースで追跡できます。

既定設定は `.agent/pipeline.json` の `entire` セクションです。

- `enabled`: Entire連携を有効化
- `required`: Entire連携に失敗したらジョブを失敗させる
- `command`: Entire CLI コマンド（例: `entire`）
- `agent`: `entire enable --agent` に渡すエージェント名
- `strategy`: `manual-commit` / `auto-commit` / `none`
- `scope`: `project` / `global`
- `verify_trailer`: コミット後にトレーラー存在を検証する
- `trailer_key`: 検証対象トレーラーキー（既定: `Entire-Checkpoint`）
- `explicit_registration.enabled`: 明示登録を有効化
- `explicit_registration.required`: 明示登録失敗時にジョブを失敗させる
- `explicit_registration.artifact_path`: 証跡ファイル出力先（リポジトリ相対パス）
- `explicit_registration.append_commit_trailers`: `Entire-Trace-*` トレーラーを追加
- `explicit_registration.max_chars_per_section`: 各証跡セクションの記録上限文字数
- `explicit_registration.generate_explain`: `entire explain --commit HEAD --generate` を実行

既定では `required: true` のため、Entire CLI が未導入・未認証、またはトレーラー未付与の場合はジョブが失敗します。  
実行時には `.agent/runs/.../entire_trace.md` にチェックポイント・明示登録・`explain` 実行結果が保存され、PR本文にも反映されます。

## 実行モード

### 単一プロジェクト（現在のリポジトリ）

```bash
python scripts/agent_pipeline.py --issue-number 123 --push --create-pr
```

### マルチプロジェクト（登録済みプロジェクト）

```bash
python scripts/agent_pipeline.py \
  --project sample-webapp \
  --issue-number 123 \
  --push \
  --create-pr
```

`--project` を指定すると、`.agent/projects.json` を使って対象リポジトリとローカルワークスペースを解決します。ワークスペースが存在しない場合は最初に clone します。

### 単発の外部リポジトリ

```bash
python scripts/agent_pipeline.py \
  --target-repo your-org/your-repo \
  --target-path /tmp/your-repo \
  --issue-number 123 \
  --push \
  --create-pr
```

## GitHub Actions利用

`自律エージェント PR` ワークフローを使い、次の入力を渡します。

- `issue_number`（必須）
- `project_id`（任意、`.agent/projects.json` のID）
- `target_repo`（任意、対象リポジトリの上書き）
- `base_branch`（任意、ベースブランチの上書き）

ワークフローで参照する Secrets:

- `AGENT_PLANNER_CMD`
- `AGENT_CODER_CMD`
- `AGENT_REVIEWER_CMD`（任意）
- `CROSS_REPO_GH_TOKEN`（任意、クロスリポジトリPRでは推奨）

## 外部呼び出し受け口（ディスパッチ）

`repository_dispatch` を使って、このリポジトリの `自律エージェント ディスパッチ受け口` ワークフローを起動します。

`event_type`:

- `autonomous-agent-request`

`client_payload` の項目:

- `issue_number`（必須、整数）
- `project_id`（任意。`target_repo` 未指定時は必須）
- `target_repo`（任意。`project_id` 未指定時は必須）
- `base_branch`（任意）
- `branch_name`（任意）
- `no_sync`（任意、`true|false`）
- `source_repository`（任意、呼び出し元メタデータ）
- `request_id`（任意、リクエスト識別子）
- `dispatch_secret`（任意。`DISPATCH_SHARED_SECRET` が設定されている場合は必須）

例:

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <FLOW_SMITH_DISPATCH_TOKEN>" \
  https://api.github.com/repos/<owner>/FlowSmith/dispatches \
  -d '{
    "event_type": "autonomous-agent-request",
    "client_payload": {
      "issue_number": 123,
      "project_id": "sample-webapp",
      "source_repository": "your-org/your-repo",
      "request_id": "run-20260216-001"
    }
  }'
```

## 運用ガードレール

- プロジェクトごとに厳格な品質ゲート（`lint/typecheck/test/build`）を設定する
- マージ前に必ず人間レビューを要求する
- Issue は構造化フォーマット（目的、非目的、受け入れ条件）で記載する
- `.agent/workspaces/` と `.agent/runs/` をコミット対象から除外する
