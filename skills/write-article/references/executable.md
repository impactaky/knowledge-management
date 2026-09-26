# 実行可能記事

計算・実測・可視化の再実行が理解を助ける場合だけ、正本をmarimo notebook `.py` とする。見栄えや無意味な操作機能のために選ばない。

## 正本

- notebookは `import marimo`、`app = marimo.App()`、`@app.cell`のセル、`app.run()`を持つPythonファイルとする。セル間の値はreturnで渡し、循環依存や同じ名前の重複定義を避ける
- PEP 723ヘッダへ `requires-python` と依存ライブラリを書く
- 第1セルを静的な `mo.md` とし、出自と`claims`文字列配列を含むYAML frontmatter、タイトルを置く
- 検索させる散文セルにf-stringや文字列結合を使わない
- 結論となる数値・主張を必ず散文セルにも書く
- コード出力だけへ結論を埋めない
- 意味のある条件変更だけUIにし、不要なボタン・スライダーを作らない

## プレビューと凍結ビュー

ドラフト中は `.py` を直接編集し、またはproject内に導入したmarimo editorを使い、必要なら使い捨てHTMLを `drafts/__marimo__/` へ生成する。ストック時はプレビューを流用せず、次の形で凍結ビューを生成し直す。

```bash
uv run --project /path/to/knowledge-ui --extra notebook marimo export html --sandbox --no-include-code /absolute/path/to/article.py -o /absolute/path/to/__marimo__/article.html
```

凍結ビューは正本ではない派生物で、ストックと差し替え・改稿時だけ再生成する。表示のためのオンデマンド再実行はしない。

`/path/to/knowledge-ui` は選択済みCatalogの shared-rules の `tools/knowledge-ui/` から解決する。
外部のmarimo skillsは任意であり、このworkflowの必須依存ではない。
