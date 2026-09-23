# Worklogの保存と再開

Worklogは、再開や過去のやり取り・作業経緯を探すために保持するrunごとの作業記録。本文形式と更新方法は実行側のハーネスが定める。採用した結論は知識Packageへ著述し、[知識更新規則](knowledge-changes.md)に従う。

管理対象session、保管庫のroot、個人/組織の振分けは呼出側の配備設定・instructionsで明示する。rootをrepo名や隣接directoryから推測せず、管理対象外のsessionには作成を要求しない。

## 配置と識別子

Worklogは、選択済みのWorklog Storeへ次の配置で保持する。

```text
<worklog-store>/<repository-or-_unscoped>/<task-id>/<run-id>.md
```

- **Task ID**: 一つのtaskを識別し、安全な単一path要素にする。既存のissue、bead、order IDなどをそのまま使えるときは優先し、無ければtask UUIDを発行する。
- **Run ID**: 連続した一つのagent実行ごとに発行する別UUID。runの開始時点から`<run-id>.md`へ記録する。
- **owner不明**: taskのrepository ownerを特定できないときは、選択済みのstoreの`_unscoped`を使う。推測で別の配置へ自動移動しない。
- **再開**: 同じtaskを再開するときはTask IDと過去のrunを引き継ぎ、新しいRun IDを発行して既存runを読む。無関係なtaskへ切り替えたときは、新しいTask IDとRun IDを発行する。

選択済みのtrust boundary内に保存し、推測で境界を越えて移動しない。管理対象sessionの保存先が利用不能・書込不能なら停止し、別storeへfallbackしない。

## 委任時の引き渡し

起動側は正確な絶対task directoryを`AGENT_WORKLOG_DIR`として渡し、worklog用に追加する書込権限をそのdirectoryだけに限定する。具体的な引数は起動CLIに従う。

- 起動側は、そのdirectoryが既存かつ書込可能であることを検証し、不備があれば委任先を起動しない。
- 受領側も、指定された`AGENT_WORKLOG_DIR`が既存で書込可能であることを実装前に検証する。不備があれば開始せずblockerとして返す。
- `AGENT_WORKLOG_DIR`の未設定が「管理対象外のsession」を意味するのか「委任の不備」を意味するのかは、起動側が判定する。managedでないsessionへ暗黙のrootやfallbackを生成しない。

## 保持

- 成功・失敗・blocked・中断を問わず、Worklogを自動削除しない。生成失敗で空のまま残ったファイルだけを除去できる。
- WorklogはGit管理外とする。Catalog、連合検索、FTSの対象にしない。必要なときは、明示的な`rg`などで探す。
- 会話transcriptやAgent Exchangeの交換ファイルをWorklog Storeへ集約しない。Worklog自身は会話全文や正本・監査証跡を保証しない。
