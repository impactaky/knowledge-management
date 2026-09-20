# Article Curation

理解をDraftからArticleへ整え、Stock、Replacement、reader calibration、learning progressを管理するモジュール。[Knowledge Management](../../CONTEXT.md)の語彙の一部をここに記述し、モデル全体で同じ意味と変更規律を共有する。

## Language

### Article lifecycle

**Article Curation**:
Understanding、Draft、Article、Stock、Replacementと、reader calibration / learning progressを担当する内部モジュール。
_Avoid_: 独立したBounded Context、単なる文章保存

**理解 (Understanding)**:
根拠、前提、適用範囲、不明点が整理され、単独で読み直せるArticleへ起こせる状態。素材の収集量や会話の完了とは独立する。
_Avoid_: Sourceの列挙、未整理のmemo、Articleとの同一視

**記事 (Article)**:
事実と根拠に基づく理解を、出自とともに自己完結で読みやすくした文書。導出・見解・不明点は事実と区別し、誤りや理解の更新が判明した箇所は、変更内容を議論して合意した新版へ差し替える。
_Avoid_: 決定の正本、出自のない記事、事実と見解の混同、誤りをsnapshotとして固定すること、自動更新

**記事境界 (Article Boundary)**:
単独で読み直したときに一つのまとまった理解を復元できる範囲。source、問い、session、章の数ではなく、独立して更新・参照する必要で分ける。
_Avoid_: 1 source 1 article、1問1 article、分離可能という理由だけの分割

**情報理解ワークフロー (Understanding Workflow)**:
自己完結したUnderstandingを作り、最上位で完了した場合だけArticle候補をDraftへ渡すworkflow群。内部利用では材料だけを親workflowへ返し、既存Articleで十分ならDraftを増やさない。
_Avoid_: 開始時の空Draft、入れ子ごとの重複Draft、既存Articleと同内容のDraft、workflow単位とArticle単位の同一視

**ソース要約 (Source Summary)**:
指定されたsourceだけに基づき、外部探索や補強をせず忠実に作るUnderstanding。別workflowから内部利用されたときは独立したDraftを作らない。
_Avoid_: 指定外sourceによる補強、生の箇条書きの恒久化、内部利用時の重複Draft

**調査 (Survey)**:
明確にした問いに対し、複数の外部根拠や実測からUnderstandingを作り、完了時にreview可能なDraftまで整えるworkflow。ArticleのStockや採用判断のDistillationは自動で行わない。
_Avoid_: 自動Stock、生の調査結果専用層、問い構造とArticle構造の同一視

**調査記事 (Survey Article)**:
Surveyで得たUnderstandingを出自とともにsnapshot化したArticle。普通のArticleとして扱い、問いの個数とは独立したArticle Boundaryを持つ。
_Avoid_: 専用layer、専用store、定型的な信頼度表の強制

**聞き取り記事 (Interview Article)**:
本人への一問一答で共有Understandingへ到達した後、その時点の理解を出自付きでsnapshot化したArticle。外部検証を無断で混ぜない。
_Avoid_: Agentによる無断の補強や訂正、既知知識の網羅的な記事化

**ストック (Stock)**:
Article本文、分類、Claim Line、必要な派生viewを本人がreviewし、「読んで理解した」かつ有用と明示判定した後に恒久化するgate。Implementation Authorizationとは独立し、自動では通らない。
_Avoid_: 自動Stock、後からの剪定前提、Implementation Authorization

**Stocked Article**:
Stock gateを通過してテーマへ配置され、自身のClaim LineとともにKnowledge Federationへ公開するArticle。Claim Lineの欠落は著述不備であり、公開を取り消す条件にはしない。
_Avoid_: Draft、採用された決定、Knowledge Package

**ドラフト (Draft)**:
完成したUnderstandingから作るStock判定待ちのArticle候補。判定中はArticle Curationが管理する可変な作業物で、検索、Claim Line、既知集合、Knowledge Federationへの公開対象にならない。
_Avoid_: 空file、同一候補の重複、判定済みArticle、検索対象、Knowledge Federationへの公開

**差し替え (Replacement)**:
誤りの訂正やUnderstandingの更新について、根拠と新旧差分をもとに本人と変更内容を議論し、合意した同じidentityの新版へ置き換える操作。旧版は履歴で保持し、Claim Lineも同時に更新する。
_Avoid_: wiki的な常時加筆、差分提示なしの置換、自動更新

**改稿 (Editorial Revision)**:
Understandingと出自を変えず、本人の明示指示でStocked Articleの表現や構成を読みやすくする操作。新版をreviewして置き換え、旧版を履歴に残し、内容変更はReplacementとして扱う。
_Avoid_: 新しい書き方への一括移行、自動rewrite、内容変更の隠蔽

### Article forms and publication

**記事層 (Article Layer)**:
Stocked Articleの層。「読み直して理解するもの」を持ち、「従うもの」を持つDistillation Layerとは分ける。増え続けてよく、検索用の派生indexを持てる。
_Avoid_: Distillation Layerの一部、wiki、決定の正本

**実行可能記事 (Executable Article)**:
計算の再実行や条件変更を通じて理解できるArticle形式。結論とsnapshotは散文が担い、変化しうる実行出力を正本にしない。
_Avoid_: 出力込みnotebook、code出力への結論の埋没、Article以外の実行物

**凍結ビュー (Frozen View)**:
Executable ArticleをStockまたはReplacement時点で実行して得る描画済みsnapshot。正本ではない派生物で、無断の再生成や閲覧時の再実行を行わない。
_Avoid_: 正本、常時再生成、on-demand実行、Draft previewとの同一視

**テーマ (Theme)**:
特定主題のStocked ArticleとClaim Lineをまとめる平坦な分類単位。Article自体に分類を重複記録せず、固有のdomain modelを持つ場合は別のBounded Contextとして判断する。
_Avoid_: category tree、Display Theme、Bounded Contextとの自動的な同一視

**主張行 (Claim Line)**:
Stocked Article自身が保持する、その記事の主張を短く記したsource付きの少数の検索可能な主張。記事を読むか判断する入口として公開し、titleの写しや本文全体の要約とは区別する。
_Avoid_: titleの写し、要約全文、網羅的な主張抽出

### Reader calibration

**読者調整 (Reader Calibration)**:
本人の反応を根拠にReader Modelを更新し、Article生成の取り消し可能なdefaultを調整するmodule。個々のArticleのStock gateや普遍的な品質判定ではない。
_Avoid_: Bounded Context、全Articleの必須gate、自動採点

**個人的わかりやすさ (Personal Clarity)**:
特定の読者がArticleをすっと理解できると感じる性質。同じUnderstandingでも構成、抽象度、例示、情報密度との相性により変わる。
_Avoid_: 普遍的なわかりやすさ、客観的品質との同一視

**読解摩擦 (Reading Friction)**:
内容自体の必要な難しさとは別に、文脈補完、要素統合、頭内simulation、表示変換を読者へ押しつけて生じる負荷。
_Avoid_: 内容の難しさ、無条件な単純化、情報量削減の目的化

**読者モデル (Reader Model)**:
Articleへの自然な反応を主な根拠として、Personal Clarityを漸進的に捉える仮説。固定規則ではなく、本人の判断を代替しない。
_Avoid_: 固定的な好み、文脈のない好み一覧、本人との同一視、自動採点器

**反応例 (Reaction Example)**:
一つのFriction Eventを、Article、説明文脈、修復、本人が明示した結果とともに扱うReader Modelの証拠。一件だけでPersonal Tendencyへ一般化しない。
_Avoid_: 反応logの別保存、文脈を捨てた好みlabel、一件からの固定規則、無反応の証拠化

**個人的傾向 (Personal Tendency)**:
複数の異なるReaction Exampleで支持された場合、または本人が明示した場合に候補とする、Article生成の取り消し可能なdefault。本人の承認後だけ恒久規則へ反映し、反例は適用文脈を分けるか傾向を弱める。
_Avoid_: 永続的な好み、文脈を越えた無条件適用、反例の無視

**敵対的読者 (Adversarial Reader)**:
Reader Modelを手がかりに、Articleが読者へ押しつける回避可能なReading Frictionを見つける内部批評役。執筆文脈から隔離し、読者と同じ表示だけを見せ、sourceや調査memoによる補完、事実検証、総合採点は担わせない。
_Avoid_: 自動採点器、事実検証、原典からの補強、文章案の勝ち抜き戦

**敵対レビュー (Adversarial Review)**:
代表的なArticleでReading Frictionの発見、修復、本人の反応確認を集中的に回し、Personal Tendencyを調整するsession。普段のArticle生成へ常設しない。
_Avoid_: 全Articleの必須品質gate、総合採点、個々のReaction Exampleの別保存、無承認のdefault更新

**摩擦トレース (Friction Trace)**:
Article内の場所、読者へ押しつけた作業、根拠を特定した検証可能なReading Frictionの予測。本人の反応と修復結果により予測、観測、解消、反証を区別する。
_Avoid_: 総合品質score、曖昧な「分かりにくい」、無反応による解消認定

**摩擦イベント (Friction Event)**:
読者が詰まり、必要な補完、頭内作業、表現や表示形式への変更要求を明示した観測。受動telemetryから無断で推定しない。
_Avoid_: Friction Traceとの同一視、暗黙行動からの無断推定

**摩擦収支 (Friction Balance)**:
修復で減るReading Frictionから、追加する文章、図、構造が生む長さ、視覚noise、navigation負荷を差し引いた効果。収支が改善しない予測は観測されるまで修復しない。
_Avoid_: Reading Frictionの網羅的除去、説明や図の無条件追加、長さだけの最小化

**主張骨格 (Claim Skeleton)**:
Personal Clarityの修復前に一時固定する、中心結論、前提と根拠、適用範囲、確定・導出・見解・不明の区別。修復後の意味変質を検証するために使う。
_Avoid_: Article構成template、主張の単純化、恒久的な要約、Adversarial Readerによる事実検証

**わかりやすさプローブ (Clarity Probe)**:
Article Draftの単独で意味が通る説明単位について、本人が理解できるか、何が欠けるかを小さく確かめる問い。代替関係でない要素を排他的に選ばせない。
_Avoid_: 文脈を失う局所表現の切り出し、全文比較、客観評価、補完要素の排他的選択、理由の強制

### Learning progress

**学習進捗 (Learning Progress)**:
大きなsourceの学習状態と、実際の既知集合に対するArticle Layerのずれを漸進的に扱うmodule。Articleを網羅的な知識台帳にはしない。
_Avoid_: Bounded Context、完全な既知集合、Articleの自動生成

**すり合わせ (Knowledge Reconciliation)**:
Article LayerとReading Mapを本人の既知集合の不完全な写像として扱い、本人の訂正を通じて時間をかけて収束させる原則。過去知識の網羅的なbackfillは行わない。
_Avoid_: 写像の完全性、既知知識の網羅的な記事化

**読書マップ (Reading Map)**:
大部のsourceを少しずつ学ぶための要素と学習状態の管理物。Articleではない可変状態で、Article候補の最終判定まで進捗確定を待つ。
_Avoid_: Article、source要約の置き場、事前の完全分解
