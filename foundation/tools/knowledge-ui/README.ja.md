# 知識ブラウザと indexer

FastAPI のブラウザ、Markdown renderer、Catalog indexer、読み取り専用 checker です。
共通の [setup guide](../../../docs/setup.md) を参照してください。Python 依存関係は
ここでロックし、Deno で固定バージョンのブラウザ asset をビルドします。実行時の CDN
依存はありません。drafts や learning map の表示は、検索公開を意味しません。

検索応答には、現在の CONTEXT 用語定義全文とその所在が含まれます。サイズ制限と
フィールド全体の省略は [Core 契約](../../integrations/federation-core/README.md)
に従います。記事 snippet は UI 独自の表示です。

## 記事へのコメント

`/?file=<記事の絶対パス>`（必要なら `#anchor` 付き）を開くと、その記事をコメント欄付きで表示した状態で起動します。アドレスバーは選択中の記事に追従するので、そのままリンクとしてコピーできます。`/article?file=...` は記事単体の表示で、コメント欄はありません。
ヘッダーの ◧ / ◨ ボタンで、左のナビゲーションと右のコメント欄をそれぞれ折りたためます。設定はブラウザに保存されます。

Markdown preview で文章を選択し、**選択箇所にコメント**から本文を入力して保存します。
隣の一覧は記事を読み込むたびにファイルから取得し、引用、本文、作成日時、open/resolved
状態を表示します。見出しボタンで元の節へ移動し、**解決**、**再開**、**削除**でファイルを
更新できます。記事編集で見出し anchor が変わっても、保存した引用は残ります。

対象は Catalog Package の `.md` 記事と、選択した Catalog と同じディレクトリの
`drafts/` です。実行可能な `.py` 記事は対象外です。既存記事の絶対パスが必要で、
範囲外パスと symlink 逸脱を拒否します。`articles/topic/guide.md` のコメントは
`articles/topic/.comments/guide.md` に保存します。別の保存先設定はありません。
Git で追跡する前提ですが、UI は Git 操作を行いません。コメントはツリー、検索 index、
publication には現れません。

各ファイルは UTF-8 Markdown です。例:

```markdown
# Article comments

<!-- comment:0123456789abcdef0123456789abcdef -->
{"id": "0123456789abcdef0123456789abcdef", "created": "2026-01-01T09:00:00+00:00", "status": "open", "heading": "Evidence", "anchor": "evidence"}

### Quote
> Synthetic passage.

### Comment
    Please clarify this example.
<!-- /comment:0123456789abcdef0123456789abcdef -->
```

境界マーカーを安定 ID で対応付けます。JSON メタデータ行に ISO 8601 の作成日時、状態、
見出しテキスト、anchor を保存します。引用は各行を `> `、本文は空行を含め各行を
4 スペースで始めます。このためコメント本文が構造マーカーになることはありません。
未知・破損レコードや手書きメモは変更時にもそのまま保持し、有効なレコードだけを UI に
表示します。破損レコードを再表示したい場合は Markdown ファイルを修復してください。
書き込みは atomic replacement と POSIX のディレクトリ advisory lock で複数 UI worker
間を直列化します。外部エディタとの同時更新は別途調整が必要です。

API は `GET /api/comments?file=...`、JSON `{file, heading, anchor, quote, body}` を
受け取る `POST /api/comments`、`{file, status}`（`open` / `resolved`）を受け取る
`PATCH /api/comments/{id}`、`DELETE /api/comments/{id}?file=...` です。
いずれも `{comments: [...]}` を返します。AI に対応を依頼する際は MCP の
[`federation_comments(path)`](../../integrations/federation-mcp/README.md) を使います。
