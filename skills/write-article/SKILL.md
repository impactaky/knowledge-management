---
name: write-article
description: 整理済みの理解と出自から、自己完結で再読できる記事をrepo直下にドラフトし、対話レビュー後に記事層へストックまたは却下する。情報理解ワークフローの共通終端として、またはユーザが「記事にして」「記事化しよう」「この記事を改稿して」「ストックして」と依頼したとき使う。未整理の理解や未調査の問いには使わない。
---

# Write article

実行前に knowledge-management の `docs/workflow.md#skill-setup` を読み、利用環境に合わせて実行手段と依存機能を確認する。

十分な理解を、読み直して理解を復元できる記事へ変換し、明示判定後にストックする。規約の正本は shared-rules の `modules/article-curation/CONTEXT.md` にある記事・記事境界・ドラフト・ストック・差し替え・改稿・実行可能記事。

開始前に [references/writing.md](references/writing.md) と [references/stocking.md](references/stocking.md) を読む。実行可能記事を選ぶ場合だけ [references/executable.md](references/executable.md) も読む。

## 1. 入力を検査する

記事化できるところまで整理された理解と出自があるか確認する。不足している場合は推測で埋めず、適切な前段へ戻す。

- 指定ソースを理解する → `summarize-source`
- 外部根拠を集める → `survey`
- ユーザへ聞き取る → `teach`
- 書籍を学ぶ → `study-book`
- 用語・設計判断を詰める → `grill-federated`

## 2. 既存記事を検索する

記事層を横断検索し、同じ理解がすでにないか確認する。既存記事で十分ならドラフトを作らず、その記事を提示して終了する。誤りの訂正や理解の更新が必要なら同一slugの差し替え、表現だけを直すなら改稿、それ以外は新規としてドラフトする。

## 3. 記事境界とストアを決める

- 1記事を「一つのまとまった理解を復元できる単位」にする
- 分離可能な内容はまず `##` 節にし、独立した更新・参照単位になる場合だけ分ける
- 信頼境界から記事ストアを決める
- テーマは候補だけ示し、ストック時に確定する
- 概念寄りの安定したslugを提案する

## 4. ドラフトしてレビューする

信頼境界に対応するknowledge repo直下の `./drafts/` にドラフトを作る。`articles/drafts/` は使わない。開始時に空ファイルを作らず、理解がまとまってから書く。同じ理解・slugの未判定ドラフトがあれば新規作成せず、そのdraftを改稿して再提示する。エッセンスを満たす最小構成から始め、ユーザの質問で不足が見えた箇所だけ厚くする。質問で深まった理解は必ず本文へ戻し、不要な節を削る。

通常はmarkdownを使う。静的な文章・表・図より、計算の再実行や意味のある条件変更の方が理解しやすい場合だけ実行可能記事を選ぶ。HTMLを第三の正本形式にはしない。

ドラフトが一通り書けたら、ユーザへ提示する前に、別agentを利用できる場合は通し読みを依頼する。利用できない場合は独立した読み直しを行い、その制約をレビュー時に明示する。渡すのは記事だけにして書いた経緯は渡さず、初読の読者として「何について、どの疑問を説明しているか」を要約させる。節の並びと位置づけ、飛躍や重複、理解が増えない段落、自然な日本語としてつまずく箇所を報告させる。構成が整っているだけで良い記事と判定せず、指摘を本文へ戻してから提示する。書き手は自分の意図で行間を補って読むので、通しの読みにくさは自分では検出できない。

レビュー用ポインタは利用環境で閲覧できる手段を使う。knowledge-uiを利用する場合は、選択済みCatalogの shared-rules の `tools/knowledge-ui/` を解決し、その絶対pathを下の `/path/to/knowledge-ui` に指定する。

```bash
uv run --project /path/to/knowledge-ui python /path/to/knowledge-ui/article_url.py /absolute/path/to/article.md
```

`article_url.py` は `/?file=` のコメント欄付きURLを出す。設定済みnotifierが呼び出し元を特定できる場合、その会話をレビューの持ち主として記録する。notifierが未設定・特定不能でもURLは利用できる。

tooling checkoutと知識ストアは別の場所でよい。`KNOWLEDGE_UI_BASE_URL`で閲覧UIのURLを指定する。

knowledge-uiを利用できる場合は、出力されたURLをMarkdownリンクとして提示する。利用できない場合は、ユーザが開けるファイルリンク・添付・会話内の全文提示を使う。これは新規draft、既存記事、ストック後の記事のすべてに適用する。ユーザが読めないパスだけを渡してレビュー済みにはしない。

### レビューを受け取る

`[knowledge-review]` の知らせを受けたら、選択済みCatalogの shared-rules から解決した `tools/knowledge-ui/review.py` を使う。知らせにある記事の絶対pathとラウンド番号で読み、判定に従う。MCPは読み取り専用のまま使う。

```bash
uv run --project /path/to/knowledge-ui python /path/to/knowledge-ui/review.py show /absolute/path/to/article.md --round 1
uv run --project /path/to/knowledge-ui python /path/to/knowledge-ui/review.py skeleton /absolute/path/to/article.md 1 > reply.json
# 本文を直し、reply.json に各コメントへの返事を書く
uv run --project /path/to/knowledge-ui python /path/to/knowledge-ui/review.py reply /absolute/path/to/article.md 1 reply.json
```

送ったコメントと1対1で、全件に1行の `reading`（どう読んだか）と `done|partial|skipped` を書く。partial/skipped は `reason` を必須とする。迷ったら最善の読みで直しつつ `needsConfirm: true` と `question` を残す。黙って無視せず、文のないコメントも正常な指摘として扱う。全体への返事は任意の `general`、人に見てほしい一言は `summary` に書く。done の reading は記録に残り、UIでは問い・見送った理由・summaryを確認できる。問いへの人の答えは元コメントへの返信として次のラウンドに届く。

短い答え（「それでいい」「ok」）はその案への決定として扱い、未決の印を外す。「〜ってなに」は説明を足さず本文を言い換え、同じ語を全部直す。全体への「わかりづらい」はその記事だけの造語を洗い出す。

承認の知らせでは何も直さず、「Stock の判定を会話で確かめる」。承認ラウンドには返事を書かない。新しいレビューを依頼する場合はURL helperまたは `claim <path>` で改めて持ち主を記録する。

差戻しは `review.py revert <path> <round>` を実行する。指定された `revertTo` の `base.md` を機械的に記事へ写し、全件を `skipped`、理由 `revert` で返す。独自の修正を混ぜない。

## 5. ストックまたは却下する

本文・配置先・新規/差し替え/改稿の別・主張行・必要なら凍結ビューをまとめて提示し、ユーザの明示判定を待つ。提示直後の `stock` は最終承認として再確認せず、テーマへの移動・記事内`claims`の整備・記事コミットまで行う。却下ならドラフトを削除し、保留なら残す。Stockで記事を `drafts/` から `articles/<theme>/` へ移すときは、同じslugの `.reviews/<slug>/` も移動先ディレクトリの `.reviews/<slug>/` へ移す。レビュー記録はGitで追跡しない。

提示済みドラフトが無い状態でユーザが `stock` と言った場合は、ドラフト作成依頼として扱って上記一式を提示し、改めて `stock` を待つ。未判定ドラフトが複数ある、または会話が切れて対象が曖昧な場合だけ対象を確認する。

ストック後、今後従うべき用語・判断が生まれていれば `distill` の候補として示す。記事コミットへ蒸留層の変更を混ぜない。
