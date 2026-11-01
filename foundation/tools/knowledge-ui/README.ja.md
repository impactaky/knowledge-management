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

## レビューラウンド

`article_url.py <絶対パス.md>` はコメント欄付きURLを出し、設定済みの
`KNOWLEDGE_REVIEW_NOTIFIER` があれば依頼元の会話を持ち主として記録します。
**修正して**・**承認**・**差戻し**（戻す先のラウンドを指定）を選び、**送信**します。
openコメントと現在の記事を一度確定します。返事待ちでもコメントは追加できますが、
次の送信には返事が必要です。承認は返事なしでレビュー完了となり、再送信には新たな
claimが必要です。承認はStockの許可ではなく、Stockの判定は会話で確かめます。

宛先のタイトル・状態と配送結果を表示します。作業中の持ち主は最大30分待ち、blockedは
承認待ちと表示します。未設定・不在・配送失敗時は、表示された1行を会話へ貼れます。
通知に含む可変部分はパス・ラウンド・判定だけで、コメント本文は含めません。
summaryは記事の先頭にも表示し、**?** を開くと問いと答え欄が現れます。
答えは任意の `reply_to` メタデータ付きコメントです。既存sidecarも引き続き読めます。
partial/skippedの理由はコメントに表示し、doneのreadingは記録だけに残します。
**差分** は最新の送信時のMarkdownと現在を比較し、追加は緑、削除は赤の取り消し線です
（日本語は文字、英数字は語単位）。書きかけのコメント・引用・答えは、ブラウザが
保存を許す場合にlocalStorageでリロード後も復元します。

記録は記事と同じディレクトリの、Gitで追跡しない `.reviews/<slug>/` に置きます。

```text
owner.json                      # notifierの宛先（本体は解釈しない）とat
rounds/<n>/submission.json       # verdict, revertTo?, comment_ids, base_sha, at, owner_at
rounds/<n>/base.md               # 送信時の記事
rounds/<n>/delivery.json         # status, title?, at
rounds/<n>/reply.json            # round, summary?, general?, orders
```

ストアのGit設定で `.reviews/` をignoreしてください。ツリー・検索index・publication・
federation読み取りには出ません。Stockで記事を移すときは `.reviews/<slug>/` も移します。
コメントと同じディレクトリロックとatomic replacementで書きます。外部エディタとは
別途調整が必要です。配送はプロセス内のバックグラウンド処理のため、サーバ停止で
待機状態が残る場合があります。手動で通知を転送する前に記録を確認してください。

CLIは選択済みCatalogを明示した環境で、`uv run --project /path/to/knowledge-ui python
/path/to/knowledge-ui/review.py <command>` として実行します。

- `claim <path>`: 呼び出し元の会話を記録。特定不能ならstderrに通知し、終了コード0。
- `show <path> [--round n]`: 判定・コメント・前ラウンドの問いと答えを読む。
- `skeleton <path> <n>`: 送信コメントと1対1の返事JSONを出す。
- `reply <path> <n> <reply.json>`: IDの過不足と1行のreadingを検査し、保存して持ち主を
  更新。doneはコメントをresolvedにする。partial/skippedはreason、needsConfirmがtrue
  ならquestionが必須。任意のgeneralはdone/partial/skipped/noted、summaryは人への1文。
- `revert <path> <n>`: revertToのbaseを機械的に写し、全件skipped・理由revertで返事。
  承認ラウンドへのreplyは拒否する。

APIは `GET /api/reviews?file=`、`{file, verdict, revertTo?}` を受ける
`POST /api/reviews/submit`、`GET /api/reviews/diff?file=&round=` です。
送信のHTTP応答後に通知をバックグラウンド実行します。MCPは読み取り専用のままです。

## notifierと認証

`KNOWLEDGE_REVIEW_NOTIFIER` に実行ファイルの絶対パスを指定します。

- `whoami`: stdoutに宛先JSON。特定不能は終了コード2。
- `status <owner.json>`: `{status, title?}`。状態はidle/working/blocked/done/absent。
- `deliver <owner.json> <message>`: `{status, title?}`。終了コードは0=配送、2=不在、
  3=blocked、4=時間切れ、1=その他。workingはidleまで待つ。

本体は宛先のフィールドを解釈しません。任意の
[Herdr adapter](../../integrations/herdr-review-notify/README.ja.md) を同梱しています。
連携なしでも会話への手動転送ができます。

loopbackは認証なしで使えます。それ以外の `UI_HOST` は有効な
`KNOWLEDGE_UI_BASIC_AUTH_FILE` がないと起動に失敗します。
`uv run --project /path/to/knowledge-ui python /path/to/knowledge-ui/auth.py
/path/to/credentials reviewer` でパスワードを2回入力すると、mode 0600の新規ファイルに
`user:pbkdf2-sha256$iterations$saltHex$digestHex` を保存します。Gitには入れません。
ページ・静的ファイル・APIすべてにBasic認証をかけ、定数時間比較で検証します。
loopback外では信頼できる暗号化通信を使ってください。
