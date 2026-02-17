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
   ただし現時点の既定設定では Entire 連携は無効です。Actions 側でも `FLOWSMITH_ENABLE_ENTIRE=true` のときのみインストールします。

## Codex要約（コミットログ）

このフレームワークは、次の2層構成で Codex の検討を可視化します。

- コミットメッセージ: `Problem / Decision / Validation / Risk` の短要約 + 末尾に `Codex-Log-Reference`
- PR本文: `TL;DR / 要求の再解釈 / Decision Log / 試行ログ / 検証結果 / 残リスク / 証跡リンク`

- Codex へ与えた内容（Issue本文、planner prompt）
- Codex の検討結果（plan/review、coder 出力と品質ゲート結果）

既定設定は `.agent/pipeline.json` の `codex_commit_summary` セクションです。

- `enabled`: Codex要約の有効/無効
- `required`: 要約生成失敗時にジョブを失敗させるか
- `max_chars_per_item`: 各要約項目の最大文字数
- `max_attempts`: 要約対象の最大試行回数
- `max_points`: PR本文に展開する要点数
- `max_total_chars`: コミット追記要約の最大文字数

## Entire連携（任意）

`entire` セクションは残していますが、既定では `enabled: false` です。
再度有効化する場合は `.agent/pipeline.json` の `entire.enabled` と `entire.explicit_registration.enabled` を `true` にしてください。

## PR必須項目と ai-logs

PR本文には次の必須セクションを出力します。

- 指示内容（必須）
- 検証コマンド（必須）
- ログの場所（必須）

また、実行ログは対象リポジトリ内の `ai-logs/issue-<番号>-<timestamp>/` へ生成したうえで、
専用ブランチ（既定: `agent-ai-logs`）へ集約して `index.md` リンクをPR本文へ記載します。
このため、実装PR用ブランチ（`main` 向け）には `ai-logs/` を含めません。
`ai_logs.required: true` または `ai_logs.publish.required: true` の場合、保存/集約に失敗するとパイプラインは失敗します。

`ai_logs` 設定例（`.agent/pipeline.json`）:

- `enabled`: `ai-logs` 保存の有効/無効
- `required`: 保存失敗時にジョブを失敗させるか
- `path`: 保存先ディレクトリ（リポジトリ相対。テンプレート変数可）
- `index_file`: インデックスファイル名（既定: `index.md`）
- `publish.mode`: `same-branch` または `dedicated-branch`
- `publish.branch`: `dedicated-branch` 利用時の集約先ブランチ名
- `publish.required`: 集約先ブランチへの反映失敗時にジョブを失敗させるか
- `publish.commit_message`: 集約ブランチ用コミットメッセージテンプレート

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

`--project` を使わず外部リポジトリを実行する場合は、`.agent/pipeline.json` の `target_repo_defaults` が自動適用されます。
この仕組みで、リポジトリ追加のたびに `.agent/projects.json` を増やさなくても、共通の安全設定を標準化できます。

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
- `OPENAI_API_KEY`（任意。`codex` の API キー認証に使用）
- `CODEX_AUTH_JSON_B64`（任意。`~/.codex/auth.json` を base64 化して設定する暫定手段）
- `AGENT_SETUP_SCRIPT`（任意。追加ツールを入れるためのシェルスクリプト）
- `CROSS_REPO_GH_TOKEN`（任意、クロスリポジトリPRでは推奨）

ワークフロー内の標準インストール手順:

1. `actions/setup-python@v5` で Python 3.12 をセットアップ
2. `actions/setup-node@v4` で Node.js 22 をセットアップ
3. `npm install -g @openai/codex` で `codex` CLI をインストール（既存ならスキップ）
4. `CODEX_AUTH_JSON_B64` があれば `~/.codex/auth.json` を復元し、なければ `OPENAI_API_KEY` で `codex login --with-api-key` を実行
5. `AGENT_SETUP_SCRIPT` が設定されていれば実行（例: `uv` / `pnpm` / 独自CLIの導入）
6. `AGENT_PLANNER_CMD` / `AGENT_CODER_CMD` / `AGENT_REVIEWER_CMD` の実行コマンド存在を事前検証
7. `Entire CLI` をインストールしてから `scripts/agent_pipeline.py` を起動

`CODEX_AUTH_JSON_B64` は検証用途の暫定手段です。安定運用は `OPENAI_API_KEY` の利用を推奨します。

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
