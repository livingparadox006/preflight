"""Local text ingestion.

No uploads to a cloud. Files are read in the browser and sent to the local
server as text, or pasted directly. This module only normalises and segments
text, preserving line numbers for citations.
"""

from __future__ import annotations

import hashlib
import re

MAX_DOCUMENT = 80_000
MAX_COMBINED = 160_000

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".py", ".csv", ".json"}


class IngestError(ValueError):
    pass


def normalize_text(text: str, limit: int = MAX_DOCUMENT) -> str:
    if not isinstance(text, str):
        raise IngestError("Input must be text.")
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + "\n[truncated locally]"
    return text


def validate_pair(brief: str, draft: str = "") -> None:
    if not brief.strip():
        raise IngestError("Add an assignment brief first.")
    if len(brief) + len(draft) > MAX_COMBINED:
        raise IngestError("Brief and draft are too large for one audit.")


def source_label(name: str, fallback: str = "pasted") -> str:
    name = (name or "").strip()
    if not name:
        return fallback
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:80] or fallback


def content_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[a-zA-Z][.)])\s+")


def split_sentences(text: str, source: str = "pasted") -> list[dict]:
    """Split into citable sentences while preserving line and paragraph numbers.

    Each item: {text, quote, line, paragraph, source_ref}
    """
    text = normalize_text(text)
    items: list[dict] = []
    paragraph = 1
    in_paragraph = False
    for line_no, raw in enumerate(text.split("\n"), start=1):
        raw = raw.strip()
        if not raw:
            if in_paragraph:
                paragraph += 1
                in_paragraph = False
            continue
        in_paragraph = True
        if raw.startswith("#"):
            raw = raw.lstrip("#").strip()
        body = _BULLET.sub("", raw).strip()
        if not body:
            continue
        pieces = [p.strip() for p in _SENTENCE_END.split(body) if p.strip()]
        if len(pieces) == 1:
            pieces = [body]
        for piece in pieces:
            if len(piece) < 3:
                continue
            items.append(
                {
                    "text": piece,
                    "quote": piece,
                    "line": line_no,
                    "paragraph": paragraph,
                    "source_ref": f"{source}:L{line_no}",
                }
            )
    return items


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", normalize_text(text)) if p.strip()]


def clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
