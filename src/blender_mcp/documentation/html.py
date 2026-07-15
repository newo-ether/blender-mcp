"""Bounded retrieval and extraction for official Blender documentation."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from .constants import DEFAULT_PAGE_MAX_CHARS, MAX_PAGE_MAX_CHARS
from .http import BlenderDocumentationRetrievalError


class _DocumentationHTMLParser(HTMLParser):
    _SKIP_TAGS = frozenset(
        {"script", "style", "nav", "header", "footer", "svg", "form"}
    )
    _SKIP_CLASSES = frozenset({"anchor", "header-anchor", "headerlink"})
    _VOID_TAGS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _BLOCK_TAGS = frozenset(
        {
            "address",
            "blockquote",
            "dd",
            "div",
            "dl",
            "dt",
            "figcaption",
            "figure",
            "li",
            "p",
            "pre",
            "table",
            "td",
            "th",
            "tr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.body_blocks: list[dict[str, Any]] = []
        self.main_blocks: list[dict[str, Any]] = []
        self._skip_depth = 0
        self._main_depth = 0
        self._in_title = False
        self._current_kind: str | None = None
        self._current_level: int | None = None
        self._current_parts: list[str] = []

    @staticmethod
    def _is_main(attrs: list[tuple[str, str | None]]) -> bool:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        return (
            values.get("role") == "main"
            or "document" in classes
            or "body" in classes
            or "page-content" in classes
        )

    def _flush(self) -> None:
        if self._current_kind is None:
            return
        text = " ".join("".join(self._current_parts).split())
        if text:
            block = {"kind": self._current_kind, "text": text}
            if self._current_level is not None:
                block["level"] = self._current_level
            self.body_blocks.append(block)
            if self._main_depth > 0:
                self.main_blocks.append(dict(block))
        self._current_kind = None
        self._current_level = None
        self._current_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag not in self._VOID_TAGS:
                self._skip_depth += 1
            return
        classes = set((dict(attrs).get("class") or "").split())
        if tag in self._SKIP_TAGS or classes.intersection(self._SKIP_CLASSES):
            self._flush()
            self._skip_depth = 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"main", "article"} or self._is_main(attrs):
            self._main_depth += 1
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._current_kind = "heading"
            self._current_level = int(tag[1])
        elif tag in self._BLOCK_TAGS:
            self._flush()
            self._current_kind = "text"
        elif tag == "br" and self._current_kind is not None:
            self._current_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if re.fullmatch(r"h[1-6]", tag) or tag in self._BLOCK_TAGS:
            self._flush()
        if tag in {"main", "article"}:
            self._main_depth = max(0, self._main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._current_kind is not None:
            self._current_parts.append(data)

    def close(self) -> None:
        self._flush()
        super().close()


def extract_html_page(
    content: bytes,
    *,
    heading: str | None = None,
    max_chars: int = DEFAULT_PAGE_MAX_CHARS,
) -> dict[str, Any]:
    """Extract readable blocks and an optional heading-bounded section."""

    if not isinstance(max_chars, int) or not 100 <= max_chars <= MAX_PAGE_MAX_CHARS:
        raise BlenderDocumentationRetrievalError(
            "invalid_max_chars",
            f"max_chars must be an integer from 100 to {MAX_PAGE_MAX_CHARS}",
        )
    parser = _DocumentationHTMLParser()
    try:
        parser.feed(content.decode("utf-8", errors="replace"))
        parser.close()
    except Exception as exc:
        raise BlenderDocumentationRetrievalError(
            "malformed_html",
            "Unable to parse official documentation HTML",
        ) from exc

    blocks = parser.main_blocks or parser.body_blocks
    headings = [
        {"level": block["level"], "text": block["text"]}
        for block in blocks
        if block["kind"] == "heading"
    ]
    selected_heading = None
    if heading:
        wanted = " ".join(str(heading).split()).casefold()
        start = None
        start_level = None
        for index, block in enumerate(blocks):
            if block["kind"] == "heading" and block["text"].casefold() == wanted:
                start = index
                start_level = block["level"]
                selected_heading = block["text"]
                break
        if start is None:
            raise BlenderDocumentationRetrievalError(
                "heading_not_found",
                f"Heading not found: {heading}",
            )
        end = len(blocks)
        for index in range(start + 1, len(blocks)):
            block = blocks[index]
            if block["kind"] == "heading" and block["level"] <= start_level:
                end = index
                break
        blocks = blocks[start:end]

    lines = [block["text"] for block in blocks if block["text"]]
    text = "\n\n".join(lines).strip()
    if not text:
        raise BlenderDocumentationRetrievalError(
            "empty_page",
            "Official documentation page contained no readable content",
        )
    truncated = len(text) > max_chars
    if truncated:
        text = text[: max_chars - 1].rstrip() + "…"
    title = " ".join("".join(parser.title_parts).split())
    if not title and headings:
        title = headings[0]["text"]
    return {
        "title": title,
        "heading": selected_heading,
        "content": text,
        "characters": len(text),
        "truncated": truncated,
        "headings": headings[:100],
    }
