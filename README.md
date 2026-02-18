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

- コミットメッセージ: `Codex-Context` 配下に `指示 / 試行錯誤 / 設計根拠` の固定3見出し + 末尾に `Codex-Log-Reference`
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

## UI変更時の画像証跡（コミットメッセージ必須）

UI変更を含むコミットでは、変更箇所のスクリーンショットまたはアニメーションGIFを
Workflow Artifact へ保存することを必須化しています（実装ブランチには含めません）。

- UI変更が検出される条件: `ui_evidence.ui_extensions` または `ui_evidence.ui_path_keywords`
- 画像証跡として認める条件: `ui_evidence.image_extensions`
- 画像証跡の保存先（既定）: `run_dir/ui-evidence/`（artifact-only）
- 必須条件を満たさない場合: コミット前にパイプラインを失敗させる
- 条件を満たす場合: コミットメッセージ末尾に `UI-Evidence` セクションを自動追記し、PR本文に artifact 参照情報を出力する

`ui_evidence` 設定例（`.agent/pipeline.json`）:

- `enabled`: UI証跡チェックの有効/無効
- `required`: UI変更時に画像が無い場合に失敗させるか
- `delivery_mode`: `artifact-only`（既定）または `commit`
- `artifact_dir`: 実行ログ配下での証跡画像ディレクトリ
- `ui_extensions`: UI変更として判定する拡張子
- `ui_path_keywords`: UI変更として判定するパスキーワード
- `image_extensions`: 証跡画像として許可する拡張子
- `evidence_path_keywords`: リポジトリ内画像を証跡として扱うパスキーワード
- `evidence_name_keywords`: リポジトリ内画像を証跡として扱うファイル名キーワード
- `max_ui_files`: コミットメッセージに列挙するUI変更ファイルの最大件数
- `max_images`: コミットメッセージに列挙する証跡画像の最大件数

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

## PRタイトルとラベル方針（OJPP準拠）

- PRタイトルは `issue_title` から装飾プレフィックス（例: `[エージェント作業]`）を除去して自動整形します。
- `feat:` / `fix:` など Conventional 形式が未指定の場合は、Issueタイトル/ラベルから推定した種別を付与します（既定は `feat:`）。
- エージェントPRには `agent/` ラベルを付与します（付与失敗時は警告ログのみで続行）。
- PR本文は OJPP の構成に合わせ、`変更内容 / 関連 Issue / 変更の種類 / チェックリスト / スクリーンショット / AIエージェント実行ログ` を出力します。

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
8. 実行後、`.agent/runs/...` を `agent-run-<run_id>-<run_attempt>` artifact として保存（UI証跡を含む）

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
