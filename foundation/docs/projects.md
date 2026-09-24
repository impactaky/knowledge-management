# Projectsの配置と配布

[プロジェクトルール](../modules/knowledge-federation/CONTEXT.md)は、利用者の私的storeなど選択済みのstoreに`projects` Packageとして置く。この文書は配置と配布の契約だけを定め、個々のprojectの中身はこのrepositoryに置かない。

## 配置

```text
<store>/projects/INDEX.md
<store>/projects/<repository-basename>/rules.md
<store>/projects/<repository-basename>/<distilled-note>.md
```

- **Catalog**: storeのCatalogに`projects/INDEX.md`を入口とするPackageを一つ登録する。projectごとのPackageを増やさない。
- **INDEX**: そのstore固有の境界判定と、project一覧(各directoryへのlinkと一行説明)を置く。
- **rules.md**: 対象repositoryで作業するagentへ配布する規則の正本。
- **directory名**: 対象repositoryのbasenameと一致させる。配布はこの名前だけで対応を解決するため、名前の一致が配布の契約になる。
- **同居できるもの**: そのprojectの理解・私見メモなどの蒸留層file。記事の実体は記事層に置き、ここからは参照だけにする。

## 配布

配備側(dotfiles、checkout hook、harness設定など)が、対象repositoryへ`rules.md`をsymlinkなどで接続する。接続先の名前と手段は配備の設定で明示し、`projects/`側へ複製しない。対象repositoryに対応directoryが無ければ何も接続しない。

## 境界

- **内容単位で判定する**: 共有しうるdomain知識や汎用規則は、より広いPackageを正本にする。書く前にFederated Searchで既存の正本を確認する。
- **マシン固有の値は書いてよい**: データpath、device IP、build cacheの場所など、特定repositoryと特定実行環境の組合せだけで意味を持つ値はここが正本。
- **連合内への参照は名前で**: 他Packageの規則は「<Package名> の <Package内相対path>」の一行で参照し、絶対pathや全文copyを書かない。
- **昇格**: 汎用化できた規則は[知識更新規則](knowledge-changes.md)に従って広いPackageへ移し、元に参照行を残す。
