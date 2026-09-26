# Storeのagent設定

- Session開始時に選択済みCatalogを読み、その読み取り規則に従う。Federation MCP接続時は `get_catalog` を使い、選択済みstoreと一致することを確認する。Package参照はCatalogのentryから解決し、file入口を読み、directory入口では必要な原文を選ぶ。INDEXは任意の案内で、自動的に必読にはしない。
- 作業中repositoryの `CONTEXT.md` 冒頭にanchor（「〜を前提とする」参照）があれば、その参照先とCatalogが指定する必読文書を読む。規約やADRは検索対象とは限らないため、anchorと必読指定は明示参照で読む。
- 技術調査や記事再利用の前に `federation_search(query, deep)` で既存知識を探し、採用する候補の原文を読む。語句の所在が要るときだけ `federation_grep` を明示的に選び、検索後や検索停止時に自動併用しない。毎ターンの自動想起はしない。
- Sessionで得た恒久的な用語・判断・規則は、shared-rules の `docs/knowledge-changes.md` に従い、適用範囲が最も狭い適切なPackageへ書く。
- Order implementation workerはorder、対象repositoryのinstructions、orderが明示参照する文書だけを使う。Orderが要求しない限り `get_catalog` と `federation_search` を呼ばず、外部Packageが不足したら親へ報告する。
- CatalogやPackageの内容をinstruction fileへ複製せず、参照で保持する。
