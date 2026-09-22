---
name: agent-exchange
description: 共有ディレクトリ上の一つのRequestと一つのResponseで長時間の作業を別Agentへ委任する。通常Requestをfreshに隔離するとき、明示Threadで同じAgentへContinuation Requestを順次渡すとき、Responseを書く・待つとき、またはHerdr Launcherを運用するとき使う。progress配送、queue、一般chatには使わない。
---

# Agent Exchange

Agent Exchangeは同一host・同一OS user・同一filesystem上の一回限りのRequest/Response規約である。通常Requestは毎回freshな応答AgentとRequest固有worktreeを使う。明示的に作ったThreadだけは、初回RequestのHerdr workspace、Git worktree、応答Agentの会話を保持し、複数のContinuation Requestを一つずつ同じAgentへ渡す。どちらも一つのRequestに最大一つの最終Responseだけを対応させる。

既定のExchange Directoryは `${AGENT_EXCHANGE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-exchange}`。参加するcontainerにはrepositoryとExchange Directoryをhostと同じ絶対pathでbind mountする。以下の`scripts/...`はこのskill directoryを基準とする。

このskillは `bash`、`git`、`jq`、`inotifywait`、`flock`、`python3`（標準ライブラリ `tomllib`）、Herdr executable を使う。導入とsystemd adapterは [deployment reference](references/deployment.md) に従う。実行時に不足するcommandがあれば、どのcommandがどの操作に必要かを具体的に報告し、代替実装を推測しない。

## Requestを送る

### 通常Request

会話履歴なしで完遂できる自己完結した依頼をstdinからpublishする。stdoutは後方互換のRequest UUID v4一行である。

```bash
request_id="$(scripts/request.sh /absolute/path/to/repository < request.md)"
```

### 新しいThread

初回は自己完結した依頼とrepositoryを渡す。stdoutは互いに異なるUUID v4を持つJSONである。

```bash
result="$(scripts/request.sh --new-thread /absolute/path/to/repository < request.md)"
request_id="$(jq -r .request_id <<<"$result")"
thread_id="$(jq -r .thread_id <<<"$result")"
```

初回Requestは通常Requestと同じくfresh worktree workspaceとfreshな応答Agentを作る。Launcherがpromptを受理した後、Threadはそのworktree、workspace、agentへdurableに対応づく。

### Threadを継続する

前RequestのResponseを取得して`cleanup.sh`を完了してから、Thread IDだけを指定する。repositoryはThread metadataを正とするため引数に取らない。Continuation Requestは同じ会話文脈を前提とした差分依頼でよく、元order全文の再掲を必須としない。

```bash
result="$(scripts/request.sh --continue "$thread_id" < continuation.md)"
request_id="$(jq -r .request_id <<<"$result")"
```

一つのThreadで同時にactiveにできるRequestは一つだけである。前Requestが未cleanupなら新Requestはqueueされず、`thread-busy`の最終failure Responseを受ける。未知または壊れたThread IDもRequestとしてpublishされ、Launcherが理由別codeを含むfailure Responseを返す。path traversalになるIDはCLIで拒否する。

Request bundleは`staging/<request-id>/`から`requests/<request-id>/`へatomic renameする。Thread metadataは`threads/<thread-id>/`、排他用fileは`thread-locks/`に置き、directoryを0700、metadataを0600にする。通常bundleは従来の`request.md`と`repository`だけである。Thread bundleは追加の`mode`、`thread`、`reservation`を持つ。

`requests/`はpending、`running/`はLauncherがclaim済み、`responses/<request-id>.md`はそのRequestの完了を表す。`threads/`はcleanup後も残る継続関係の正本であり、`active`の不在がidleを表す。inotifyは通知にだけ使い、fileの存在とmetadataを状態の正本にする。

## Responseを待つ

依頼元sessionでは次をbackground processとして起動し、完了通知を同じsessionへ戻す。

```bash
scripts/wait.sh "$request_id"
```

`wait.sh`はResponseの存在確認と60秒timeout付き`inotifywait`を交互に行う。通知を受けたら`responses/<request-id>.md`を改めて読み、内容を取得してから次を実行する。

```bash
scripts/cleanup.sh "$request_id"
```

cleanupはResponseがpublish済みの場合だけ交換fileを消す。Thread Requestではactive Requestがcleanup対象と一致するときだけThreadをidleへ戻す。busyになった古いRequestのcleanupは別のactive Requestを解除しない。Thread metadata、workspace、worktree、agentは保持する。

Gatewayやmachineの再起動後は、進行中Request IDについてResponseを先に確認し、なければ`wait.sh`を再armする。Response処理前にcleanupしない。

## Responseを書く

応答Agentは各Requestの最終結果または続行不能の理由をstdinから一度だけpublishする。

```bash
scripts/respond.sh <request-id> < response.md
```

Responseにはworktree絶対path、変更概要、commit、検証結果、逸脱またはblockerを含める。途中経過や質問はpublishしない。追加判断が必要ならそれを一つの最終Responseとし、依頼元がcleanup後にContinuation Requestを作る。

## Launcherを運用する

`scripts/launcher.sh`は参照Launcherであり、交換規約そのものには含まれない。

Launcherは起動時に一度だけconfigを解決し、応答Agentの`kind`とnative引数を決める。configは `python3` の標準ライブラリ `tomllib` で読むreal TOMLであり、非対話の応答Agentをどの実装で起動するかを決める。Exchange Directoryなどの配備値を持つdeployment envとは用途が異なる別fileである。解決順は明示的な `AGENT_EXCHANGE_CONFIG`、次に `${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/agent-exchange.toml`。相対的な `XDG_CONFIG_HOME` や `HOME` fallback はcwd基準で解決せずエラーにする。

```toml
[implementation]
kind = "<herdr kind>"
args = ["<native arg>", "..."]
```

config欠落、不正TOML、`[implementation]`欠落、空の`kind`、文字列配列でない`args`は、いずれもAgent起動前に明示エラーになる。`kind`候補の固定リストは持たず、未知のkindもnative引数の順序をそのまま保ってHerdrへ渡す。既知kindでは応答AgentがExchange Directoryへ到達できるよう、利用者引数の順序を保ったまま次を補う。

- `codex` / `claude`（Claude Code）: `--add-dir <Exchange Directory>`。Claude Codeのpermission modeとsandboxは利用者の設定を引き継ぐ。
- `cursor`（Cursor Agent）: `--add-dir <Exchange Directory> --sandbox enabled`。両optionに対応するCLIを使い、native引数に`agent`を重ねない。
- `agy`: `--add-dir <Exchange Directory> --mode accept-edits --sandbox`。
- `opencode`: 起動paneへsession-onlyの`OPENCODE_CONFIG_CONTENT`としてExchange Directoryとその配下の`external_directory`許可をmergeしてexportする。

`--dangerously-skip-permissions`（全kind）、`claude`の`--allow-dangerously-skip-permissions`と`--permission-mode bypassPermissions`、`cursor`の`--force` / `-f` / `--yolo`と`enabled`以外の`--sandbox`、`agy`の`--mode`、`opencode`の`--auto`は起動前に拒否する。Claude CodeとCursorでは`=`形式の指定も検証し、Cursorの既存の`--sandbox enabled`は受理する。kind別の根拠と必要CLIは[deployment reference](references/deployment.md#known-kind-preparation)を参照する。未知kindでは専用準備を推測しない。必須optionをCLIが受理しない場合も起動失敗として扱い、in-repoの既定modelや別kindへ自動fallbackしない。実利用configはrepositoryへcommitせず、[config.example.toml](config.example.toml)を雛形にする。

新規Requestでは次の順で進む。

1. startup時と通知後・60秒timeout後に`requests/`をscanする。
2. 通常RequestとThread初回Requestではrepository親workspaceを作成または再利用し、Request IDのbranch、linked worktree workspace、freshな応答Agentを起動する。
3. Thread初回promptの受理後、worktree、workspace、agent、共通repositoryのbindingをThreadへatomicに記録する。
4. ContinuationではThreadのactive所有権、Git worktree、Herdr workspace、interactive agentの相互対応を検証し、同じagentへ新しいrequest pathと`respond.sh <new-request-id>`をpromptする。worktree作成やagent起動は行わない。
5. Threadの不在、不整合、busy、resource消失、prompt失敗は`thread-*` code、Thread ID、可能ならworktreeとlast commit、明示的fresh recoveryの指示を持つfailure Responseにする。自動fallbackやThread修復は行わない。

LauncherはHerdr停止中のRequestをclaimしない。claim後の同期失敗だけfailure Responseにし、prompt後のcrashやblocked状態は自動再配送しない。`running/<id>/`の存在により再scanで同じRequestを重複promptしない。

systemd user unitのtemplateは`assets/systemd/`にあり、`scripts/install-systemd.sh`がdeployment envを読んで生成する。Herdr sessionは依頼元の`default`と応答側の`agent-exchange`に分ける。

`herdr-server.sh`は対象sessionのAPIが応答すれば既存serverを利用し、5秒間隔で確認する。API確認は5秒でtimeoutし、終了しなければさらに1秒後に停止する。serverが利用できなければ起動し、起動競合時はAPIを再確認する。既存serverを監視しているサービスを停止しても、そのserverへ停止命令は送らない。サービス自身が起動したserverは子processとして停止対象になる。server終了後にAPIが利用できなければ、正常終了だった場合もサービスのrestart対象にする。

LauncherはHerdr起動サービスを`Wants=`で起動依頼する。Herdrサービスの停止・再起動をLauncherへ伝播させず、配送可否はclaim前のAPI確認で判断する。Herdr本体が停止した場合の進行中Requestの自動再配送は行わない。

Herdr sessionは用途で分ける。

- `default`: 依頼元
- `agent-exchange`: repository親workspaceと通常/Thread初回のworktree workspace、保持するThreadの応答Agent

## 検収と終了

Threadを閉じるscript、自動close、branch削除はない。最終Responseを検収し、commit、diff、未commit変更を確認した上位workflowだけが、記録したworkspace IDに対して次を明示的に実行する。

```bash
herdr worktree remove --workspace <workspace-id>
```

workspace消失後のContinuationはfail-closedになる。復旧する場合は最後にreviewできたworktree/commitをrepositoryとし、元order、前Response、追加指示を含む自己完結した通常Requestを明示的にpublishする。legacy workspaceをThreadへ移行しない。

## 境界

- 対象は同一host・同一OS user・local filesystemだけ。認証、暗号化、network filesystemは扱わない。
- Threadは複数のRequest/Responseを順序づけるだけで、progress、stream、一般chat、Agent identity管理にはしない。
- queue、claim/lease/retry、複数Launcher、自動fresh fallback、自動closeは設けない。
- 通常Requestは常にfreshで隔離し、repositoryから継続先を推測しない。
- 同じ応答Agentを長く使うことで増えるcontextはそのAgent自身のcompactionへ委ねる。
