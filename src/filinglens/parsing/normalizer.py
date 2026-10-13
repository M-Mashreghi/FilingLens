"""Filing HTML / inline-XBRL normalizer.

Produces an ordered list of visible content blocks (headings, paragraphs, list items, tables)
with stable ids and DOM locators. Hidden content (``display:none``, ``ix:header``/
``ix:hidden``) is never emitted as narrative; tagged hidden iXBRL facts are returned
separately. iXBRL wrappers around visible text (``ix:nonFraction``, ``ix:nonNumeric`` ...) are
unwrapped so visible text is preserved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lxml import etree
from lxml import html as lxml_html

NORMALIZER_VERSION = "normalizer/1"

_DROP_TAGS = {"script", "style", "noscript", "head", "title", "meta", "link", "template"}
_BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "table",
    "ul",
    "ol",
    "section",
    "article",
    "body",
    "center",
    "blockquote",
    "pre",
    "dl",
    "dt",
    "dd",
    "tr",
    "td",
    "th",
    "font_block",
}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_BOLD_STYLE = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)", re.IGNORECASE)
_WS = re.compile(r"\s+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_PAGE_ARTIFACT = re.compile(r"^(page\s+)?\d{1,3}$|^table of contents$", re.IGNORECASE)
_ITEM_HEADING = re.compile(r"^(part\s+[ivx]+\b|item\s+\d+[a-z]?\b)", re.IGNORECASE)


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"


@dataclass(frozen=True)
class Block:
    block_id: str
    index: int
    kind: BlockKind
    text: str
    original_text: str
    locator: str
    source_line: int | None = None
    heading_level: int | None = None
    rows: tuple[tuple[str, ...], ...] | None = None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.kind}|{self.text}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class HiddenFact:
    name: str
    context_ref: str | None
    unit_ref: str | None
    text: str
    locator: str


@dataclass(frozen=True)
class NormalizedDocument:
    blocks: tuple[Block, ...]
    hidden_facts: tuple[HiddenFact, ...]
    normalizer_version: str = NORMALIZER_VERSION
    snapshot_id: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    def block(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.block_id == block_id), None)


def normalize_ws(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ").translate(_ZERO_WIDTH)).strip()


def _local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    t = tag.lower()
    if "}" in t:
        t = t.split("}", 1)[1]
    return t


def _ix_name(tag: Any) -> str | None:
    """Return the ix element local name for ``ix:foo`` / ``{ns}foo`` tags, else None."""
    if not isinstance(tag, str):
        return None
    t = tag.lower()
    if t.startswith("ix:"):
        return t[3:]
    if t.startswith("{http://www.xbrl.org/2013/inlinexbrl}"):
        return t.split("}", 1)[1]
    return None


def _is_hidden(el: Any) -> bool:
    if _ix_name(el.tag) in ("header", "hidden"):
        return True
    style = el.get("style") or ""
    return bool(_HIDDEN_STYLE.search(style)) or el.get("hidden") is not None


def _visible_text(el: Any) -> str:
    """Concatenate visible text of ``el`` (excluding hidden descendants), keeping tails."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if _is_hidden(node) or _local(node.tag) in _DROP_TAGS:
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                walk(child)
                if _local(child.tag) == "br":
                    parts.append(" ")
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return "".join(parts)


def _has_block_descendant(el: Any) -> bool:
    for d in el.iterdescendants():
        if isinstance(d.tag, str) and _local(d.tag) in _BLOCK_TAGS and not _is_hidden(d):
            return True
    return False


def _looks_bold(el: Any) -> bool:
    if _BOLD_STYLE.search(el.get("style") or ""):
        return True
    children = [c for c in el if isinstance(c.tag, str)]
    if not children:
        return False
    text_outside = normalize_ws((el.text or "") + "".join(c.tail or "" for c in children))
    if text_outside:
        return False
    return all(
        _local(c.tag) in ("b", "strong")
        or _BOLD_STYLE.search(c.get("style") or "")
        or (_looks_bold(c) if len(c) else False)
        for c in children
        if normalize_ws(_visible_text(c))
    )


def _table_rows(table: Any) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for tr in table.iter():
        if not isinstance(tr.tag, str) or _local(tr.tag) != "tr" or _is_hidden(tr):
            continue
        cells = [
            normalize_ws(_visible_text(c))
            for c in tr
            if isinstance(c.tag, str) and _local(c.tag) in ("td", "th")
        ]
        merged: list[str] = []
        pending_prefix = ""
        for cell in cells:
            if not cell:
                continue
            if cell in ("$", "€", "£", "("):
                pending_prefix += cell
                continue
            if cell in (")", "%", ")%", "%)") and merged:
                merged[-1] += cell
                continue
            merged.append(pending_prefix + cell)
            pending_prefix = ""
        if pending_prefix:
            merged.append(pending_prefix)
        if merged:
            rows.append(tuple(merged))
    return tuple(rows)


class _Builder:
    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self.blocks: list[Block] = []

    def emit(
        self,
        kind: BlockKind,
        raw: str,
        locator: str,
        line: int | None,
        *,
        heading_level: int | None = None,
        rows: tuple[tuple[str, ...], ...] | None = None,
    ) -> None:
        text = normalize_ws(raw) if rows is None else " | ".join(" ".join(r) for r in rows)
        if not text:
            return
        if kind is not BlockKind.TABLE and _PAGE_ARTIFACT.match(text):
            return
        index = len(self.blocks)
        digest = hashlib.sha256(f"{kind}|{text}".encode()).hexdigest()[:8]
        self.blocks.append(
            Block(
                block_id=f"blk-{index:05d}-{digest}",
                index=index,
                kind=kind,
                text=text,
                original_text=raw,
                locator=locator,
                source_line=line,
                heading_level=heading_level,
                rows=rows,
            )
        )

    def classify(self, el: Any, text: str) -> tuple[BlockKind, int | None]:
        tag = _local(el.tag)
        if tag in _HEADINGS:
            return BlockKind.HEADING, _HEADINGS[tag]
        if tag == "li":
            return BlockKind.LIST_ITEM, None
        norm = normalize_ws(text)
        if (
            norm
            and len(norm) <= 200
            and not norm.endswith((".", ";", ","))
            and (_looks_bold(el) or (_ITEM_HEADING.match(norm) and len(norm) <= 120))
        ):
            return BlockKind.HEADING, None
        return BlockKind.PARAGRAPH, None

    def walk(self, el: Any) -> None:
        if not isinstance(el.tag, str) or _is_hidden(el) or _local(el.tag) in _DROP_TAGS:
            return
        tag = _local(el.tag)
        path = self.tree.getpath(el)
        if tag == "table":
            rows = _table_rows(el)
            self.emit(BlockKind.TABLE, _visible_text(el), path, el.sourceline, rows=rows)
            return
        if tag in _BLOCK_TAGS and not _has_block_descendant(el):
            text = _visible_text(el)
            kind, level = self.classify(el, text)
            self.emit(kind, text, path, el.sourceline, heading_level=level)
            return
        # Mixed content: flush inline runs between block children as paragraphs.
        run: list[str] = [el.text or ""]
        run_no = 0

        def flush() -> None:
            nonlocal run, run_no
            raw = "".join(run)
            if normalize_ws(raw):
                self.emit(BlockKind.PARAGRAPH, raw, f"{path}#run{run_no}", el.sourceline)
                run_no += 1
            run = []

        for child in el:
            if not isinstance(child.tag, str):
                run.append(child.tail or "")
                continue
            if _is_hidden(child) or _local(child.tag) in _DROP_TAGS:
                run.append(child.tail or "")
                continue
            if _local(child.tag) in _BLOCK_TAGS or _has_block_descendant(child):
                flush()
                self.walk(child)
                run.append(child.tail or "")
            else:
                run.append(_visible_text(child))
                run.append(child.tail or "")
        flush()


def _hidden_facts(tree: Any, root: Any) -> Iterator[HiddenFact]:
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = _ix_name(el.tag)
        if name not in ("nonfraction", "nonnumeric", "fraction"):
            continue
        hidden = any(_is_hidden(a) for a in el.iterancestors()) or _is_hidden(el)
        if not hidden:
            continue
        yield HiddenFact(
            name=el.get("name") or "",
            context_ref=el.get("contextref"),
            unit_ref=el.get("unitref"),
            text=normalize_ws(el.text_content()),
            locator=tree.getpath(el),
        )


def normalize_html(raw: bytes | str, *, snapshot_id: str | None = None) -> NormalizedDocument:
    raw_bytes = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    parser = lxml_html.HTMLParser(remove_comments=True, encoding=_sniff_encoding(raw_bytes))
    root = lxml_html.fromstring(raw_bytes, parser=parser)
    tree = etree.ElementTree(root)
    body = root.find(".//body")
    start = body if body is not None else root
    builder = _Builder(tree)
    builder.walk(start)
    facts = tuple(_hidden_facts(tree, root))
    warnings: list[str] = []
    if not builder.blocks:
        warnings.append("no visible content blocks extracted")
    return NormalizedDocument(
        blocks=tuple(builder.blocks),
        hidden_facts=facts,
        snapshot_id=snapshot_id,
        warnings=tuple(warnings),
    )


def _sniff_encoding(raw: bytes) -> str:
    head = raw[:2048].decode("ascii", "ignore").lower()
    m = re.search(r'charset=["\']?([a-z0-9_\-]+)', head)
    if m:
        return m.group(1)
    m = re.search(r'encoding=["\']([a-z0-9_\-]+)', head)
    if m:
        return m.group(1)
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "windows-1252"
