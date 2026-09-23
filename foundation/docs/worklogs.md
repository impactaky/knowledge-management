# Worklogの配置・識別・引き渡し・保持

Worklogは、一つのagent実行（run）の作業記録であり、再開のためだけでなく、過去のやり取りや作業経緯を後から探す手掛かりとして保持する。この文書は、Worklogの配置・識別・引き渡し・保持の共通契約を定める正本である。

Worklogの本文形式と更新方法はMacro Harnessが所有する。管理対象sessionの選定、Worklog Storeのroot、個人/組織の振分けは、呼出側が明示した配備設定とinstructionsが所有する。知識として採用した結論はWorklogではなく知識Packageへ著述し、その更新手順は[知識の更新と適用条件](knowledge-changes.md)を正本とする。

この契約が存在することは、すべてのsessionへWorklogを強制しない。管理対象外のsessionはWorklogを持たないままでよい。

## 配置と識別子

Worklogは、選択済みのWorklog Storeへ次の配置で保持する。

```text
<worklog-store>/<repository-or-_unscoped>/<task-id>/<run-id>.md
```

- **Task ID**: 一つのtaskを識別し、安全な単一path要素にする。既存のissue、bead、order IDなどをそのまま使えるときは優先し、無ければtask UUIDを発行する。
- **Run ID**: 連続した一つのagent実行ごとに発行する別UUID。runの開始時点から`<run-id>.md`へ記録する。
- **owner不明**: taskのrepository ownerを特定できないときは、選択済みのstoreの`_unscoped`を使う。推測で別の配置へ自動移動しない。
- **再開**: 同じtaskを再開するときはTask IDと過去のrunを引き継ぎ、新しいRun IDを発行して既存runを読む。無関係なtaskへ切り替えたときは、新しいTask IDとRun IDを発行する。

保存先のrootはWorklog Storeとして配備側が明示する。rootをrepository名や隣接directoryの名前から推測しない。

## 機密境界

- Worklog Storeは、呼出側が選択したtrust boundaryの中だけに置く。選択した保存先が利用不能または書込不能でも、別のtrust boundary（個人と組織、公開と非公開など）へはfallbackしない。
- 個人と組織のWorklog Storeを分ける場合、どのtaskをどちらへ置くかは配備側のinstructionsが所有する。
- 境界を越える移動を推測で行わない。

## delegateへの引き渡し

起動側は、delegateへ既存かつ書込可能な正確な絶対task directoryを`AGENT_WORKLOG_DIR`として渡し、そのdirectoryへ必要な書込権限を与える。

- 起動側は、`AGENT_WORKLOG_DIR`が既存のdirectoryであり、起動前に書込可能であることを検証する。検証できない場合はdelegateを起動しない。
- 受領側も、指定された`AGENT_WORKLOG_DIR`が既存で書込可能であることを実装前に検証する。不備があれば開始せずblockerとして返す。
- `AGENT_WORKLOG_DIR`の未設定が「管理対象外のsession」を意味するのか「委任の不備」を意味するのかは、起動側が判定する。managedでないsessionへ暗黙のrootやfallbackを生成しない。

具体的な引数や環境の渡し方は各harnessと起動CLIに従う。Worklog自身は会話全文や正本・監査証跡を保証しない。

## 保持

- 成功・失敗・blocked・中断を問わず、Worklogを自動削除しない。生成失敗で空のまま残ったファイルだけを除去できる。
- WorklogはGit管理外とする。Catalog、連合検索、FTSの対象にしない。必要なときは、明示的な`rg`などで探す。
- 会話transcriptやAgent Exchangeの交換ファイルをWorklog Storeへ集約しない。Activity Streamを参照可能に保つ扱いは[Activity Retention](../CONTEXT.md#external-contracts)の外部contractによる。
