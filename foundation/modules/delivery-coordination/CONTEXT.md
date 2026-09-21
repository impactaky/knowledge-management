# Delivery Coordination

設計の提案から実装着手、settled後のreviewまでを調整するモジュール。[Knowledge Management](../../CONTEXT.md)の語彙の一部をここに記述し、StockとImplementation Authorization、OrderとRequestを同じモデル内の異なる概念として区別する。

Related decisions: [ADR 0001](../../docs/adr/0001-shared-delivery-and-agent-exchange.md)。

## Language

**Delivery Coordination**:
Proposal、Order、Task、Design Confirmation、Implementation Authorization、Review Boundaryを担当する、Knowledge Managementの内部モジュール。
_Avoid_: 独立したBounded Context、実行systemそのもの

**意図層 (Intent Layer)**:
着手を約束したTaskと実装のOrderからなる可変な作業層。未分類の思いつきや実行systemの進行状態を保管する層ではない。
_Avoid_: 未分類の捕捉先、未約束ideaの保管、実行systemの代替

**提案 (Proposal)**:
設計全体の仮説、重要な差分、未確定点をまとめ、Design Confirmationを得るための候補。承認前のOrderやImplementation Authorizationではない。
_Avoid_: Order、確定仕様、Implementation Authorization

**発注書 (Order)**:
着手を決めた実装をfreshな実装者へ渡し、完了条件と検証方法を自己完結して示す仕様。Agent ExchangeのRequestやArticleのDraftではない。
_Avoid_: Request、Draft、未分類idea

**タスク (Task)**:
着手を約束した作業項目。実行管理先や一つのOrderに含む作業数とは独立し、Agent Exchangeの一回のRequestを意味しない。
_Avoid_: Request、未約束idea、全作業の集約

**設計確認 (Design Confirmation)**:
Proposalが意図と制約を正しく表していると確認するgate。設計理解への同意であり、実装着手の許可を含まない。
_Avoid_: Implementation Authorization、Article Stock、黙示の着手

**実装許可 (Implementation Authorization)**:
確認済みの設計をOrderとして実行へ移す明示的な許可。Articleを「読んで理解した」とするStock gateとは独立する。
_Avoid_: Design Confirmation、Stock、暗黙の着手

**レビュー境界 (Review Boundary)**:
実装がsettleした後、開始点からの完全な成果、検証結果、残差を一つのunitとして検収する境界。作業中の進捗観測をreviewへ混ぜない。
_Avoid_: 途中監視、部分diffだけの検収、Responseとの同一視

**作業ログ束縛 (Worklog Binding)**:
Orderの自己完結性を、開始前に確定したtask worklog directoryの絶対pathとorder文書（`order.md`）へ結びつける規律。暗黙のprivate pathを持たず、解決できない場合は着手しない。
_Avoid_: 暗黙の保存先、`tasks/`へのorder作成、worklog未確定での起動
