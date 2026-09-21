---
name: order
description: 着手を決めたGit実装を元セッションで整理して1つのorder Markdownにまとめ、Herdrの隔離worktreeでfreshな実装Agentへ直接promptし、同じAgentへ差し戻しながら検収まで回す。ユーザが「orderして」「仕様書に切って」「発注して実装まで回して」と依頼したとき使う。Herdr名前付きdaemonに接続できない環境、repoなし、未分類の思いつき、調査には使わない。
---

# /order — 設計とreviewからHerdr実装まで回す

一つの自己完結したorderをHerdr名前付きsession上のfreshな実装Agentへ直接渡し、元セッションが成果物を検収する。実装者とnative引数は、orderごとの明示指定を最優先し、指定がなければorder skill所有のconfig（このSKILL.mdと同じdirectoryにある`scripts/resolve-config.py`が解決する`order.toml`）のHerdr `kind`と引数配列から解決する。orderはHerdr対応kindの固定リストやmodel・effortの意味、利用可能model一覧を所有せず、解決した値を`herdr agent start`へ渡す。file-based Agent Exchange、queue、scheduler、progress channel、独自のdurable tracker、新規常駐サービスは使わない。

Delivery Coordinationの語彙（Proposal、Design Confirmation、Implementation Authorization、Order、Review Boundary、Worklog Binding）は [delivery-coordination](../../foundation/modules/delivery-coordination/CONTEXT.md) を正本とする。設計への合意はImplementation Authorizationを含まず、本skillは明示的な実装依頼と実行計画の承認が揃ったときだけ起動する。

## 1. worklog_dirを確定してorderを書く

`grill-federated` skillを読み、承認済み設計ではgrillを省略する。それ以外はコードで分かる事実を調べ、未確定の設計判断を詰める。独立した質問は同じroundにまとめ、依存する質問は次のroundへ回す。共有理解の確認を承認として扱う。

親sessionがこのtaskに使っている作業ログtask directoryを、絶対pathのshell変数`worklog_dir`として先に確定する。`AGENT_WORKLOG_DIR`が設定されていればそれを使い、親が別途確定した絶対pathがあればそれを使う。どちらもなく、解決したconfigの任意の`[worklog] root`からtask directoryを一意に決められるときだけそれを使う。store名やrepository名から保存先を推測せず、暗黙のprivate pathを持たない。directoryを作成して親から書き込めることを確認するまで、order文書の作成と実装Agentの起動をしない。確定できない場合はblockerとして停止する。確定した`worklog_dir`の絶対pathをContextへ記録する。

確定したworklog directoryへ`order_path="$worklog_dir/order.md"`として、会話履歴のない実装Agentが実装と完了判定をできる自己完結した本文を書く。`tasks/`へorder Markdownを作らない。order文書はtask worklog directoryの`order.md`に一つだけ置く。一つのorderに必要な実装・test・docsを含め、自動的に複数orderへ分けない。親sessionはHerdrを起動する前に`order_path`が存在し読み書き可能であることを確認する。

```markdown
# <order名>

## Goal

なぜ行うかと、完了時に実現している状態。

## Context

- 実装repositoryの絶対path
- 実装者の明示指定（任意）。指定する場合はHerdr `kind`とnative引数配列。省略時の既定値は必ずorder skill所有のconfigの`[implementation]`から読み、明示指定も有効なconfigもなければ起動前に停止する
- 親sessionが確定した作業ログtask directory（`worklog_dir`）の絶対path（`AGENT_WORKLOG_DIR`と同値）と、order文書の絶対path（`order_path`＝`worklog_dir/order.md`）
- 現状と変更後の振る舞い
- 参照するコード・文書・用語
- 確定した判断、制約、非対象

## Work

- 必要な実装

## Acceptance criteria

- [ ] 成果物に対してtrue/falseで判定できる条件

## Verification

- 実行するtest・checkと確認方法
```

## 2. Herdr sessionでfreshな実装Agentを起動する

依頼元がHerdr pane内かどうかを`HERDR_ENV`だけで判定しない。Herdr CLIはpane外からも名前付きdaemonへ接続できる。最初に解決済みsession名で`herdr --session <session> api snapshot`を実行し、CLIが存在して対象daemonへ接続できることを確認する。接続できなければ、CLI欠落・Unix socket未共有・daemon停止などの具体的なblockerを報告して停止し、file-based Agent Exchangeへfallbackしない。session名は明示指定、なければconfigの`[herdr] session`、どちらもなければ既定の`agent-exchange`を使う。

依頼元の実行場所は問わず、実装側をその名前付きsessionへ作る。以後、実装側を操作するすべてのHerdr commandに`--session <session>`を付ける。

実装Agentを起動する前に、`order_path`のorder文書が存在し親sessionから読み書き可能であること、`worklog_dir`が作成済みで書き込めることを再確認する。欠ける場合は起動せずblockerとして停止する。

### 実装者をconfigから解決する

order開始時に実装者とnative引数を一度だけ解決し、結果をContextへsnapshotとして記録する。以後は記録済みの`kind`と引数を使い、進行中のconfig変更を反映しない。選択の優先順位は次のとおり。

1. 当該orderで利用者が明示した`kind`とnative引数の組。明示指定は組全体を一回限りで置き換え、`kind`だけを指定した場合は引数を空とし、別kindのconfig引数を持ち越さない。明示指定はdefaultではなく一回限りの上書きであるため、configがなくても明示指定した値を使える。
2. 明示指定がない場合の既定値は、必ずorder skill所有のconfigから読む。configの所在は明示的な`ORDER_CONFIG`、次に`${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/order.toml`の順で解決し、対象実装repositoryには依存しない。`kind`はHerdrへ渡すkind、`args`は順序を保った文字列配列。configが存在しない場合は起動前にblockerとして停止し、skill本文の既定値へ戻さない。雛形は同じdirectoryの`config.example.toml`に置き、実利用configはrepositoryへcommitしない。

```toml
[implementation]
kind = "<herdr kind>"
args = ["<native arg>"]
```

解決とschema検証は、このSKILL.mdと同じdirectoryの`scripts/resolve-config.py`を実行して一貫して行い、機械可読なsnapshotを得る。`kind`が空でない文字列であること、`args`が文字列配列であることを検証する。configが存在しない、または存在して不正なTOML、`[implementation]`欠落、空の`kind`、文字列配列でない`args`のいずれかである場合は、起動前にblockerとして停止する。`kind`がHerdrと対象CLIに受理されるかの判断はHerdrとCLIを正とし、orderはkind候補の固定リストを持たない。modelやreasoning effortの意味もorderは解釈せず、利用者がnative引数で指定した値だけを渡す。

Contextへは選択元（明示指定 / config）と解決した`kind`、順序を保った引数配列を記録する。configから解決した場合は解決したconfigの絶対pathも記録する。

実装対象がGit repository rootであることと、開始commitを確認する。次のcommandで隔離worktreeを作り、JSONからworkspace ID、root pane ID、worktree pathを読む。IDやpathを推測しない。

```bash
herdr --session <session> worktree create \
  --cwd <repo> \
  --branch order/<slug>-<timestamp> \
  --base <commit> \
  --label <repo>:<slug> \
  --no-focus
```

Agent名はworkspace IDをASCII lowercaseへ正規化してから`order-<lowercase-workspace-id>`のように32文字以内で一意にし、root paneでfreshな実装Agentを起動する。workspace ID自体はHerdrから得た元の値を保持し、Agent名だけを正規化する（HerdrのAgent名は小文字英数字・`-`・`_`だけを受け付ける一方、workspace IDには大文字が入り得る）。

起動前にroot paneのshellへ、shell-escapeした`worklog_dir`を`AGENT_WORKLOG_DIR`としてexportする。export投入に失敗した場合は起動しない。

```bash
printf -v export_worklog_dir 'export AGENT_WORKLOG_DIR=%q' "$worklog_dir"
herdr --session <session> pane run "$pane" "$export_worklog_dir"
```

### 起動commandとorder必須準備

起動するのは解決済みの実装者だけである。検証済みの利用者引数`args`とkind別の必須引数を合成した最終引数配列`final_args`を作り、`herdr agent start`のnative引数へそのまま渡す。shell command文字列を組み立て直さず、文字列の再評価や再分割も行わず、配列の各要素を一つの引数として渡す。

合成の前に、必須引数と同じoptionを利用者が単一値で指定して矛盾する場合、または必須の安全引数を打ち消す場合は、黙って上書きせず起動前にblockerとして停止する。

- OpenCode (`kind = "opencode"`): 必須native引数はないため、`final_args`は検証済みの利用者引数と同じ。`--add-dir`がないため、起動paneの`OPENCODE_CONFIG_CONTENT`へ`permission.external_directory`の作業ログtask directoryの絶対pathとその配下（`<worklog_dir>/**`）を`"allow"`として追加し、shell-escapeしてexportする。既存のinline設定やpermission規則がある場合は保持してmergeし、exportに失敗した場合は起動しない。これは起動sessionだけの設定とし、グローバル設定は変更しない。`--auto`は付けない。設定の扱いは[OpenCode Config](https://opencode.ai/docs/config/)、外部directoryの指定は[OpenCode Permissions](https://opencode.ai/docs/permissions/#external-directories)に従う。
- Codex (`kind = "codex"`): 検証済みの利用者引数へ必須引数`--add-dir "$worklog_dir"`を合成したものを`final_args`とする。
- agy (`kind = "agy"`): 利用者のグローバル設定がterminal sandboxとproceed-in-sandboxを有効にしていることを前提とし、異なる場合は起動前に親sessionで確認してworkerにグローバル設定を変更させない。検証済みの利用者引数へ必須引数`--add-dir "$worklog_dir" --mode accept-edits --sandbox`を合成したものを`final_args`とする。sandbox内のコマンドは実行範囲に基づいて自動許可し、sandbox外での実行は承認対象として扱う。`--dangerously-skip-permissions`は付けず、利用者引数に含まれる場合も起動前に停止する。
- その他のkind: 専用準備を推測せず、`final_args`は検証済みの利用者引数と同じ。workerは実装前に`AGENT_WORKLOG_DIR`へ書けることを確認し、書けなければblockerとして返す。

`final_args`が空のときはnative引数delimiter `--`を付けない。

```bash
# final_args が空でない場合
herdr --session <session> agent start \
  "$agent" --kind "$kind" --pane "$pane" --timeout 300000 -- "${final_args[@]}"

# final_args が空の場合
herdr --session <session> agent start \
  "$agent" --kind "$kind" --pane "$pane" --timeout 300000
```

worktree integration直後の`agent_pane_busy`だけは、同じ`agent start`を最大60秒再試行してよい。他の起動失敗では`order_path`のorder文書とworktreeを残して停止し、別のkind・model・実装者へ自動fallbackしない。

初回promptには`order_path`の`order.md`から固定済みの本文をそのまま埋め込み、全体の実装、test、diff review、review可能なcommitまで依頼する。file pathだけをworkerへ渡して読ませる方式にせず、本文をpromptへ含める。promptでは応答Agentを、連合解決と設計を完了した親sessionから自己完結したorderを受け取る **order implementation worker** と明示する。workerはorder、対象repositoryのinstructions、orderが明示的に参照する文書だけを外部contextとし、orderが明示的に要求しない限り`get_catalog`や`federation_search`を呼ばない。必要な外部Packageがorderに欠けていれば、自律的に連合を探索せずblockerとして親sessionへ返す。対象repository内のcode・test・docsを調べる通常の実装作業はこの制限に含めない。

workerはorderに明記された作業ログの絶対pathを使う。実行ツールに`AGENT_WORKLOG_DIR`が継承されない場合も同じpathを使い、必要なcommandで同変数を設定して、既存directoryへ書き込めることを実装前に確認する。orderのpath指定がない、envと矛盾する、directoryが存在しない、または書けない場合はblockerとして返す。暗黙の別directory生成やグローバル設定変更は行わない。

Agentはこのorder専用とし、元セッション以外からpromptしない。

## 3. Background terminalで待ち、同じcommandの結果を回収する

初回のpromptは次の一つのcommandで渡し、`--wait`を付けたままBackground terminalへ預ける。

```bash
herdr --session <session> agent prompt "$agent" "$prompt" --wait
```

`--wait`は既定のsettled状態である`idle`、`done`、`blocked`のいずれかまで待つ。`--until`で同じ状態を重ねない。実行ツールがsession handleを返しても、commandが実行中ならAgentの終端状態を得たことにはならない。

元セッションは`order_path`と`worklog_dir`、workspace ID、Agent名、worktree path、開始commit、実行ツールのsession handleを対応づけて保持する。Background terminalが返したsession handleを保持し、独立作業の区切りで同じprocessの出力を回収する。実行ツール固有の待機APIは環境に従う。

| 状況 | 元セッションの扱い |
| --- | --- |
| commandが実行中 | 同じhandleを保持し、依頼済みの独立作業を進める。区切りで結果を回収し、まだ実行中なら同じ扱いを続ける。 |
| 別作業がない | 同じ待機commandを長めに待つ。 |
| Background terminal非対応 | 同じ`prompt --wait`を従来どおり同期的に待つ。 |
| `idle` / `done` | 結果を回収し、次節で実出力・最終応答を確認する。 |
| `blocked` | 次節で理由を確認して解消する。合格や完了として扱わない。 |

別作業へ移る際は未検収orderがあることを明示する。実装中のworktreeを別作業で変更せず、未検収の成果に依存する作業を進めない。orderは検収完了まで未完了である。自動完了通知や最終応答後の自動再開は保証しないため、それらに依存せず通常の作業区切りで同じhandleを回収する。

短い間隔のLLM polling、working中のGit・worktree・process・pane・agent status監視、途中diffからの進捗推測は行わない。Agentがworking中に新しいpromptを送らず、待機のために同じpromptを再送しない。`agent_prompt_stalled`等のエラーでは、次節の`agent get`と`agent read`でpromptが送信済みか診断してから再開する。未送信を確認せず再送しない。

## 4. reviewして同じAgentへ差し戻す

settledが返ったら、まず`herdr --session <session> agent read "$agent" --source recent-unwrapped`で実出力・最終応答を読む。`idle`や`done`でも、承認待ちや実行未完了なら検収・差し戻しへ進まない。これはsettled後の診断であり、作業中の監視ではない。

`blocked`や承認待ちは、`herdr --session <session> agent get "$agent"`と上記`agent read`で理由を確認する。blocked中の`agent prompt`は送信前に`agent_blocked`で拒否される。コマンド承認画面なら対象内容と既存の許可範囲を確認し、CLIの入力形式に合わせて一回限りで解消する。必要なら対象Agentへ`herdr --session <session> agent send-keys "$agent" <key>...`を使う。ユーザの新しい判断が必要な場合だけ確認する。

解消後も新しいpromptを重ねず、同じAgentの完了を待つ。既存の待機commandが実行中なら同じhandleを回収し、終了済みなら`herdr --session <session> agent wait "$agent"`を前節と同じ方式で待つ。返却後は再び実出力・最終応答を確認する。

実装完了を確認してから、開始commitからの全commit・完全なtask diff・test結果・未commit変更を読み、Acceptance criteriaを検収する。応答Agentの完了報告だけで合格にしない。

問題があれば、対象箇所、期待する振る舞い、再現または検証方法だけを完了した同じAgentの同じ会話・worktreeへ`herdr --session <session> agent prompt ... --wait`で渡し、前節と同じ方式で待機・回収して再度検収する。元order全文は再掲しない。

## 5. 合格して終了する

合格しても`order_path`の`order.md`を削除せず、worklogの記録として残す。commit、branch、検証結果をユーザへ報告する。worktreeがcleanでcommitがbranchに保持されていることを確認してから、作成したworkspaceを閉じる。

```bash
herdr --session <session> worktree remove --workspace "$workspace"
```

branchは削除しない。失敗・中断・未解決blockでは`order_path`の`order.md`、workspace、worktree、branchを残し、後続turnで同じ`order_path`を再開元としてHerdrのlabelとGit branchから再開する。自動復旧metadataは作らない。

## 境界

- 元セッションはgrill、order作成、Herdr起動、完了後review、差し戻し、終了を担う。
- 応答Agentは一つのworktreeと実装者の会話で実装、test、commit、差し戻し対応を担う。
- 実行中の応答Agentを監督しない。観測はsettled後の実出力確認・検収、明示的blockや送信エラーの診断に限る。
- Herdr session名は実装側resourceの配置先であり、Agent ExchangeのRequest / Response / Thread規約を使うことを意味しない。
- file-based Agent Exchangeは耐久的なfile handoffが必要な別workflowであり、orderから呼ばない。
- 実装者のkind候補を固定せず、不明なkindでは専用準備を推測しない。
- 会社の機密の実体を個人ストアに書かない。
