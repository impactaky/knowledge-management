---
name: propose
description: 設計を一問一答せず、調べた全体案と確認項目で差分レビューし、設計とユーザの理解が収束するまで周回する。明示的な実装依頼があったときだけ同じ設計文をorderにして、Herdrの隔離worktreeでfreshな実装Agentへ渡し、検収する。設計・計画を少ないユーザターンで詰めたいとき使う。ユーザ自身だけが情報源の聞き取り、暗黙の着手、repoなしの作業には使わない。実装にはHerdr名前付きdaemonへの接続が必要。
---

# Propose

質問ではなく仮説で進める。調べれば分かることは調べ、設計全体を先に置き、置いたものをユーザに否定・訂正してもらう。ツールで取れる事実をユーザへ質問しない。連合カタログの指定file/directory入口と必読条件に従い、関連パッケージの正本、対象repoのCONTEXT・ADR・コード・test、既存記事を読む。directoryにINDEXがあっても自動的に必読にはしない。`CONTEXT.md` 冒頭のアンカー(「〜を前提とする」参照)で辿ったパッケージの語彙は、repo glossaryと同格にセッションを拘束する。

設計文は一つで、設計フェーズで書き、発注時に実装の節が生える。書き方は [design document format](references/design-doc.md) に従う。設計フェーズの標準出力は次の3節だけとする。

```markdown
# <名前>

## 実装案
## 仮定したこと
## 確認項目
```

`実装案`には、解く問題、実現する状態、構造や処理の流れのうち設計判断に必要なものを書く。`仮定したこと`には、調査しても確定できず、設計を成立させるために置いた仮定を書く。`確認項目`には、回答によって設計が変わる点だけを置き、推奨する回答と理由を短く添える。ほかの選択肢やその影響は並べない。詳しい規律は [design document format](references/design-doc.md) を正本とする。

設計フェーズに文字数、項目数、画面数の固定上限は設けない。今回の理解や判断に影響しない説明を載せないことで短くする。対象外、懸念点、未決定事項は必須の独立見出しにせず、今回の理解や判断に関係するときだけ3節の自然な位置へ含める。埋まらなかった不確実性は`仮定したこと`か`確認項目`に見える形で置き、隠して収束したことにしない。不可逆・高コスト・危険なものは、回答を得るまで実行へ進めない。

設計全文のHerdr side-pane表示はoptional capabilityである。Herdrを利用でき、再利用可能な横paneがあるときだけ、設計全文を `/tmp` のMarkdown artifactへ置き、paneを先に`clear`してから `uvx --from rich python -m rich.markdown <artifact>` で表示する。更新ごとに同じartifactを更新し、同じpaneをclearしてから再表示する。会話には変更点、確認項目、pane ID、artifact pathだけを短く返す。artifactは設計の正本にしない。Herdrまたは該当capabilityを利用できないときは、設計全文を会話へ直接出して同じレビューを継続する。表示できないことを理由に設計を止めない。

確認項目は番号と差分だけで答えられる形にし、独立した問いを一問ずつ聞かない。回答のたびに設計全体を改訂し、変更点を先に短く示す。同じことを二度聞かない。

合意の対象は設計文ではなく、設計とお互いの理解である。用語が動かなくなり、前提が訂正されなくなり、新しい背景情報が出てこなくなるまで周回する。用語の揺れは理解のズレが目に見える形になったものであり、`grill-federated` の語彙・境界規則をそのまま使ってこれを測る。ただし指摘は問いにせず設計文へ断定として書き、争点のある語だけを扱い、解決した語はその場で該当`CONTEXT.md`へ書く。収束したら、何が動かなくなったかを短く示して共通理解を確認する。確認まで設計確定としない。ユーザが確認を待たずに切り上げた場合は、未収束の項目を明記して止める。Proposal、Design Confirmation、Implementation Authorizationの区別は [delivery-coordination](../../foundation/modules/delivery-coordination/CONTEXT.md) を正本とする。

設計への合意だけでは実装しない。`Design Confirmation` は設計理解への同意であり、`Implementation Authorization` を含まない。実装・発注の依頼と、その実行計画の承認が別に揃い、未解決の分岐がなく、対象がGit repositoryでHerdrに適するときだけ進む。「OK」等は承認対象で判断する。判断や規則の恒久化は `distill`、ユーザ自身だけが情報源で仮説先行が内容を歪める聞き取りは `teach` へ分ける。

## 実装

コードから取れる最新事実だけを再確認し、確定した設計判断をやり直さない。`order`に従って作業ログtask directoryを`worklog_dir`として確定・作成・書込確認し、確定した設計文を`order_path="$worklog_dir/order.md"`へ展開する。`tasks/<slug>.md`へは書かない。`確認項目`を落とし、設計フェーズの記述を前提に省略せず、次の4節を持つ自己完結したorder文書にする。

```markdown
## 実装対象              ← orderのContext相当。repositoryの絶対path、対象、参照、実装者の選択元（明示指定 / config）と解決した`kind`と順序を保ったnative引数（選択元がconfigのときだけ解決したconfigの絶対path）、worklog directory（`worklog_dir`）とorder文書（`order_path`）の絶対path
## Work                  ← 必要な実装・test・docs
## Acceptance criteria    ← 成果物に対してtrue/falseで判定できる条件
## Verification           ← 実行するtest・checkと確認方法
```

`実装案`と`仮定したこと`から実装と完了判定に必要な背景、制約、確定した判断をorder文書へ含める。会話履歴のない実装Agentがこの一文書だけで実装と完了判定をできる状態にする。実装者の明示指定、order skill所有のconfig（対象実装repositoryには依存しない）の優先順位とsnapshot記録は`order`に従い、利用者がmodelやCLIを名前で示した場合は`resolve-config.py --list`の設定済み候補を一意に照合して`--implementation`で選択する。`実装対象`へは選択元、configから選択した候補名と`label`（inline既定では`null`）、`kind`、順序を保った引数、configから解決した場合はconfigの絶対pathを記録する。

`order` skillの「Herdr sessionでfreshな実装Agentを起動する」以降をそのまま実行し、手順を複製しない。接続条件は依頼元がHerdr pane内かどうかではなく、名前付きdaemonへの到達性で判断する。確定した設計文を`order_path`の同じ文書として唯一の実装仕様にし、Herdrへはその本文を埋め込んで渡す。Background terminalでの待機・回収、未検収orderの扱い、同じAgentへの差し戻し、合格後も`order.md`を残す扱いも`order`に従う。検収は応答Agentの報告だけで合格にせず、開始commitからの全commit、完全なtask diff、test結果、未commit変更を検査する。
