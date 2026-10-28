"""Lossless Markdown comment records and serialized atomic mutations (POSIX)."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

# Content is indented, so user text cannot introduce record boundaries.
RECORD = re.compile(r'^<!-- comment:([a-f0-9]{32}) -->\n(.*?)^<!-- /comment:\1 -->\n?', re.M | re.S)
FIELDS = ("id", "created", "status", "heading", "anchor", "quote", "body")


def decode(match):
    try:
        metadata, content = match[2].split("\n\n### Quote\n", 1)
        quote, body = content.split("\n\n### Comment\n", 1)
        item = json.loads(metadata)
        if (item["id"] != match[1] or item["status"] not in {"open", "resolved"}
                or not all(isinstance(item[key], str) for key in FIELDS[:5])):
            return None
        quote_lines = quote.split("\n")
        body_lines = body.removesuffix("\n").split("\n")
        if not all(line.startswith("> ") for line in quote_lines):
            return None
        if not all(line.startswith("    ") for line in body_lines):
            return None
        return {**item, "quote": "\n".join(line[2:] for line in quote_lines),
                "body": "\n".join(line[4:] for line in body_lines)}
    except (ValueError, KeyError, TypeError):
        return None


def parse(text):
    return [item for match in RECORD.finditer(text) if (item := decode(match)) is not None]


def encode(item):
    metadata = json.dumps({key: item[key] for key in FIELDS[:5]}, ensure_ascii=False)
    quote = "\n".join("> " + line for line in item["quote"].split("\n"))
    body = "\n".join("    " + line for line in item["body"].split("\n"))
    return (f'<!-- comment:{item["id"]} -->\n{metadata}\n\n### Quote\n{quote}'
            f'\n\n### Comment\n{body}\n<!-- /comment:{item["id"]} -->\n')


@contextmanager
def locked(target):
    target.parent.mkdir(exist_ok=True)
    # Lock the directory inode: no extra lock file and stable across os.replace.
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def mutate(target: Path, *, new=None, comment_id=None, status=None):
    with locked(target):
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("Comment paths must not be symlinks")
        text = target.read_text(encoding="utf-8") if target.exists() else "# Article comments\n\n"
        if new is not None:
            item = {**new, "id": uuid4().hex, "created": datetime.now(timezone.utc).isoformat(), "status": "open"}
            text += ("\n" if text and not text.endswith("\n") else "") + encode(item)
        else:
            matches = [m for m in RECORD.finditer(text) if m[1] == comment_id and decode(m) is not None]
            if len(matches) != 1:
                raise KeyError("Comment not found or ID is ambiguous")
            match = matches[0]
            item = decode(match)
            replacement = ""
            if status is not None:
                item["status"] = status
                replacement = encode(item)
            text = text[:match.start()] + replacement + text[match.end():]
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
                temp = stream.name
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        return parse(text)
