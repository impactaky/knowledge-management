# 任意のHerdrレビューnotifier

実行可能な `notifier.py` の絶対パスを `KNOWLEDGE_REVIEW_NOTIFIER` に指定します。
Python標準ライブラリとインストール済みHerdr CLIだけが必要です。
`HERDR_BIN_PATH` でCLIを選び、省略時はPATHの `herdr` を使います。

依頼・返事をするagentは `HERDR_ENV=1`、`HERDR_PANE_ID`、絶対パスの
`HERDR_SOCKET_PATH` を継承します。`whoami` は `agent get` のpane一致と、
`agent_session.kind` が `id` であることを検証し、特定不能なら2で終了します。
agentの種類は制限しません。宛先にはkind、agent、session_id、pane_hint、socket、
titleを記録します。ownerファイルは信頼できるローカル実行時データとして扱い、公開しません。

`status <owner.json>` と `deliver <owner.json> <message>` は保存されたsocketを使い、
`agent list` から会話IDで探します。pane再利用だけでは宛先になりません。
workingは `agent wait <pane> --until idle --timeout 1800000` で待ち、
`agent prompt` の直前に再び会話IDを検証します。blockedには送らず、自動resumeも
行いません。UIとHerdrは同じマシンで動かす前提で、別マシンへの配送は対象外です。

stdoutはJSONです。配送の終了コードは0=配送、2=不在、3=blocked、4=時間切れ、
1=その他。状態はidle/working/blocked/done/absentと任意のtitleを返します。
blocked・不在・失敗時はUIに会話へ貼る1行が残ります。checkoutのルートで実行します。

```bash
uv run --locked --project foundation/tools/knowledge-ui python -m pytest foundation/tests/test_herdr_review_notify.py
```

テストはstub実行ファイルを使い、実際のHerdr sessionには接続しません。
