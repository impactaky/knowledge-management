# Store側の変更

新しい変更を先頭に記録する。共有文書の更新はcheckoutの `git pull` で取得する。

## 2026-09-26

Catalogに `shared-rules` entryを追加し、checkoutの `foundation/INDEX.md` の絶対pathを指定する。Storeの `AGENTS.md` は、選択済みCatalogを使って shared-rules の `docs/agent-setup.md` に従うpointerだけにする。
