# MCP stdio adapter

共通の uv project で起動し、`get_catalog`、`federation_search`、`federation_grep`、
`federation_read`、`federation_comments` を提供します。設定は
[setup](../../../docs/setup.md)、応答仕様は [Core 契約](../federation-core/README.md)
を参照してください。マシンから Catalog、store、検索サービス、credential を推測しません。

CONTEXT 用語候補は、適用範囲や注意点を含め現在の `definition` 全文を返します。
上限を超える定義はフィールド全体を理由付きで省略し、可能なら所在は残します。
記事本文 snippet は返しません。明示的な grep も所在だけを返します。

`federation_read(path)` はサーバ側の絶対パスを受け取り、
`{"path": "...", "content": "..."}` を返します。grep と同じ Catalog Package の
`PublicationScope` と publication boundary を適用します。drafts、tests、worklogs、
assets、dot directory などの除外ディレクトリ、Package 範囲外、symlink 逸脱、存在しない
ファイル、ディレクトリを拒否します。UTF-8 テキストで NUL を含まず、2,000,000 bytes
以下である必要があり、バイナリ拡張子も拒否します。不正な読み取りは MCP tool error です。

[Hermes provider](../hermes-federation/README.md) は `memory.federation.mcp_command`
経由で利用でき、`knowledge.example.invalid` のような合成 host への SSH stdio command
も指定できます。ローカル知識ファイルなしで Catalog を取得して prompt に注入し、既存の
3 つの federation tool を転送します。

## コメントの明示的な読み取り

`federation_comments(path)` は既存 `.md` 記事の絶対パスを受け取り、コメントの UTF-8
Markdown 原文を返します:
`{"path": ".../.comments/guide.md", "exists": true, "content": "..."}`。
コメントファイルがなければ `exists: false` と空の `content` を返します。
不正なパスは MCP tool error です。Catalog Package root と、選択した Catalog と同じ
ディレクトリの `drafts/` を許可します。相対パス、`.py` 記事、範囲外、symlink 逸脱を
拒否し、コメントディレクトリやファイル自体の symlink も拒否します。検索 backend は不要です。

[UI README](../../tools/knowledge-ui/README.ja.md#記事へのコメント) に Markdown 形式の
完全な例があります。各レコードは安定 ID、ISO 8601 作成日時、open/resolved 状態、
見出しテキストと anchor、引用、本文を持ちます。例えば `Evidence` 見出しへの open
コメントで `Synthetic passage.` を引用し、`Please clarify this example.` と依頼できます。
エージェントは破損レコードや手書きメモを含めファイル全体を受け取り、明示的に確認できます。
ツールは読み取り専用で、AI への自動送信は行いません。`federation_read` は引き続き
`.comments/` と drafts を拒否し、コメントを検索公開しません。Hermes provider はこの新規
ツールを転送しません。
