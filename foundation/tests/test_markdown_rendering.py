from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from federation_support import ROOT

import server


class MarkdownRenderingTest(unittest.TestCase):
    def render(self, markdown: str, *, embed: bool = False) -> str:
        with tempfile.TemporaryDirectory() as directory:
            article = Path(directory) / "fixture.md"
            article.write_text(markdown, encoding="utf-8")
            return server.render_markdown(article, embed=embed)

    def test_gfm_features_use_standard_html(self) -> None:
        output = self.render(
            """# GFM

~~removed~~

- [x] shipped
- [ ] pending

| name | value |
| --- | --- |
| one | two |

https://example.com/path
"""
        )

        self.assertIn("<s>removed</s>", output)
        self.assertIn('type="checkbox"', output)
        self.assertIn('checked="checked"', output)
        self.assertIn("<table>", output)
        self.assertIn('<a href="https://example.com/path">', output)

    def test_math_and_browser_dependencies_are_local(self) -> None:
        output = self.render("Inline $x^2$ and block:\n\n$$\\sum_i x_i$$\n")

        self.assertIn('<span class="math inline">x^2</span>', output)
        self.assertIn('<div class="math block">', output)
        self.assertIn("/static/browser-assets/katex.min.css", output)
        self.assertIn("/static/browser-assets/katex.min.js", output)
        self.assertIn("/static/browser-assets/mermaid.min.js", output)
        self.assertIn("/static/browser-assets/water.min.css", output)
        self.assertNotIn("cdn.jsdelivr.net", output)

    def test_language_fence_is_highlighted_without_dangling_code_close(self) -> None:
        output = self.render("```python\nprint('ok')\n```\n")

        article_html = output.split("<article>", 1)[1].split("</article>", 1)[0]
        self.assertIn('<div class="highlight"><pre>', article_html)
        self.assertIn('class="nb"', article_html)
        self.assertNotIn("</code></pre>", article_html)
        self.assertEqual(article_html.count("<pre>"), article_html.count("</pre>"))

    def test_yaml_front_matter_preserves_scalars_and_lists(self) -> None:
        output = self.render(
            """---
title: Fixture title
source:
  - first source
  - second source
surveyed: 2026-07-13
---
# Body
"""
        )

        self.assertIn("<dt>title</dt><dd>Fixture title</dd>", output)
        self.assertIn("<li>first source</li><li>second source</li>", output)
        self.assertIn("<dt>surveyed</dt><dd>2026-07-13</dd>", output)
        self.assertNotIn("\n---\n", output)

    def test_relative_references_heading_anchors_and_toc(self) -> None:
        output = self.render(
            "## Linked *heading*\n\n![diagram](images/diagram.png)\n\n[Next](next.md#part)\n",
            embed=True,
        )

        self.assertIn('<h2 id="linked-heading">Linked <em>heading</em></h2>', output)
        self.assertIn('<nav class="toc">', output)
        self.assertIn('<a href="#linked-heading">Linked heading</a>', output)
        self.assertIn('src="/asset?file=', output)
        self.assertIn('href="/article?file=', output)
        self.assertIn("&embed=1#part", output)

    def test_raw_html_is_escaped(self) -> None:
        output = self.render("Before\n\n<script>globalThis.pwned = true</script>\n\nAfter\n")

        article_html = output.split("<article>", 1)[1].split("</article>", 1)[0]
        self.assertIn("&lt;script&gt;globalThis.pwned = true&lt;/script&gt;", article_html)
        self.assertNotIn("<script>", article_html)
        self.assertIn("Before", article_html)
        self.assertIn("After", article_html)

    def test_mermaid_fences_keep_source_and_render_independently(self) -> None:
        output = self.render("Before\n\n```mermaid\ngraph TD\n  A --> B\n```\n\nAfter\n")

        self.assertIn('class="mermaid-source"', output)
        self.assertIn("graph TD", output)
        self.assertIn('class="mermaid-render"', output)
        self.assertNotIn('<code class="language-mermaid">', output)
        self.assertIn("Before", output)
        self.assertIn("After", output)

    def test_mermaid_runtime_success_hides_source_and_failure_restores_it(self) -> None:
        if not shutil.which("deno"):
            self.skipTest("deno not found")
        output = self.render("```mermaid\ngraph TD\n  A --> B\n```\n")
        handler = re.search(
            r'document\.addEventListener\("DOMContentLoaded".*?^\}\);', output, re.M | re.S
        ).group()
        script = '''
const EXTRACTED = ''' + json.dumps(handler) + ''';
async function scenario(mermaidImpl) {
  let handler;
  const blockClassList = { values: new Set(), add(value) { this.values.add(value); } };
  const sourceNode = { hidden: false, textContent: "graph TD\\n  A --> B" };
  const outputNode = { textContent: "", innerHTML: "", classList: blockClassList, values: new Set() };
  const block = {
    classList: blockClassList,
    querySelector(selector) {
      if (selector === ".mermaid-source") return sourceNode;
      if (selector === ".mermaid-render") return outputNode;
      return null;
    },
  };
  const document = {
    addEventListener(name, fn) { handler = fn; },
    querySelectorAll(selector) { return selector.includes("mermaid") ? [block] : []; },
  };
  const window = { mermaid: mermaidImpl };
  eval(EXTRACTED);
  await handler();
  return { block, sourceNode, outputNode };
}
const ok = await scenario({ initialize() {}, async render() { return { svg: "<svg></svg>" }; } });
if (ok.outputNode.innerHTML !== "<svg></svg>") throw new Error("svg not injected");
if (ok.sourceNode.hidden !== true) throw new Error("source not hidden on success");
if (ok.block.classList.values.has("mermaid-error")) throw new Error("false error on success");
const failed = await scenario({ initialize() {}, async render() { throw new Error("boom"); } });
if (!failed.block.classList.values.has("mermaid-error")) throw new Error("failure not marked");
if (!failed.outputNode.textContent.includes("Mermaid rendering failed:") || !failed.outputNode.textContent.includes("boom")) throw new Error("failure message missing");
if (failed.sourceNode.hidden !== false) throw new Error("source not restored");
console.log("mermaid runtime fallback passed");
'''
        completed = subprocess.run(
            ["deno", "run", "--no-config", "-"],
            input=script, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_generated_assets_are_ignored_and_untracked(self) -> None:
        asset_dir = "foundation/tools/knowledge-ui/static/browser-assets"
        asset = f"{asset_dir}/mermaid.min.js"
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", asset_dir], cwd=ROOT, check=False
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", asset],
            cwd=ROOT, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        self.assertEqual(ignored.returncode, 0)
        self.assertNotEqual(tracked.returncode, 0)


if __name__ == "__main__":
    unittest.main()
