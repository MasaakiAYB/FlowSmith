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
- `.github/workflows/autonomous-agent-dispatch.yml`: Issue起点の外部呼び出しディスパッチ受け口
- `.github/workflows/autonomous-agent-feedback-dispatch.yml`: PRフィードバック起点の外部呼び出しディスパッチ受け口
- `.github/workflows/autonomous-agent-runner.yml`: 共通実行ワークフロー（`workflow_call`）
- `scripts/agent_pipeline.py`: Issue から PR までを実行するオーケストレーター

## AGENTS.md 導入

このリポジトリには `AGENTS.md` を配置しており、Codex 実行時のプロジェクト固有ルールを定義しています。

- 反映タイミング: `AGENTS.md` 更新後の「次回」Codex 実行から有効
- 主な定義内容: トリガー条件、ロック運用、ログ保存方針、最低限の検証手順

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
- 画像の投入先（既定）: `ui_evidence.repo_dir`（`.flowsmith/ui-evidence/`。対象リポジトリ配下で書き込み可能）
- 画像証跡の保存先（既定）: `ui_evidence.artifact_dir`（`run_dir/ui-evidence/`）
- 必須条件を満たさない場合: コミット前にパイプラインを失敗させる
- 条件を満たす場合: コミットメッセージ末尾に `UI-Evidence` セクションを自動追記し、`ai-logs` ブランチ上の画像リンクを出力する
- PR本文では `ai-logs` ブランチ上の画像をインライン表示する（`artifact` は補助情報として保持）

`ui_evidence` 設定例（`.agent/pipeline.json`）:

- `enabled`: UI証跡チェックの有効/無効
- `required`: UI変更時に画像が無い場合に失敗させるか
- `delivery_mode`: `artifact-only`（既定）または `commit`
- `repo_dir`: 証跡画像を投入する対象リポジトリ内ディレクトリ（既定: `.flowsmith/ui-evidence`）
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
UI証跡画像（`run_dir/ui-evidence`）も `ai-logs/.../ui-evidence/` として同じ専用ブランチへ集約し、
PR本文で直接プレビューできるURLを埋め込みます。
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
- エージェントPRには `agent/` 系ラベルを必ず付与します（既定で `pr.labels_required=true`。付与できない場合はジョブ失敗）。
- PR本文は OJPP の構成に合わせ、`変更内容 / レビュー要約（変更種別の自動判定・自動チェック・手動チェック） / 関連 Issue / スクリーンショット / AIエージェント実行ログ` を出力します。

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

## GitHub Actions利用（詳細）

FlowSmith を運用するときは、次の2層で設定します。

1. FlowSmith リポジトリ側: 受け口ワークフローと実行環境を管理
2. 呼び出し側リポジトリ: `repository_dispatch` を送るトリガーワークフローを実装

### FlowSmithリポジトリ側のワークフロー

- Issue受け口: `.github/workflows/autonomous-agent-dispatch.yml`
- PRフィードバック受け口: `.github/workflows/autonomous-agent-feedback-dispatch.yml`
- 共通ランナー: `.github/workflows/autonomous-agent-runner.yml`
- 手動実行: `.github/workflows/autonomous-agent-pr.yml`

`autonomous-agent-runner.yml` の標準処理:

1. Python/Node.js と日本語フォントをセットアップ
2. `codex` CLI をインストール
3. `codex_auth_json_b64`（workflow input）-> `CODEX_AUTH_JSON_B64`（Secret）-> `OPENAI_API_KEY` の優先順で認証
4. `AGENT_SETUP_SCRIPT`（任意）を実行
5. `AGENT_PLANNER_CMD` / `AGENT_CODER_CMD` / `AGENT_REVIEWER_CMD` を事前検証
6. `scripts/agent_pipeline.py` を実行して commit / push / PR作成・更新
7. 実行ログを artifact 保存

FlowSmith側で参照する Secrets:

- `AGENT_PLANNER_CMD`（必須）
- `AGENT_CODER_CMD`（必須）
- `AGENT_REVIEWER_CMD`（任意）
- `OPENAI_API_KEY`（推奨）
- `CODEX_AUTH_JSON_B64`（代替）
- `AGENT_SETUP_SCRIPT`（任意）
- `CROSS_REPO_GH_TOKEN`（クロスリポジトリ更新時に推奨）
- `DISPATCH_SHARED_SECRET`（受け口保護に推奨）

`CODEX_AUTH_JSON_B64` は検証用途の代替手段です。安定運用は `OPENAI_API_KEY` を推奨します。

### 呼び出し側リポジトリで設定する Secrets/Variables

FlowSmithを呼び出す側（業務アプリ側）には、次を設定します。

- Secret `FLOW_SMITH_DISPATCH_TOKEN`: FlowSmith リポジトリに `repository_dispatch` 可能なトークン
- Secret `FLOW_SMITH_DISPATCH_SECRET`: FlowSmith 側の `DISPATCH_SHARED_SECRET` と同じ値（設定している場合）
- Secret `FLOW_SMITH_CODEX_AUTH_JSON_B64`: 利用者 `auth.json` の base64（任意。FlowSmith側へそのまま転送）
- Variable `FLOW_SMITH_REPO`: `owner/FlowSmith` 形式（例: `MasaakiAYB/FlowSmith`）
- Variable `FLOW_SMITH_PROJECT_ID`: FlowSmith の `.agent/projects.json` を使うときのみ指定

FlowSmith側（このリポジトリ）でのロック制御用 Variables:

- `FLOWSMITH_LOCK_LABEL`（既定: `agent/running`）
- `FLOWSMITH_MAX_PARALLEL_PER_REPO`（既定: `2`）
- `FLOWSMITH_LOCK_POLL_SECONDS`（既定: `20`）
- `FLOWSMITH_LOCK_TIMEOUT_MINUTES`（既定: `180`）
- `FLOWSMITH_LOCK_STALE_MINUTES`（既定: `360`）
- `FLOWSMITH_SERVICE_LABEL_PREFIX`（既定: `agent/service:`）
- `FLOWSMITH_OPERATION_LABEL_PREFIX`（既定: `agent/op:`）
- `FLOWSMITH_OPERATION_COOLDOWN_MINUTES`（既定: `30`）

トリガーワークフローの最小 `permissions`:

- Issue起点: `contents: read`
- PRフィードバック起点: `contents: read`, `pull-requests: read`, `issues: read`

### キュー＋排他（ラベルロック）

`autonomous-agent-runner.yml` では `scripts/agent_lock.py` を使って、次を制御します。

1. 同一Issueの同時実行禁止: `concurrency`（Issue単位） + `agent/running` ラベル
2. 同一リポジトリの同時実行上限: `FLOWSMITH_MAX_PARALLEL_PER_REPO`（既定2）
3. 同一サービス操作の連打防止: `agent/service:*` + `agent/op:*` ラベルでクールダウン判定

ロックの流れ:

1. `acquire-lock` ジョブが対象Issueへ `agent/running` を付与
2. `run-agent` ジョブで実装・PR更新を実行
3. `release-lock` ジョブ（`always`）で `agent/running` を除去

操作クールダウンを使う場合は、対象Issueに次のラベルを付けます。

- `agent/service:<service-name>` 例: `agent/service:api`
- `agent/op:<operation-name>` 例: `agent/op:restart`

`release-lock` で操作記録コメントを残し、次回 `acquire-lock` 時に
`FLOWSMITH_OPERATION_COOLDOWN_MINUTES`（既定30分）以内なら待機します。

### 呼び出し側Actions実装例 1: Issue作成で自動起動

ファイル例: `.github/workflows/flowsmith-on-issue.yml`

```yaml
name: Trigger FlowSmith On Issue

on:
  issues:
    types:
      - opened
      - labeled

permissions:
  contents: read

jobs:
  dispatch:
    if: >-
      ${{
        contains(github.event.issue.labels.*.name, 'agent-task') ||
        contains(github.event.issue.labels.*.name, 'agent/')
      }}
    runs-on: ubuntu-latest
    env:
      FLOW_SMITH_REPO: ${{ vars.FLOW_SMITH_REPO || 'MasaakiAYB/FlowSmith' }}
      FLOW_SMITH_PROJECT_ID: ${{ vars.FLOW_SMITH_PROJECT_ID || '' }}
    steps:
      - name: Validate dispatch token
        run: |
          if [ -z "${{ secrets.FLOW_SMITH_DISPATCH_TOKEN }}" ]; then
            echo "Secret FLOW_SMITH_DISPATCH_TOKEN is required." >&2
            exit 1
          fi

      - name: Dispatch to FlowSmith
        env:
          FLOW_SMITH_DISPATCH_TOKEN: ${{ secrets.FLOW_SMITH_DISPATCH_TOKEN }}
          FLOW_SMITH_DISPATCH_SECRET: ${{ secrets.FLOW_SMITH_DISPATCH_SECRET || '' }}
        run: |
          set -euo pipefail

          payload="$(jq -n \
            --argjson issue_number "${{ github.event.issue.number }}" \
            --arg target_repo "${{ github.repository }}" \
            --arg base_branch "${{ github.event.repository.default_branch }}" \
            --arg project_id "${FLOW_SMITH_PROJECT_ID}" \
            --arg source_repository "${{ github.repository }}" \
            --arg request_id "issue-${{ github.event.issue.number }}-run-${{ github.run_id }}" \
            --arg dispatch_secret "${FLOW_SMITH_DISPATCH_SECRET}" \
            '{
              event_type: "autonomous-agent-issue-request",
              client_payload: (
                {
                  issue_number: $issue_number,
                  target_repo: $target_repo,
                  base_branch: $base_branch,
                  source_repository: $source_repository,
                  request_id: $request_id,
                  dispatch_secret: $dispatch_secret
                } + (if $project_id != "" then {project_id: $project_id} else {} end)
              )
            }'
          )"

          curl -fsSL -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${FLOW_SMITH_DISPATCH_TOKEN}" \
            "https://api.github.com/repos/${FLOW_SMITH_REPO}/dispatches" \
            -d "$payload"
```

### 呼び出し側Actions実装例 2: PRレビュー/コメントで再実行

ファイル例: `.github/workflows/flowsmith-on-pr-feedback.yml`

```yaml
name: Trigger FlowSmith On PR Feedback

on:
  pull_request_review:
    types:
      - submitted
  issue_comment:
    types:
      - created

permissions:
  contents: read
  pull-requests: read
  issues: read

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - name: Resolve PR context
        id: ctx
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail

          event_name="${{ github.event_name }}"
          pr_number=""
          trigger_reason=""

          if [ "$event_name" = "pull_request_review" ]; then
            state="${{ github.event.review.state }}"
            if [ "$state" != "changes_requested" ] && [ "$state" != "commented" ]; then
              echo "changes_requested / commented 以外はスキップします。"
              exit 0
            fi
            pr_number="${{ github.event.pull_request.number }}"
            trigger_reason="review:${state}"
          elif [ "$event_name" = "issue_comment" ]; then
            if [ "${{ github.event.issue.pull_request.url != '' }}" != "true" ]; then
              echo "PR以外のIssueコメントは対象外です。"
              exit 0
            fi
            body="${{ github.event.comment.body }}"
            if ! echo "$body" | grep -Eiq '^/agent\s+(fix|retry)\b'; then
              echo "/agent fix または /agent retry 指示のみ対象です。"
              exit 0
            fi
            pr_number="${{ github.event.issue.number }}"
            trigger_reason="comment-command"
          else
            exit 0
          fi

          pr_json="$(gh api "repos/${GITHUB_REPOSITORY}/pulls/${pr_number}")"
          head_ref="$(echo "$pr_json" | jq -r '.head.ref')"
          base_ref="$(echo "$pr_json" | jq -r '.base.ref')"
          body="$(echo "$pr_json" | jq -r '.body // \"\"')"

          issue_number="$(echo "$head_ref" | sed -n 's|.*issue-\([0-9]\+\).*|\1|p' | head -n 1)"
          if [ -z "$issue_number" ]; then
            issue_number="$(printf '%s' "$body" | grep -Eio 'closes #[0-9]+' | head -n 1 | grep -Eo '[0-9]+' || true)"
          fi
          if [ -z "$issue_number" ]; then
            echo "Issue番号を特定できないためスキップします。"
            exit 0
          fi

          echo "pr_number=${pr_number}" >> "$GITHUB_OUTPUT"
          echo "issue_number=${issue_number}" >> "$GITHUB_OUTPUT"
          echo "head_ref=${head_ref}" >> "$GITHUB_OUTPUT"
          echo "base_ref=${base_ref}" >> "$GITHUB_OUTPUT"
          echo "trigger_reason=${trigger_reason}" >> "$GITHUB_OUTPUT"

      - name: Dispatch to FlowSmith
        if: ${{ steps.ctx.outputs.pr_number != '' }}
        env:
          FLOW_SMITH_REPO: ${{ vars.FLOW_SMITH_REPO || 'MasaakiAYB/FlowSmith' }}
          FLOW_SMITH_PROJECT_ID: ${{ vars.FLOW_SMITH_PROJECT_ID || '' }}
          FLOW_SMITH_DISPATCH_SECRET: ${{ secrets.FLOW_SMITH_DISPATCH_SECRET || '' }}
          FLOW_SMITH_DISPATCH_TOKEN: ${{ secrets.FLOW_SMITH_DISPATCH_TOKEN }}
        run: |
          set -euo pipefail

          payload="$(jq -n \
            --arg issue_number "${{ steps.ctx.outputs.issue_number }}" \
            --arg pr_number "${{ steps.ctx.outputs.pr_number }}" \
            --arg source_repository "${GITHUB_REPOSITORY}" \
            --arg project_id "${FLOW_SMITH_PROJECT_ID}" \
            --arg branch_name "${{ steps.ctx.outputs.head_ref }}" \
            --arg base_branch "${{ steps.ctx.outputs.base_ref }}" \
            --arg dispatch_secret "${FLOW_SMITH_DISPATCH_SECRET}" \
            --arg request_id "pr-feedback-${{ github.run_id }}-${{ github.run_attempt }}" \
            --arg feedback_text "Triggered by: ${{ steps.ctx.outputs.trigger_reason }}" \
            '{
              event_type: "autonomous-agent-feedback-request",
              client_payload: {
                issue_number: ($issue_number | tonumber),
                target_repo: $source_repository,
                project_id: $project_id,
                branch_name: $branch_name,
                base_branch: $base_branch,
                feedback_pr_number: ($pr_number | tonumber),
                feedback_text: $feedback_text,
                source_repository: $source_repository,
                request_id: $request_id,
                dispatch_secret: $dispatch_secret
              }
            }'
          )"

          curl -fsSL -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${FLOW_SMITH_DISPATCH_TOKEN}" \
            "https://api.github.com/repos/${FLOW_SMITH_REPO}/dispatches" \
            -d "$payload"
```

### 呼び出し側Actions実装例 3: 手動再実行（workflow_dispatch）

ファイル例: `.github/workflows/flowsmith-manual-dispatch.yml`

```yaml
name: Trigger FlowSmith Manually

on:
  workflow_dispatch:
    inputs:
      mode:
        description: "issue か feedback"
        required: true
        default: "issue"
        type: choice
        options:
          - issue
          - feedback
      issue_number:
        description: "対象Issue番号"
        required: true
        type: string
      feedback_pr_number:
        description: "feedback時のPR番号"
        required: false
        default: ""
        type: string
      branch_name:
        description: "feedback時のhead branch"
        required: false
        default: ""
        type: string
      base_branch:
        description: "feedback時のbase branch"
        required: false
        default: ""
        type: string

permissions:
  contents: read
  pull-requests: read
  issues: read

jobs:
  dispatch:
    runs-on: ubuntu-latest
    env:
      FLOW_SMITH_REPO: ${{ vars.FLOW_SMITH_REPO || 'MasaakiAYB/FlowSmith' }}
      FLOW_SMITH_PROJECT_ID: ${{ vars.FLOW_SMITH_PROJECT_ID || '' }}
    steps:
      - name: Dispatch
        env:
          FLOW_SMITH_DISPATCH_TOKEN: ${{ secrets.FLOW_SMITH_DISPATCH_TOKEN }}
          FLOW_SMITH_DISPATCH_SECRET: ${{ secrets.FLOW_SMITH_DISPATCH_SECRET || '' }}
        run: |
          set -euo pipefail
          if [ -z "${FLOW_SMITH_DISPATCH_TOKEN:-}" ]; then
            echo "FLOW_SMITH_DISPATCH_TOKEN が必要です。" >&2
            exit 1
          fi

          mode="${{ inputs.mode }}"
          payload="$(jq -n \
            --argjson issue_number "${{ inputs.issue_number }}" \
            --arg target_repo "${{ github.repository }}" \
            --arg project_id "${FLOW_SMITH_PROJECT_ID}" \
            --arg base_branch "${{ github.event.repository.default_branch }}" \
            --arg dispatch_secret "${FLOW_SMITH_DISPATCH_SECRET}" \
            --arg request_id "manual-${{ github.run_id }}-${{ github.run_attempt }}" \
            '{
              event_type: "autonomous-agent-issue-request",
              client_payload: {
                issue_number: $issue_number,
                target_repo: $target_repo,
                project_id: $project_id,
                base_branch: $base_branch,
                source_repository: $target_repo,
                request_id: $request_id,
                dispatch_secret: $dispatch_secret
              }
            }'
          )"

          if [ "$mode" = "feedback" ]; then
            if [ -z "${{ inputs.feedback_pr_number }}" ] || [ -z "${{ inputs.branch_name }}" ] || [ -z "${{ inputs.base_branch }}" ]; then
              echo "feedbackモードでは feedback_pr_number / branch_name / base_branch が必須です。" >&2
              exit 1
            fi
            payload="$(jq -n \
              --argjson issue_number "${{ inputs.issue_number }}" \
              --argjson feedback_pr_number "${{ inputs.feedback_pr_number }}" \
              --arg branch_name "${{ inputs.branch_name }}" \
              --arg base_branch "${{ inputs.base_branch }}" \
              --arg target_repo "${{ github.repository }}" \
              --arg project_id "${FLOW_SMITH_PROJECT_ID}" \
              --arg dispatch_secret "${FLOW_SMITH_DISPATCH_SECRET}" \
              --arg request_id "manual-feedback-${{ github.run_id }}-${{ github.run_attempt }}" \
              '{
                event_type: "autonomous-agent-feedback-request",
                client_payload: {
                  issue_number: $issue_number,
                  target_repo: $target_repo,
                  project_id: $project_id,
                  feedback_pr_number: $feedback_pr_number,
                  branch_name: $branch_name,
                  base_branch: $base_branch,
                  source_repository: $target_repo,
                  request_id: $request_id,
                  dispatch_secret: $dispatch_secret
                }
              }'
            )"
          fi

          curl -fsSL -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${FLOW_SMITH_DISPATCH_TOKEN}" \
            "https://api.github.com/repos/${FLOW_SMITH_REPO}/dispatches" \
            -d "$payload"
```

### ディスパッチペイロード仕様

`repository_dispatch` の入口は2種類です。

- Issue起点: `autonomous-agent-issue-request`
- PRフィードバック起点: `autonomous-agent-feedback-request`

共通ルール:

- `issue_number` は必須（整数）
- `project_id` または `target_repo` のどちらかは必須
- FlowSmith側で `DISPATCH_SHARED_SECRET` を使う場合は `dispatch_secret` が必須
- `codex_auth_json_b64` は任意。指定時は FlowSmith 側で `CODEX_AUTH_JSON_B64` Secret より優先して使用

Issue起点の禁止項目:

- `feedback_pr_number`
- `feedback_text`

PRフィードバック起点の必須項目:

- `issue_number`
- `feedback_pr_number`
- `branch_name`
- `base_branch`

APIを直接叩く場合の例:

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <FLOW_SMITH_DISPATCH_TOKEN>" \
  https://api.github.com/repos/<owner>/FlowSmith/dispatches \
  -d '{
    "event_type": "autonomous-agent-feedback-request",
    "client_payload": {
      "issue_number": 123,
      "target_repo": "your-org/your-repo",
      "feedback_pr_number": 45,
      "branch_name": "agent/your-repo-issue-123-feature",
      "base_branch": "main",
      "feedback_text": "Triggered by: review:changes_requested",
      "source_repository": "your-org/your-repo",
      "request_id": "pr-feedback-45-run-2"
    }
  }'
```

## PRレビュー指摘の自動再実行

`scripts/agent_pipeline.py` は次の入力を受け取ると、PRレビュー/コメントの改善指摘を収集して再実装に反映します。

- `--feedback-pr-number <PR番号>`: 対象PRから `changes_requested` / レビューコメント / PRコメントを自動抽出
- `--feedback-file <path>` または `--feedback-text <text>`: 手動で改善指摘を追加
- `--feedback-pr-number` 指定時は、`--branch-name` 未指定でも対象PRの head ブランチを自動採用（既存PRに追記）

抽出した内容は `run_dir/external_feedback_pr.md` と `external_feedback_status.md` に保存され、
Planner/Coder/Reviewer プロンプトへ反映されます。

呼び出し側リポジトリでの自動トリガー例:

- `docs/examples/trigger-flowsmith-on-issue.yml`
- `docs/examples/trigger-flowsmith-on-pr-feedback.yml`

## 運用ガードレール

- プロジェクトごとに厳格な品質ゲート（`lint/typecheck/test/build`）を設定する
- マージ前に必ず人間レビューを要求する
- Issue は構造化フォーマット（目的、非目的、受け入れ条件）で記載する
- `.agent/workspaces/` と `.agent/runs/` をコミット対象から除外する
