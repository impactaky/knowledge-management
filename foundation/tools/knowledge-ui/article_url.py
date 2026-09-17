#!/usr/bin/env python3
"""Build a clickable knowledge-ui URL for an article or draft path."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote


DEFAULT_PORT = "7776"

def default_base_url() -> str:
    configured = os.environ.get("KNOWLEDGE_UI_BASE_URL")
    if configured:
        return configured.rstrip("/")

    host = os.environ.get("UI_HOST") or "127.0.0.1"
    port = os.environ.get("UI_PORT") or DEFAULT_PORT
    if host.startswith(("http://", "https://")):
        return f"{host.rstrip('/')}:{port}"
    return f"http://{host}:{port}"


def build_article_url(file_path: str | Path, *, base_url: str | None = None) -> str:
    absolute_path = Path(file_path).expanduser().resolve()
    base = (base_url or default_base_url()).rstrip("/")
    return f"{base}/article?file={quote(str(absolute_path), safe='')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Markdown or executable article path")
    parser.add_argument("--base-url", help="Override the knowledge-ui base URL")
    args = parser.parse_args()
    print(build_article_url(args.file, base_url=args.base_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
