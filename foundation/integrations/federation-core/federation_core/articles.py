"""Static article prose and YAML claims, shared by every reader.

No article Python is imported or executed. Source locations refer to the real
file, including when a Python string encodes several Markdown lines on one line.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import io
from pathlib import Path
import re
import textwrap
import tokenize

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


@dataclass(frozen=True)
class Claim:
    text: str
    line: int
    index: int


@dataclass
class Article:
    markdown: str
    body: str
    body_lines: list[tuple[int, str]]
    metadata: dict
    claims: list[Claim]
    error: str | None = None


def markdown_nodes(source: str) -> list[ast.Constant]:
    """Static first arguments to mo.md, in source order."""
    tree = ast.parse(source)
    return sorted([
        node.args[0] for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "mo" and node.func.attr == "md"
        and node.args and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ], key=lambda node: (node.lineno, node.col_offset))


def node_span(source: str, node: ast.AST) -> tuple[int, int]:
    """AST columns are UTF-8 bytes, not Python character offsets."""
    lines = source.encode("utf-8").splitlines(keepends=True)
    return (sum(map(len, lines[:node.lineno - 1])) + node.col_offset,
            sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset)


def _literal_lines(source: str, node: ast.Constant) -> list[int]:
    segment = ast.get_source_segment(source, node)
    decoded = ""
    positions = []
    for token in tokenize.generate_tokens(io.StringIO(segment).readline):
        if token.type != tokenize.STRING:
            continue
        match = re.match(r"(?i)([ru]*)(\"\"\"|'''|\"|')", token.string)
        prefix, quote = match.groups()
        raw = token.string[match.end():-len(quote)]
        line = node.lineno + token.start[0] - 1
        i = 0
        while i < len(raw):
            end = i + 1
            value = raw[i]
            if value == "\\" and 'r' not in prefix.lower():
                escape = re.match(r"\\(?:\r?\n|N\{[^}]+\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|.)", raw[i:], re.DOTALL)
                if escape:
                    end = i + len(escape[0])
                    value = ast.literal_eval('"' + escape[0] + '"')
            decoded += value
            positions.extend([line] * len(value))
            line += raw[i:end].count('\n')
            i = end
    if decoded != node.value:
        raise ValueError("Cannot map static Markdown literal to its source")
    starts = [0] + [i + 1 for i, c in enumerate(decoded) if c == '\n']
    return [positions[min(i, len(positions) - 1)] if positions else node.lineno for i in starts]


def extract_marimo_markdown_cells(source: str) -> list[str]:
    try:
        return [textwrap.dedent(node.value) for node in markdown_nodes(source)]
    except (SyntaxError, ValueError):
        return []


def extract_marimo_markdown(path: Path) -> str:
    return "\n\n".join(extract_marimo_markdown_cells(path.read_text(encoding="utf-8")))


def frontmatter_span(text: str) -> tuple[int, int] | None:
    """Return the YAML character span; allow the leading blank of mo.md."""
    opening = re.match(r"\A[ \t\r\n]*---[ \t]*\r?\n", text)
    if not opening:
        return None
    closing = re.search(r"(?m)^---[ \t]*(?:\r?\n|$)", text[opening.end():])
    if not closing:
        raise ValueError("Unclosed article frontmatter")
    return opening.end(), opening.end() + closing.start()


def frontmatter_end(text: str, span: tuple[int, int]) -> int:
    end = text.find('\n', span[1])
    return len(text) if end < 0 else end + 1


def _metadata(text: str):
    span = frontmatter_span(text)
    if span is None:
        return {}, None, None
    raw = text[span[0]:span[1]]
    if any(isinstance(token, yaml.AliasToken) for token in yaml.scan(raw)):
        raise ValueError("Article frontmatter aliases are not supported")
    node = yaml.compose(raw, Loader=yaml.SafeLoader)
    if node is None:
        return {}, None, span
    if not isinstance(node, MappingNode):
        raise ValueError("Article frontmatter must be a mapping")
    if any(not isinstance(key, ScalarNode) or key.tag != 'tag:yaml.org,2002:str' for key, _ in node.value):
        raise ValueError("Article frontmatter requires string keys")
    keys = [key.value for key, _ in node.value]
    if len(set(keys)) != len(keys):
        raise ValueError("Article frontmatter requires unique string keys")
    claims_node = next((value for key, value in node.value if key.value == 'claims'), None)
    metadata = yaml.safe_load(raw)
    if claims_node is not None and (
        not isinstance(claims_node, SequenceNode)
        or not claims_node.value
        or any(not isinstance(item, ScalarNode) or item.tag != 'tag:yaml.org,2002:str' or not item.value.strip() for item in claims_node.value)
    ):
        raise ValueError("claims must be a non-empty array of non-empty strings")
    return metadata, claims_node, span


def parse_markdown_metadata(markdown: str) -> dict:
    try:
        return _metadata(markdown)[0]
    except (ValueError, yaml.YAMLError):
        return {}


def read_article(path: Path, *, source: str | None = None) -> Article:
    try:
        source = path.read_text(encoding="utf-8") if source is None else source
        if path.suffix.lower() == '.py':
            nodes = markdown_nodes(source)
            blocks = [(textwrap.dedent(n.value), _literal_lines(source, n)) for n in nodes]
        else:
            blocks = [(source, list(range(1, len(source.splitlines()) + 2)))]
    except (OSError, UnicodeError, SyntaxError, ValueError, tokenize.TokenError) as exc:
        return Article('', '', [], {}, [], f"Cannot read static article: {exc}")
    if not blocks:
        return Article('', '', [], {}, [], "No static mo.md article prose")
    first, locations = blocks[0]
    metadata, claims, error = {}, [], None
    body_start = 0
    try:
        span = frontmatter_span(first)
        if span:
            body_start = frontmatter_end(first, span)
        elif first.lstrip().startswith('---'):
            raise ValueError('Invalid article frontmatter')
        metadata, claims_node, span = _metadata(first)
        if claims_node:
            offset = first[:span[0]].count('\n')
            claims = [Claim(item.value, locations[offset + item.start_mark.line], i)
                      for i, item in enumerate(claims_node.value)]
    except (ValueError, yaml.YAMLError) as exc:
        if not body_start and first.lstrip().startswith("---"):
            body_start = len(first)
        # A malformed claim is an authoring error, never a publication flag.
        error = exc.problem if isinstance(exc, yaml.YAMLError) else str(exc)
    body_blocks = [(first[body_start:], locations[first[:body_start].count('\n'):]), *blocks[1:]]
    body_lines = [(numbers[i], line) for text, numbers in body_blocks
                  for i, line in enumerate(text.splitlines())]
    return Article('\n\n'.join(t for t, _ in blocks), '\n\n'.join(t for t, _ in body_blocks),
                   body_lines, metadata, claims, error)


def prose_lines(lines):
    """Ignore fenced examples when resolving headings and authored links."""
    fence = ''
    for number, line in lines:
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ''
            continue
        if not fence:
            yield number, line


def slugify(text: str) -> str:
    return re.sub(r'[\s_]+', '-', re.sub(r'[^\w\s\-]', '', text.lower())).strip('-') or 'section'


def headings(path: Path) -> list[tuple[int, int, str]]:
    result = []
    for n, line in prose_lines(read_article(path).body_lines):
        match = re.match(r'^\s*(#{1,6})\s+(.+?)\s*#*\s*$', line)
        term = re.match(r'^\*\*([^*]+)\*\*:', line.strip())
        if match:
            result.append((n, len(match[1]), match[2]))
        elif term:
            result.append((n, 2, term[1]))
    return result


def anchors(path: Path) -> set[str]:
    article = read_article(path)
    names = set(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', article.body))
    counts = {}
    for _, line in prose_lines(article.body_lines):
        match = re.match(r'^\s*#{1,6}\s+(.+?)\s*#*\s*$', line)
        if match:
            slug = slugify(match[1])
            count = counts.get(slug, 0)
            names.add(f'{slug}-{count}' if count else slug)
            counts[slug] = count + 1
    return names
