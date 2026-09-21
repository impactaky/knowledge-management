"""
Knowledge Browser 検索・閲覧 Webサーバ

FastAPI, 127.0.0.1:7776

Endpoints:
  GET /              → static/index.html
  GET /api/tree      → CATALOG.md 由来パッケージの記事ツリー
  GET /api/drafts    → このストア直下の drafts/ 記事ツリー
  GET /api/suggest   → entriesインデックスへ語彙検索
  GET /api/search    → 共通検索契約 + 人間向け記事snippet
  GET /api/grep      → 明示文字列検索の所在
  GET /article       → mdファイルをHTMLレンダリング(パッケージ境界チェック付き)
  POST /api/reindex  → indexerをその場で実行
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import meilisearch
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name

from indexer import extract_marimo_markdown, extract_markdown_title, main as run_indexer
from indexer import parse_catalog as read_catalog_packages
from indexer import corpus_fingerprint as index_corpus_fingerprint
from launch import terminate_process_group

CORE_PATH = Path(__file__).resolve().parents[2] / "integrations" / "federation-core"
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from federation_core import search as federation_core_search  # noqa: E402
from federation_core import grep as federation_core_grep  # noqa: E402
from federation_core.articles import read_article
from federation_core.locators import current_entry
from federation_core.publication import PublicationScope  # noqa: E402

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

from federation_core.core import resolve_catalog_path

CATALOG_PATH = resolve_catalog_path()
DRAFTS_ROOT = (CATALOG_PATH.parent / "drafts").resolve()
MEILI_URL = os.environ.get("MEILI_URL", "")
STATIC_DIR = Path(__file__).parent / "static"
BROWSER_ASSET_DIR = STATIC_DIR / "browser-assets"
LIVE_MARIMO_HOST = (
    os.environ.get("KNOWLEDGE_MARIMO_HOST")
    or os.environ.get("UI_HOST")
    or "127.0.0.1"
)
LIVE_MARIMO_PORT_START = int(os.environ.get("KNOWLEDGE_MARIMO_PORT_START", "7780"))
LIVE_MARIMO_PORT_END = int(os.environ.get("KNOWLEDGE_MARIMO_PORT_END", "7879"))
LIVE_MARIMO_IDLE_SECONDS = int(os.environ.get("KNOWLEDGE_MARIMO_IDLE_SECONDS", "3600"))
SKIP_TREE_DIRS = {
    ".git",
    ".agents",
    ".codex",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__marimo__",
    "__pycache__",
    "node_modules",
}

app = FastAPI(title="Knowledge Browser")
app.mount(
    "/static/browser-assets",
    StaticFiles(directory=BROWSER_ASSET_DIR, check_dir=False),
    name="browser-assets",
)

# ---------------------------------------------------------------------------
# カタログ解析 → パッケージルート一覧 (セキュリティ検証用)
# ---------------------------------------------------------------------------

def parse_catalog_packages(catalog_path: Path) -> list[dict]:
    """Use the same package resolution as search and indexing."""
    return [p for p in read_catalog_packages(catalog_path) if p["root"].exists()]


def parse_catalog_roots(catalog_path: Path) -> list[Path]:
    """カタログから全パッケージルートの realpath リストを返す。"""
    return [pkg["root"] for pkg in parse_catalog_packages(catalog_path)]


PACKAGE_ROOTS: list[Path] = []


def is_safe_path(file_path: str) -> bool:
    """ファイルがカタログパッケージまたはこのストアの drafts/ 配下にあることを確認。"""
    try:
        real = Path(file_path).resolve()
    except Exception:
        return False
    for root in [*parse_catalog_roots(CATALOG_PATH), DRAFTS_ROOT]:
        try:
            real.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _read_markdown_title(file_path: Path) -> str:
    """Markdownの最初のH1をタイトルとして返す。無ければstem。"""
    try:
        for line in file_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# ") and not line.startswith("## "):
                title = line[2:].strip()
                if title:
                    return title
    except OSError:
        pass
    return file_path.stem


def _read_article_title(file_path: Path) -> str:
    """Markdown/実行可能記事の表示タイトルを返す。"""
    try:
        if file_path.suffix.lower() == ".py":
            return extract_markdown_title(extract_marimo_markdown(file_path), file_path.stem)
        return _read_markdown_title(file_path)
    except OSError:
        return file_path.stem


def _directory_node(name: str, rel: str) -> dict:
    return {"type": "dir", "name": name, "rel": rel, "children": []}


def _looks_like_executable_article(path: Path) -> bool:
    try:
        return bool(extract_marimo_markdown(path).strip())
    except OSError:
        return False


def _prune_tree_dirs(dirnames: list[str]) -> None:
    dirnames[:] = [
        name for name in dirnames
        if name not in SKIP_TREE_DIRS and not name.startswith(".")
    ]


def _visible_markdown_dirs(root: Path) -> set[Path]:
    dirs: set[Path] = set()
    for current, dirnames, filenames in os.walk(root):
        _prune_tree_dirs(dirnames)
        if any(Path(filename).suffix.lower() == ".md" for filename in filenames):
            dirs.add(Path(current))
    return dirs


def iter_visible_article_files(
    root: Path,
    *,
    include_py: bool = False,
    require_markdown_dir_for_py: bool = True,
):
    """UIツリー・監視対象に出す記事ファイル。hidden/cache/vendor配下は除外する。"""
    markdown_dirs = (
        _visible_markdown_dirs(root)
        if include_py and require_markdown_dir_for_py
        else set()
    )
    for current, dirnames, filenames in os.walk(root):
        _prune_tree_dirs(dirnames)
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            suffix = Path(filename).suffix.lower()
            if suffix == ".md":
                yield path
                continue
            if suffix != ".py" or not include_py:
                continue
            if require_markdown_dir_for_py and current_path not in markdown_dirs:
                continue
            if _looks_like_executable_article(path):
                yield path


def build_markdown_tree(
    root: Path,
    *,
    exclude_roots: list[Path] | None = None,
    include_py: bool = False,
    require_markdown_dir_for_py: bool = True,
) -> list[dict]:
    """パッケージroot配下の記事をディレクトリ階層として返す。"""
    dirs: dict[str, dict] = {"": _directory_node("", "")}
    exclude_roots = exclude_roots or []

    def is_excluded(path: Path) -> bool:
        for excluded in exclude_roots:
            try:
                path.resolve().relative_to(excluded)
                return True
            except ValueError:
                continue
        return False

    article_files = [
        p for p in iter_visible_article_files(
            root,
            include_py=include_py,
            require_markdown_dir_for_py=require_markdown_dir_for_py,
        )
        if p.is_file() and p.resolve().is_relative_to(root.resolve()) and not is_excluded(p)
    ]
    for article_file in sorted(article_files, key=lambda p: str(p.relative_to(root)).lower()):
        rel_path = article_file.relative_to(root)
        parent_rel = ""
        parent = dirs[""]

        for part in rel_path.parts[:-1]:
            next_rel = f"{parent_rel}/{part}" if parent_rel else part
            if next_rel not in dirs:
                node = _directory_node(part, next_rel)
                dirs[next_rel] = node
                parent["children"].append(node)
            parent = dirs[next_rel]
            parent_rel = next_rel

        parent["children"].append(
            {
                "type": "file",
                "name": rel_path.name,
                "title": _read_article_title(article_file),
                "articleType": "executable" if article_file.suffix.lower() == ".py" else "markdown",
                "rel": str(rel_path),
                "file": str(article_file.resolve()),
            }
        )

    def sort_children(node: dict) -> None:
        node["children"].sort(
            key=lambda child: (
                0 if child["type"] == "file" and child["name"].lower() == "index.md" else 1,
                0 if child["type"] == "dir" else 1,
                child["name"].lower(),
            )
        )
        for child in node["children"]:
            if child["type"] == "dir":
                sort_children(child)

    sort_children(dirs[""])
    return dirs[""]["children"]


def describe_catalog_path(file_path: str) -> dict:
    """ファイルパスが属するカタログパッケージと相対パスを返す。"""
    try:
        real = Path(file_path).resolve()
    except Exception:
        return {"package": "", "rel": file_path}
    packages = sorted(parse_catalog_packages(CATALOG_PATH), key=lambda p: len(str(p["root"])), reverse=True)
    for pkg in packages:
        root: Path = pkg["root"]
        try:
            return {"package": pkg["name"], "rel": str(real.relative_to(root))}
        except ValueError:
            continue
    return {"package": "", "rel": str(real)}


# ---------------------------------------------------------------------------
# Meilisearch クライアント
# ---------------------------------------------------------------------------

meili_client = meilisearch.Client(MEILI_URL, os.environ.get("MEILI_API_KEY")) if MEILI_URL else None


# ---------------------------------------------------------------------------
# Markdownレンダラ
# ---------------------------------------------------------------------------


def _slugify_heading(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^\w\s\-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-") or "section"


_md = (
    MarkdownIt("gfm-like", {"html": False, "linkify": True})
    .use(front_matter_plugin)
    .use(dollarmath_plugin)
    .use(tasklists_plugin)
    .use(anchors_plugin, max_level=6, slug_func=_slugify_heading)
)


def _scope_css(css: str, scope: str) -> str:
    def repl(match: re.Match) -> str:
        selectors = [s.strip() for s in match.group(1).split(",")]
        return ", ".join(f"{scope} {selector}" for selector in selectors) + " {"

    return re.sub(r"([^{}]+)\s*\{", repl, css)


_pygments_css = HtmlFormatter(style="friendly").get_style_defs(".highlight")
_pygments_dark_css = _scope_css(
    HtmlFormatter(style="native").get_style_defs(".highlight"),
    'html[data-display-theme="dark"]',
)


def _highlight_code(code: str, lang: str) -> str:
    try:
        lexer = get_lexer_by_name(lang or "text")
    except Exception:
        lexer = TextLexer()
    formatter = HtmlFormatter(style="friendly", cssclass="highlight")
    return highlight(code, lexer, formatter)


def _render_front_matter_value(value: object) -> str:
    if isinstance(value, list):
        items = "".join(f"<li>{_render_front_matter_value(item)}</li>" for item in value)
        return f"<ul>{items}</ul>"
    if isinstance(value, dict):
        items = "".join(
            f"<dt>{html.escape(str(key))}</dt><dd>{_render_front_matter_value(item)}</dd>"
            for key, item in value.items()
        )
        return f"<dl>{items}</dl>"
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return html.escape(str(value))


def _render_front_matter(
    _renderer: object, tokens: list[Token], idx: int, *_args: object
) -> str:
    source = tokens[idx].content
    try:
        fields = yaml.safe_load(source)
    except yaml.YAMLError as error:
        return (
            '<aside class="frontmatter frontmatter-error">'
            '<strong>Front matter error</strong>'
            f"<pre>{html.escape(source)}</pre><p>{html.escape(str(error))}</p></aside>\n"
        )
    if not isinstance(fields, dict):
        return f'<aside class="frontmatter"><pre>{html.escape(source)}</pre></aside>\n'
    items = "".join(
        f"<dt>{html.escape(str(key))}</dt><dd>{_render_front_matter_value(value)}</dd>"
        for key, value in fields.items()
    )
    return f'<aside class="frontmatter"><dl>{items}</dl></aside>\n'


def _render_fence(
    _renderer: object, tokens: list[Token], idx: int, *_args: object
) -> str:
    token = tokens[idx]
    language = token.info.strip().split(maxsplit=1)[0] if token.info.strip() else ""
    if language.lower() == "mermaid":
        source = html.escape(token.content)
        return (
            '<div class="mermaid-block">'
            f'<pre class="mermaid-source"><code>{source}</code></pre>'
            '<div class="mermaid-render" aria-live="polite"></div>'
            "</div>\n"
        )
    return _highlight_code(token.content, language)


_md.add_render_rule("front_matter", _render_front_matter)
_md.add_render_rule("fence", _render_fence)


def _heading_text(token: Token) -> str:
    if not token.children:
        return token.content
    visible_types = {"text", "code_inline", "math_inline", "image"}
    return "".join(child.content for child in token.children if child.type in visible_types)


def render_markdown(file_path: Path, *, embed: bool = False) -> str:
    """Markdownを完全なHTMLページとして返す。目次・アンカー・KaTeX・Mermaid付き。"""
    text = file_path.read_text(encoding="utf-8", errors="replace")

    env: dict = {}
    tokens = _md.parse(text, env)
    body_html = _md.renderer.render(tokens, _md.options, env)

    # 相対参照の解決: 画像は /asset、.md リンクは /article へ書き換える
    base_dir = file_path.parent

    def _resolve_rel(m: re.Match) -> str:
        attr, url = m.group(1), m.group(2)
        if re.match(r"^(https?:|/|#|data:|mailto:)", url):
            return m.group(0)
        url_path, sep, url_anchor = url.partition("#")
        target = (base_dir / url_path).resolve()
        anchor = f"#{url_anchor}" if sep else ""
        if str(target).lower().endswith(".md"):
            embed_param = "&embed=1" if embed else ""
            return f'{attr}="/article?file={quote(str(target))}{embed_param}{anchor}"'
        return f'{attr}="/asset?file={quote(str(target))}"'

    body_html = re.sub(r'\b(src|href)="([^"]+)"', _resolve_rel, body_html)

    # anchors_plugin が付けた見出しIDから目次を組み立てる。
    toc_items: list[tuple[int, str, str]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag not in {"h2", "h3"}:
            continue
        inline = tokens[index + 1]
        anchor = token.attrGet("id")
        if inline.type == "inline" and anchor:
            toc_items.append((int(token.tag[1]), _heading_text(inline), anchor))

    # 目次HTML
    toc_html = ""
    if toc_items:
        toc_lines = ['<nav class="toc"><h2>目次</h2><ul>']
        for level, text_t, slug in toc_items:
            indent = "  " * (level - 2)
            toc_lines.append(
                f'{indent}<li><a href="#{html.escape(slug, quote=True)}">'
                f"{html.escape(text_t)}</a></li>"
            )
        toc_lines.append("</ul></nav>")
        toc_html = "\n".join(toc_lines)

    back_link = "" if embed else '<p><a href="/">&larr; 検索に戻る</a></p>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(file_path.name)}</title>
<script>
(() => {{
  function applyDisplayTheme(theme) {{
    document.documentElement.dataset.displayTheme = theme === "dark" ? "dark" : "light";
  }}
  try {{
    applyDisplayTheme(localStorage.getItem("knowledgeBrowser:displayTheme"));
  }} catch (_) {{
    applyDisplayTheme("light");
  }}
  window.addEventListener("message", (event) => {{
    if (event.origin !== window.location.origin) return;
    if (!event.data || event.data.type !== "knowledgeBrowser:displayTheme") return;
    applyDisplayTheme(event.data.theme);
  }});
}})();
</script>
<link rel="stylesheet" href="/static/browser-assets/water.min.css">
<link rel="stylesheet" href="/static/browser-assets/katex.min.css">
<script defer src="/static/browser-assets/katex.min.js"></script>
<script defer src="/static/browser-assets/mermaid.min.js"></script>
<style>
  :root,
  html[data-display-theme="light"] {{
    color-scheme: light;
    --background-body: #ffffff;
    --background: #ffffff;
    --background-alt: #f1f5f9;
    --selection: #dbeafe;
    --text-main: #1f2937;
    --text-bright: #111827;
    --text-muted: #64748b;
    --links: #2563eb;
    --focus: #2563eb;
    --border: #d8dee8;
    --code: #eef2f7;
    --code-text: #1e3a5f;
    --code-border: #cbd5e1;
    --article-code-bg: #f8f8f8;
  }}
  html[data-display-theme="dark"] {{
    color-scheme: dark;
    --background-body: #101114;
    --background: #181a20;
    --background-alt: #252a33;
    --selection: rgba(122, 183, 255, 0.26);
    --text-main: #e6e8ee;
    --text-bright: #f5f7fb;
    --text-muted: #9aa4b2;
    --links: #7ab7ff;
    --focus: #7ab7ff;
    --border: #343a46;
    --code: #202a36;
    --code-text: #d7ebff;
    --code-border: #44556a;
    --article-code-bg: #20242c;
  }}
  body {{ max-width: 75ch; margin: 0 auto; padding: 1rem 2rem; }}
  .highlight {{ background: var(--article-code-bg); border-radius: 4px; overflow-x: auto; }}
  pre {{ line-height: 1.4; }}
  :not(pre) > code {{
    padding: 0.12em 0.36em;
    border: 1px solid var(--code-border);
    border-radius: 4px;
    background: var(--code);
    color: var(--code-text);
    font-size: 0.92em;
  }}
  pre code {{
    background: transparent;
    color: inherit;
  }}
  .toc {{ background: var(--background-alt,#f5f5f5); padding: 1em; border-radius: 6px; margin-bottom: 2em; }}
  .toc ul {{ list-style: disc; padding-left: 1.5em; margin: 0.25em 0; }}
  .toc h2 {{ margin: 0 0 0.5em 0; font-size: 1em; }}
  a[href^="#"] {{ text-decoration: none; }}
  .frontmatter {{ font-size: 0.85em; color: var(--text-muted,#666); background: var(--background-alt,#f5f5f5);
                  padding: 0.5em 1em; border-radius: 6px; margin-bottom: 1.5em; }}
  .frontmatter > dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 0.25em 1em; margin: 0; }}
  .frontmatter dt {{ font-weight: bold; }}
  .frontmatter dd {{ margin: 0; }}
  .frontmatter ul {{ margin: 0; padding-left: 1.5em; }}
  .task-list-item {{ list-style: none; }}
  .task-list-item-checkbox {{ margin-right: 0.5em; }}
  .mermaid-render svg {{ max-width: 100%; height: auto; }}
  .mermaid-error {{ border-left: 4px solid #c62828; padding-left: 1em; }}
  .render-error {{ color: #c62828; font-weight: bold; }}
  {_pygments_css}
  {_pygments_dark_css}
</style>
</head>
<body>
{back_link}
{toc_html}
<article>
{body_html}
</article>
<script>
document.addEventListener("DOMContentLoaded", async () => {{
  document.querySelectorAll(".math.inline, .math.block").forEach((node) => {{
    if (!window.katex) return;
    const source = node.textContent;
    try {{
      window.katex.render(source, node, {{
        displayMode: node.classList.contains("block"),
        throwOnError: false,
      }});
    }} catch (error) {{
      node.classList.add("render-error");
      node.title = String(error);
    }}
  }});

  if (!window.mermaid) return;
  window.mermaid.initialize({{startOnLoad: false}});
  const blocks = document.querySelectorAll(".mermaid-block");
  for (const [index, block] of blocks.entries()) {{
    const sourceNode = block.querySelector(".mermaid-source");
    const outputNode = block.querySelector(".mermaid-render");
    const source = sourceNode.textContent;
    try {{
      const result = await window.mermaid.render(`knowledge-mermaid-${{index}}`, source);
      outputNode.innerHTML = result.svg;
      if (result.bindFunctions) result.bindFunctions(outputNode);
      sourceNode.hidden = true;
    }} catch (error) {{
      block.classList.add("mermaid-error");
      outputNode.textContent = `Mermaid rendering failed: ${{String(error)}}`;
      outputNode.classList.add("render-error");
      sourceNode.hidden = false;
    }}
  }}
}});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 実行可能記事: 凍結ビュー表示 + ライブ起動
# ---------------------------------------------------------------------------

LIVE_MARIMO_PROCESSES: dict[str, dict] = {}
LIVE_MARIMO_LOCK = asyncio.Lock()


def frozen_view_path(file_path: Path) -> Path:
    return file_path.parent / "__marimo__" / f"{file_path.stem}.html"


def render_missing_frozen_view(file_path: Path, *, embed: bool = False) -> str:
    title = html.escape(_read_article_title(file_path))
    path = html.escape(str(file_path))
    back_link = "" if embed else '<p><a href="/">&larr; 検索に戻る</a></p>'
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script>
(() => {{
  function applyDisplayTheme(theme) {{
    document.documentElement.dataset.displayTheme = theme === "dark" ? "dark" : "light";
  }}
  try {{
    applyDisplayTheme(localStorage.getItem("knowledgeBrowser:displayTheme"));
  }} catch (_) {{
    applyDisplayTheme("light");
  }}
  window.addEventListener("message", (event) => {{
    if (event.origin !== window.location.origin) return;
    if (!event.data || event.data.type !== "knowledgeBrowser:displayTheme") return;
    applyDisplayTheme(event.data.theme);
  }});
}})();
</script>
<link rel="stylesheet" href="/static/browser-assets/water.min.css">
<style>
  :root,
  html[data-display-theme="light"] {{
    color-scheme: light;
    --background-body: #ffffff;
    --background: #ffffff;
    --background-alt: #f8fafc;
    --text-main: #1f2937;
    --text-muted: #64748b;
    --links: #2563eb;
    --border: #d8dee8;
  }}
  html[data-display-theme="dark"] {{
    color-scheme: dark;
    --background-body: #101114;
    --background: #181a20;
    --background-alt: #252a33;
    --text-main: #e6e8ee;
    --text-muted: #9aa4b2;
    --links: #7ab7ff;
    --border: #343a46;
  }}
  body {{ max-width: 75ch; margin: 0 auto; padding: 1rem 2rem; }}
  .notice {{ border: 1px solid var(--border); border-radius: 6px; padding: 1rem; background: var(--background-alt); }}
  .path {{ color: var(--text-muted); overflow-wrap: anywhere; }}
</style>
</head>
<body>
{back_link}
<main class="notice">
  <h1>{title}</h1>
  <p>凍結ビュー未生成</p>
  <p class="path">{path}</p>
</main>
</body>
</html>"""


def _is_process_running(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def _reserve_live_port(host: str) -> int:
    used_ports = {
        entry["port"]
        for entry in LIVE_MARIMO_PROCESSES.values()
        if _is_process_running(entry["process"])
    }
    for port in range(LIVE_MARIMO_PORT_START, LIVE_MARIMO_PORT_END + 1):
        if port in used_ports:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError("No live marimo ports available")


def _launch_live_marimo(file_path: Path) -> dict:
    key = str(file_path.resolve())
    existing = LIVE_MARIMO_PROCESSES.get(key)
    if existing and _is_process_running(existing["process"]):
        existing["last_used"] = time.time()
        return {"url": existing["url"], "reused": True, "pid": existing["process"].pid}

    port = _reserve_live_port(LIVE_MARIMO_HOST)
    cmd = [
        sys.executable,
        "-m",
        "marimo",
        "run",
        "--sandbox",
        "--headless",
        "--no-token",
        "--host",
        LIVE_MARIMO_HOST,
        "-p",
        str(port),
        str(file_path),
    ]
    data_dir = Path(os.environ.get("KNOWLEDGE_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / f"marimo-{port}.log"
    log_file = log_path.open("ab")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    url = f"http://{LIVE_MARIMO_HOST}:{port}"
    LIVE_MARIMO_PROCESSES[key] = {
        "process": proc,
        "port": port,
        "url": url,
        "last_used": time.time(),
        "log": str(log_path),
    }
    return {"url": url, "reused": False, "pid": proc.pid}


async def _cleanup_live_marimo() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        async with LIVE_MARIMO_LOCK:
            for key, entry in list(LIVE_MARIMO_PROCESSES.items()):
                proc = entry["process"]
                expired = now - entry["last_used"] > LIVE_MARIMO_IDLE_SECONDS
                if not _is_process_running(proc):
                    terminate_process_group(proc, grace_period=0.5)
                    LIVE_MARIMO_PROCESSES.pop(key, None)
                    continue
                if not expired:
                    continue
                terminate_process_group(proc, grace_period=2.0)
                LIVE_MARIMO_PROCESSES.pop(key, None)


# ---------------------------------------------------------------------------
# ライフサイクル: 起動時インデックス + mtime監視
# ---------------------------------------------------------------------------

WATCH_INTERVAL = float(os.environ.get("KNOWLEDGE_WATCH_INTERVAL", "60"))
INDEX_TASK: asyncio.Task | None = None
INDEX_STATUS = {"status": "idle", "error": None}


def corpus_fingerprint() -> str:
    return index_corpus_fingerprint(CATALOG_PATH)


async def _watch_and_reindex() -> None:
    """mtime監視ループ。初回は無条件でフルインデックス(起動時最新化を兼ねる)。"""
    global PACKAGE_ROOTS
    last: str | None = None
    while True:
        try:
            fp = await asyncio.to_thread(corpus_fingerprint)
            if fp != last:
                if last is not None:
                    print("corpus changed — reindexing...")
                PACKAGE_ROOTS = parse_catalog_roots(CATALOG_PATH)
                await _run_indexer()
                last = fp
        except Exception as e:
            print(f"watch loop error: {e}")
        await asyncio.sleep(WATCH_INTERVAL)


@app.on_event("startup")
async def startup_event() -> None:
    global PACKAGE_ROOTS
    PACKAGE_ROOTS = parse_catalog_roots(CATALOG_PATH)
    print(f"Package roots: {[str(r) for r in PACKAGE_ROOTS]}")
    if os.environ.get("KNOWLEDGE_AUTO_INDEX") == "1":
        if not MEILI_URL:
            raise ValueError("KNOWLEDGE_AUTO_INDEX requires MEILI_URL")
        asyncio.create_task(_watch_and_reindex())
    asyncio.create_task(_cleanup_live_marimo())


@app.on_event("shutdown")
def shutdown_event() -> None:
    for session in list(LIVE_MARIMO_PROCESSES.values()):
        proc = session.get("process")
        if proc:
            terminate_process_group(proc, grace_period=1.0)
    LIVE_MARIMO_PROCESSES.clear()


async def _index_job() -> bool:
    try:
        await asyncio.to_thread(run_indexer, CATALOG_PATH, MEILI_URL)
    except Exception as exc:
        INDEX_STATUS.update(status="failed", error=str(exc))
        return False
    INDEX_STATUS.update(status="succeeded", error=None)
    return True


def _start_indexer() -> tuple[asyncio.Task, bool]:
    global INDEX_TASK
    if INDEX_TASK is not None and not INDEX_TASK.done():
        return INDEX_TASK, False
    INDEX_STATUS.update(status="running", error=None)
    INDEX_TASK = asyncio.create_task(_index_job())
    return INDEX_TASK, True


async def _run_indexer() -> None:
    task, _ = _start_indexer()
    if not await asyncio.shield(task):
        raise RuntimeError(INDEX_STATUS["error"])


# ---------------------------------------------------------------------------
# API エンドポイント
# ---------------------------------------------------------------------------

@app.get("/api/tree")
async def tree():
    """CATALOG.md 由来パッケージ配下の記事ツリーを返す。"""
    packages = []
    catalog_packages = parse_catalog_packages(CATALOG_PATH)
    for pkg in catalog_packages:
        root: Path = pkg["root"]
        entry_path: Path = pkg["entry_path"]
        entry_file = str(entry_path) if entry_path != root and entry_path.is_file() else ""
        nested_package_roots = [
            other["root"] for other in catalog_packages
            if other["root"] != root and _is_relative_to(other["root"], root)
        ]
        packages.append(
            {
                "name": pkg["name"],
                "description": pkg["description"],
                "root": str(root),
                "entryFile": entry_file,
                "indexFile": entry_file,  # Cached clients still use this entrance field.
                "children": build_markdown_tree(
                    root,
                    exclude_roots=nested_package_roots,
                    include_py=True,
                ),
            }
        )
    return {"packages": packages}


@app.get("/api/drafts")
async def drafts():
    """このストア直下の drafts/ にある判定待ち記事ツリーを返す。"""
    children = []
    if DRAFTS_ROOT.exists() and DRAFTS_ROOT.is_dir():
        children = build_markdown_tree(
            DRAFTS_ROOT,
            include_py=True,
            require_markdown_dir_for_py=False,
        )
    return {
        "name": "drafts",
        "root": str(DRAFTS_ROOT),
        "children": children,
    }


@app.get("/api/suggest")
async def suggest(q: str = Query("", min_length=0)):
    """entriesへ語彙検索。キー入力ごとに叩かれる。"""
    if not q or meili_client is None:
        return {"results": []}
    try:
        idx = meili_client.index("entries")
        result = idx.search(
            q,
            {
                "limit": 10,
                "attributesToHighlight": ["text"],
                "highlightPreTag": "<mark>",
                "highlightPostTag": "</mark>",
            },
        )
        hits = []
        scopes = [
            (pkg, PublicationScope(pkg["root"], boundary=pkg.get("publication_root")))
            for pkg in sorted(
                parse_catalog_packages(CATALOG_PATH),
                key=lambda pkg: len(pkg["root"].parts), reverse=True,
            )
        ]
        for h in result["hits"]:
            path = Path(h.get("file", "")).resolve()
            owner = next(
                ((pkg, scope) for pkg, scope in scopes if path.is_relative_to(pkg["root"])),
                None,
            )
            if owner is None:
                continue
            pkg, scope = owner
            if not scope.allows(path) or not scope.allows_line(path, h.get("text", "")):
                continue
            current = current_entry(path, h)
            if current is None:
                continue
            line, text = current
            hits.append(
                {
                    "text": text,
                    "highlighted": _highlight_search_text(text, q),
                    "kind": h.get("kind", ""),
                    "package": pkg["name"],
                    "file": h.get("file", ""),
                    "rel": str(path.relative_to(pkg["root"])),
                    "line": line,
                    "claim_source": {"path": str(path), "line": line} if h.get("kind") == "article-claim" else None,
                }
            )
        return {"results": hits}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1), deep: bool = True):
    """共有検索コアの新契約に、人間向け原文 snippet だけを投影する。"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="query must be a non-empty string")
    try:
        result = federation_core_search(
            q,
            deep=deep,
            catalog_path=CATALOG_PATH,
            meili_url=MEILI_URL,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return _format_search_response_for_ui(result, query=q)


@app.get("/api/grep")
async def grep(q: str = Query(..., min_length=1)):
    """明示した文字列の所在だけを返す。本文snippetは追加しない。"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="query must be a non-empty string")
    try:
        return federation_core_grep(q, catalog_path=CATALOG_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _format_search_response_for_ui(result: dict, *, query: str = "") -> dict:
    """Preserve the core contract and add snippets only to article locators."""
    response = dict(result)
    response["article_results"] = [
        {
            **article_result,
            "snippet": _project_article_snippet(article_result, query or result.get("query", "")),
        }
        for article_result in result.get("article_results", [])
    ]
    return response


def _project_article_snippet(article_result: dict, query: str) -> str:
    """Read a short original-source excerpt for the human search UI."""
    raw_path = article_result.get("path", "")
    if not isinstance(raw_path, str) or not raw_path or not is_safe_path(raw_path):
        return ""
    path = Path(raw_path)
    try:
        content = read_article(path).body
    except OSError:
        return ""

    section = str(article_result.get("section") or "")
    lines = content.splitlines()
    if section:
        expected = f"## {section}".casefold()
        start = next(
            (index for index, line in enumerate(lines) if line.strip().casefold() == expected),
            0,
        )
    else:
        start = 0
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].lstrip().startswith("## ")
        ),
        len(lines),
    )
    excerpt = " ".join(line.strip() for line in lines[start:end] if line.strip())
    excerpt = re.sub(r"\s+", " ", excerpt)[:240]
    return _highlight_search_text(excerpt, query)


def _highlight_search_text(text: str, query: str) -> str:
    if not query:
        return html.escape(text)
    parts = re.split(f"({re.escape(query)})", text, flags=re.IGNORECASE)
    return "".join(
        f"<mark>{html.escape(part)}</mark>" if index % 2 else html.escape(part)
        for index, part in enumerate(parts)
    )


@app.get("/article")
async def article(file: str = Query(...), embed: bool = Query(False)):
    """記事ファイルをレンダリング。Markdownは変換、実行可能記事は凍結ビューを返す。"""
    if not is_safe_path(file):
        raise HTTPException(status_code=404, detail="File not found or outside package roots")
    file_path = Path(file)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = file_path.suffix.lower()
    if suffix not in {".md", ".py"}:
        raise HTTPException(status_code=400, detail="Only .md and .py article files are supported")
    try:
        if suffix == ".py":
            frozen = frozen_view_path(file_path)
            if frozen.exists() and frozen.is_file() and is_safe_path(str(frozen)):
                return FileResponse(str(frozen), media_type="text/html")
            return HTMLResponse(content=render_missing_frozen_view(file_path, embed=embed))
        return HTMLResponse(content=render_markdown(file_path, embed=embed))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/live-marimo")
async def live_marimo(file: str = Query(...)):
    """実行可能記事をmarimo runでライブ起動し、既起動なら再利用する。"""
    if not is_safe_path(file):
        raise HTTPException(status_code=404, detail="File not found or outside package roots")
    file_path = Path(file).resolve()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if file_path.suffix.lower() != ".py":
        raise HTTPException(status_code=400, detail="Only executable article .py files can be launched")
    if os.environ.get("KNOWLEDGE_ENABLE_LIVE_MARIMO") != "1":
        raise HTTPException(status_code=403, detail="Live execution is disabled; set KNOWLEDGE_ENABLE_LIVE_MARIMO=1 for trusted notebooks")
    async with LIVE_MARIMO_LOCK:
        try:
            return await asyncio.to_thread(_launch_live_marimo, file_path)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))


ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}


@app.get("/asset")
async def asset(file: str = Query(...)):
    """記事に同梱された画像等を配信。パッケージ境界外・非対応拡張子は404。"""
    if not is_safe_path(file):
        raise HTTPException(status_code=404, detail="File not found or outside package roots")
    file_path = Path(file)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if file_path.suffix.lower() not in ASSET_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Unsupported asset type")
    return FileResponse(str(file_path))


@app.post("/api/reindex")
async def reindex():
    """フルインデックスを非同期実行。"""
    if not MEILI_URL:
        raise HTTPException(status_code=503, detail="MEILI_URL is not configured")
    _, started = _start_indexer()
    return {"status": "reindexing started" if started else "already running"}


@app.get("/api/reindex")
async def reindex_status():
    return dict(INDEX_STATUS)


# ---------------------------------------------------------------------------
# フロントエンド
# ---------------------------------------------------------------------------

@app.get("/")
async def index_page():
    return FileResponse(STATIC_DIR / "index.html")


# static ファイル (CSS等があれば)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
