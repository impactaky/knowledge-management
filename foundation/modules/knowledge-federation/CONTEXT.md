# Knowledge Federation

信頼境界ごとに分かれたKnowledge Packageを、Catalogと解決規約で正本を重複させず一体として利用するモジュール。[Knowledge Management](../../CONTEXT.md)の語彙の一部をここに記述し、モデル全体で同じ意味と変更規律を共有する。

## Language

### Structure

**Knowledge Federation**:
Knowledge Packageの保存・解決・蒸留とFederated Searchを担当する内部モジュール。
_Avoid_: 独立したBounded Context、知識基盤全体、Knowledge Packageとの同一視

**連合 (Federation)**:
記憶をTrust Boundaryごとに別々のstoreへ置いたまま、解決規約で繋いで一体として機能させる方式。Trust Boundaryが可視性、Knowledge Packageが保存と参照、Catalogが入口、Resolutionが選択を担い、境界をまたいで正本を統合しない。
_Avoid_: 集約、統合store、一元化、単一の意味境界

**信頼境界 (Trust Boundary)**:
その知識を誰が読んでよいかを定める境界。保存先を分ける軸であり、知識の適用範囲やBounded Contextとは独立する。
_Avoid_: scope、Bounded Context

**パッケージ (Knowledge Package)**:
Catalogから解決できる知識の保存・参照単位。適用範囲とTrust Boundaryに従って置かれ、モデルと言語の一貫性を表す境界ではない。
_Avoid_: Bounded Contextとの同一視、layer、階層

**適用条件 (Applicability Conditions)**:
知識が成り立つ対象、前提、版や環境の制約、例外を定める条件。保存先の広さや閲覧を許可する範囲とは独立し、正本を移しても根拠なく拡張しない。
_Avoid_: Trust Boundary、保存場所だけによる一般化、条件を失った再利用

**INDEX.md**:
Knowledge Package内で読む文書の条件や固有の規則を示し、必要な正本へ辿るための任意の案内。Packageの成立条件ではない。
_Avoid_: 必須の登録要件、全ファイルの一覧、内容全文の複製、Context Map

**推奨形 (Grill-Compatible Shape)**:
glossaryとdecision recordを備え、用語と判断を対話中にそのまま正本へ着地させられるKnowledge Packageの推奨構造。すべてのPackageへ強制する必須形ではない。
_Avoid_: Packageの必須条件、Bounded Contextの物理構造

**共有基盤 (Common Core)**:
再利用可能な検索コア、MCP、UI、ワークフロー、共有語彙・規約を正本として管理するリポジトリ。私的な知識や組織の機密は含まず、各ストアから選ばれて実行される。
_Avoid_: 下流コピー、各ストアへのコード複製

**私的ストア (Private Store)**:
個人または特定組織の知識、Catalog、固有パッケージ、記事層、作業状態を保持するストア。共有基盤のコードを実行して利用し、閲覧権限や信頼境界（Trust Boundary）に応じて共有基盤や他のストアから独立して保持される。
_Avoid_: 共有基盤との同一視、ツールの二重実装、異なる信頼境界の知識の混同

### Resolution

**解決 (Resolution)**:
ある作業文脈で読むべきKnowledge Packageを決めるcapability。保存単位の設計とは独立し、AnchorとDynamic Resolutionを組み合わせる。
_Avoid_: Distillation、Packageの作成

**アンカー (Anchor)**:
読み損ねが致命的なKnowledge Packageへ作業文脈から張る決定的な静的参照。すべての依存を列挙する宣言ではない。
_Avoid_: manifest、uses宣言

**ミラー (Bundled Mirror)**:
Anchor先の正本を直接参照できない境界へ同梱する、出自と同期時点を明示した派生物。正本を持つ環境では正本を優先する。
_Avoid_: 第二の正本、無管理のcopy

**カタログ (Catalog)**:
全Knowledge Packageの名前、使う条件、所在を示すResolutionの入口。Packageを解決する索引であり、モデル境界間の関係を表すContext Mapではない。
_Avoid_: Context Mapとの同一視、Package内容の全文、registry database

**動的解決 (Dynamic Resolution)**:
作業や会話の文脈ごとに、Catalogから関連するKnowledge Packageを判断して開くこと。Anchor対象以外を常時loadせず、読み損ねの可能性を受け入れる。
_Avoid_: 自動load、常時load

**横断検索 (Federated Search)**:
Catalogから導出したKnowledge Package集合のうち、検索へ公開した知識を対象とするcapability。Articleはテーマに配置されたStocked Articleと記事自身のClaim Lineを対象とし、Draft、Learning Progressの作業状態、テスト用データを知識として返さない。派生indexを使っても公開範囲と検索意味論を共有し、適合候補がなければ該当なしを返す。
_Avoid_: RAGとの同一視、検索専用registry、固定directory走査、毎回の自動想起

**連合プロバイダ (Federation Provider)**:
CatalogとFederated Searchを連合の利用者へ供給するadapterの総称。知識のcopyを持たず、検索対象集合をCatalogから導出し、provider間で検索意味論を一致させる。
_Avoid_: Catalogの複製、RAG基盤との同一視、既製providerによるmodelの置換、provider固有のcorpus、知識の正本

### Distillation and operation

**蒸留層 (Distillation Layer)**:
Knowledge PackageとCatalogからなるcurated knowledgeの層。従うための知識を漸進的開示で読める大きさに保つKnowledge Federationの中核である。
_Avoid_: Article Layer、RAG対象、無制限のknowledge base

**蒸留 (Distillation)**:
Activity Streamから採用する事実や判断を抽出し、適切なKnowledge Packageへ著述するcapability。検索結果だけでは結論は正本にならず、著述して初めて存在する。
_Avoid_: 要約、archive、ArticleのStock

**随時蒸留 (In-Flow Distillation)**:
日常作業中に気づいた恒久知識をその場でKnowledge Packageへ著述する基本のDistillation。書き先に迷ったら狭い適用範囲へ置き、後のPromotionに委ねる。
_Avoid_: session後の一括要約、Re-distillation

**重蒸留 (Heavy Distillation)**:
用語の確立、矛盾の解消、Package間のPromotionを明示的な対話で行う重いDistillation。
_Avoid_: Re-distillation

**再蒸留 (Re-distillation)**:
保持されたActivity Streamを遡り、過去の知見からKnowledge Packageを更新するbatch的内省。In-Flow Distillationで落ちた知識を補う安全網である。
_Avoid_: In-Flow Distillation、Activity Streamの自動正典化

**agentベース管理 (Agent-Curated)**:
知識の著述、更新、Promotion、Catalog更新をAgent自身が行うというKnowledge Federationの前提。
_Avoid_: 人手だけのfile整理、build時pipelineだけによる管理

**ポインタ (Pointer)**:
利用者の常時load機構からCatalogの所在と読み方規約へ張る最小の間接参照。正本はCatalog側だけに置き、利用者別設定へ複製しない。
_Avoid_: Catalogのcopy、利用者別のPackage一覧

**プロジェクトルール (Project Rules)**:
特定repositoryと特定実行環境の組合せだけで意味を持つ運用知識。共有可能なdomain知識や汎用規則は適切なKnowledge PackageへPromotionし、ここへ影の正本を作らない。
_Avoid_: 共有可能な知識のcopy、repository単位だけでのTrust Boundary判定

**リンク規律 (Link Discipline)**:
正本間の参照は関係の性質を一言で説明できる場合だけ張る規律。正本一箇所と片方向参照を保ち、既存node間の装飾的な相互linkを増やさないが、未作成の正本へのforward pointerは許す。
_Avoid_: 全nodeの相互link、関係を説明できないlink、重複定義

**昇格 (Promotion)**:
ある文脈で見つかった知識の正本を、より広く再利用するKnowledge Packageへ移し、元には参照を残す行為。適用条件、根拠、出自を保持し、適用範囲そのものを広げる場合は追加の根拠に基づく判断として採用する。著述前のFederated Searchで重複概念を確認する。
_Avoid_: copy、同期、複数正本、移動や類似検索だけを根拠とする一般化
