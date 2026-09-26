# knowledge-management

[English](README.md)

人間とAI agentが作成・レビュー・検索・閲覧できる知識を保持するための共有ツール群: Core、MCP server、ブラウザUI、indexer、checker、agent skill、および共有ルール。利用者の知識は別個のプライベートstoreに保持し、このcheckoutを参照するように設定するため、`git pull` で更新を受け取れます。

**リポジトリの作成は作者により承認されていますが、プロジェクトのライセンスは未定です。**
このリポジトリによってプロジェクト全体のライセンスが付与されることはありません。[来歴と注記](docs/provenance.md)を参照してください。

## インストール

Linux、Python 3.12+、[uv](https://docs.astral.sh/uv/)、Deno 2 が必要です。

```bash
git clone https://github.com/impactaky/knowledge-management.git
cd knowledge-management
export UV_CACHE_DIR="$PWD/.cache/uv" DENO_DIR="$PWD/.cache/deno"
uv sync --locked --project foundation/tools/knowledge-ui
deno task --config foundation/tools/knowledge-ui/deno.json build-browser-assets
```

任意: 最初にsyntheticサンプルを試す場合は、
`FEDERATION_CATALOG="$PWD/examples/minimal/CATALOG.md" bash foundation/tools/knowledge-ui/run.sh`
を実行して <http://127.0.0.1:7776> を開きます。

## storeの作成

storeは独立したGitリポジトリです。このcheckoutの内部には配置しないでください。

```bash
cp -R templates/store ~/my-knowledge
git -C ~/my-knowledge init
```

`~/my-knowledge/CATALOG.md` の `shared-rules` のpathを、このcheckoutの `foundation/INDEX.md` の絶対pathに変更します:

```markdown
- [shared-rules](/home/you/knowledge-management/foundation/INDEX.md) — …
```

## このマシンの設定

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management"
cp .env.example "${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/runtime.env"
```

`runtime.env` で `FEDERATION_CATALOG=/home/you/my-knowledge/CATALOG.md` を設定します。
`run.sh` はデフォルトでこのファイルを読み込みます。

## チェックと閲覧

```bash
uv run --locked --project foundation/tools/knowledge-ui \
  python foundation/tools/knowledge-ui/check.py --catalog ~/my-knowledge/CATALOG.md
bash foundation/tools/knowledge-ui/run.sh
```

checkerが `Advisory` なしで `0 issue(s)` を報告することを確認してください。ブラウザは <http://127.0.0.1:7776> で動作します。閲覧と検索は追加サービスなしで機能しますが、セマンティック検索にはMeilisearchとOllamaが必要です（[optional backend](docs/setup.md#optional-backend)）。

## agentの接続

**MCP server** (Claude Codeの例。他のクライアントも同じコマンドを使用します。[Core and MCP](docs/setup.md#core-and-mcp) を参照):

```bash
claude mcp add knowledge-federation -s user \
  -e UV_CACHE_DIR=$HOME/knowledge-management/.cache/uv -- \
  uv run --locked --directory $HOME/knowledge-management \
  --project foundation/tools/knowledge-ui \
  --env-file $HOME/.config/knowledge-management/runtime.env \
  python foundation/integrations/federation-mcp/server.py
```

agentは `get_catalog`、`federation_search`、`federation_grep` を利用できるようになります。

**Skills**: [skills/](skills/) 以下の各ディレクトリを通常使用しているskillツールでインストールするか、agentのskillsディレクトリへsymlinkします。`SKILL.md` 単体ではなく、必ずディレクトリ全体をインストールしてください（[skill setup](docs/workflow.md#skill-setup)）。

**Instructions**: storeの `AGENTS.md` には、agentがshared-rulesの `docs/agent-setup.md` に従うよう既に記述されています。他のリポジトリからもこのstoreを利用するには、ユーザレベルのagent instructionsにも同様の行を追加してください。

## 日常的な利用

普段の言葉でagentに指示します:

| 発話例 | Skill | 結果 |
|---|---|---|
| 「Xを調べて」 | `survey` | 出典付きの記事ドラフト |
| 「この論文を要約して」 | `summarize-source` | その情報源のみに基づく忠実な要約ドラフト |
| 「Xについて説明させて」 | `teach` | あなたの知識をインタビュー形式で引き出してドラフト化 |
| 「記事にして」 | `write-article` | レビュー後、ドラフトを `articles/` 配下にstock |
| 「これ覚えて」 | `distill` | 恒久的な規則や用語を適切なPackageへ記録 |
| 「このトピック / 本を勉強したい」 | `study-topic` / `study-book` | 段階的に進められる学習マップ |

ローカルパスやデバイスIPなどのリポジトリ固有のメモは、
`projects/<repo>/rules.md` に保持します（[projects contract](foundation/docs/projects.md)）。

## 更新

```bash
git -C ~/knowledge-management pull
# update installed skills with your skill tool
uv run --locked --project foundation/tools/knowledge-ui \
  python foundation/tools/knowledge-ui/check.py --catalog ~/my-knowledge/CATALOG.md
```

checkerが `Advisory` を出力した場合はそれを適用してください。[store-changes.md](foundation/docs/store-changes.md) にstore側で必要な各変更が記載されています。

## その他のドキュメント

- [インストール、設定、MCP、任意の検索サービス](docs/setup.md)
- [理解 → ドラフト → 人手によるレビュー → stock → 検索 → 閲覧](docs/workflow.md)
- [Delivery coordination、order、Agent Exchange](docs/setup.md#delivery-and-agent-exchange-configuration)
- [検索と公開の契約](foundation/integrations/federation-core/README.md)
- [抽出範囲と受け入れのエビデンス](docs/extraction.md)

## メンテナ向け: このリポジトリの記事

ルートの [CATALOG.md](CATALOG.md) と `articles/` は、このリポジトリ自体の記事のためのものです。`export FEDERATION_CATALOG="$PWD/CATALOG.md"` で選択します（MCP serverの環境変数にも同じ絶対pathを設定し、再起動してください）。
`drafts/` でドラフトを作成し、レビュー済みの記事を `articles/<theme>/` 配下にstockします。

## 検証

```bash
uv run --locked --project foundation/tools/knowledge-ui pytest foundation/tests -q
uv run --locked --project foundation/tools/knowledge-ui python scripts/smoke.py
```

テストは一時的なsynthetic storeとモックバックエンドを使用し、稼働中の検索サービスには一切アクセスしません。テストスイートにはAgent Exchangeとorderのシェル契約が含まれています。使い捨ての実バックエンドによるスモークテストについては [setup](docs/setup.md#optional-backend) を参照してください。
