# Knowledge Management

知識の作成・採用・保存・再利用と、それに伴う作業を一つのモデルと言語で扱う語彙。このリポジトリ（knowledge-management）は再利用可能な検索コア、MCP、UI、インデクサ、チェッカー、8つの知識ワークフロー、および共有語彙・規約の正本である。

個人の知識や組織の機密は別個の信頼境界を持つ各知識ストア（Private Store等）で管理され、本共有基盤のコードを実行して利用する。各ストアは独自のCatalog、Knowledge Package、記事層、作業状態を保持する。

語彙はモジュール別ファイルへ分冊しているが、どの語もこのモデル全体で一貫した意味を持つ。

- [Knowledge Federation](modules/knowledge-federation/CONTEXT.md) — 知識の保存・解決・蒸留・横断検索
- [Article Curation](modules/article-curation/CONTEXT.md) — 記事の理解・執筆・採用・更新・読解摩擦の改善

## System

**知識管理 (Knowledge Management)**:
知識の作成・採用・保存・再利用と関連する作業を、一貫したモデルと言語で扱う体系。再利用可能な共通機構と、信頼境界ごとに分かれた各知識ストアからなる。
_Avoid_: 単一の集中型データベース、ツールと個別ストアの混同

**知識ストア (Knowledge Store)**:
利用者の知識原本を、再利用可能な共通ツールや他者のストアから閲覧権限（Trust Boundary）に応じて分離して保持する保存先。
_Avoid_: ツールリポジトリへの個人知識の混入

**主張 (Claim)**:
記事の著者が明記した検索可能な主張。定義および運用規則は [article-curation の主張行](modules/article-curation/CONTEXT.md#article-forms-and-publication) を正本とする。検索によって取得されたこと自体はその主張の正しさや妥当性を証明するものではなく、読者が原本を検証するための入口として機能する。
_Avoid_: titleの写し、要約全文、網羅的な主張抽出、検索一致を主張の証明と見なすこと

**派生インデックス (Derived Index)**:
原本から生成される使い捨ての検索用状態。原本からいつでも再構築可能であり、これを直接編集して正本にしてはならない。
_Avoid_: 検索インデックスの正典化

**操作入口 (Operational Entry Point)**:
エージェントやハーネスが、複数のKnowledge Packageまたは外部systemに対するworkflowを起動する境界。知識の保存・参照単位ではなく、特定Packageの内部実装として所有させない。
_Avoid_: Knowledge Package、特定Packageの内部構成物

**境界づけられたコンテキスト (Bounded Context)**:
一つのドメインモデルと言葉の意味を一貫させ、その適用範囲を明示した境界。境界はモデルの適用と継続的な整合性維持の実態から判断し、操作や不変条件の違いだけでは分けない。
_Avoid_: Knowledge Package、repository、Trust Boundary、モジュールとの同一視

**モジュール (Module)**:
一つのモデルの内部を、関連する概念と責務で整理する単位。モジュール別に語彙を記述しても、語の意味と変更規律はモデル全体で共有する。
_Avoid_: Bounded Context、独立した語義の境界

**活動ストリーム (Activity Stream)**:
各エージェントがnative形式のまま蓄積する会話ログ、session履歴、作業記録。正典ではなく、採用された結論そのものはここには存在しない。
_Avoid_: 生ログ、履歴、正本、蒸留済み知識、記事

## Operational conventions

**ラッパースキル (Wrapper Skill)**:
上流skillを無改変のまま名前で参照し、自分の差分だけを自分の正本として持つskill。上流更新への追従と独自規約を分離する。
_Avoid_: 上流copyの直接編集、vendor、上流と同名のshadowing
