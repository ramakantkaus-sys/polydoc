"""Structural inference: outlines and nested lists.

Readers produce a *flat* block sequence, because that is what the source formats
actually contain -- a DOCX has no notion of "section 2 contains section 2.1". These
functions recover the hierarchy that the flat sequence implies.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from ..model.blocks import (
    Block,
    CodeBlock,
    Container,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    Quote,
    Section,
    Table,
)
from ..model.inline import Text
from ..model.style import ListStyle
from .heuristics import ListMarker

__all__ = [
    "build_nested_list",
    "build_sections",
    "coalesce_code_blocks",
    "flatten_sections",
    "renumber_headings",
]


def build_sections(
    blocks: Sequence[Block],
    max_level: int = 6,
    copy_blocks: bool = False,
) -> List[Section]:
    """Turn a flat block list into a nested :class:`~polydoc.model.Section` tree.

    By default the returned sections *reference* the original blocks without
    reparenting them, so the outline is a cheap read-only view over the document. Pass
    ``copy_blocks=True`` for an independent tree you can safely mutate.

    Content appearing before the first heading is returned as a leading section with
    ``level=0`` and an empty title, so nothing is silently lost.

    >>> from polydoc.model import Heading, Paragraph
    >>> tree = build_sections([Heading.of("A", 1), Paragraph.of("x"), Heading.of("B", 2)])
    >>> tree[0].title_text, len(tree[0].subsections)
    ('A', 1)
    """
    root: List[Section] = []
    stack: List[Section] = []
    preamble: List[Block] = []

    def emit(target_list: List[Block], block: Block) -> None:
        target_list.append(block.copy() if copy_blocks else block)

    for block in blocks:
        if isinstance(block, Heading) and block.level <= max_level:
            title = [inline.copy() for inline in block.content]
            section = Section(title=title, level=block.level)
            while stack and stack[-1].level >= section.level:
                stack.pop()
            if stack:
                stack[-1].content.append(section)
            else:
                root.append(section)
            stack.append(section)
        elif stack:
            emit(stack[-1].content, block)
        else:
            emit(preamble, block)

    if preamble:
        root.insert(0, Section(title=[], content=preamble, level=0))
    return root


def flatten_sections(sections: Sequence[Section]) -> List[Block]:
    """Inverse of :func:`build_sections`: a section tree back to a flat block list."""
    out: List[Block] = []
    for section in sections:
        if section.title:
            out.append(Heading([inline.copy() for inline in section.title], section.level))
        for block in section.content:
            if isinstance(block, Section):
                out.extend(flatten_sections([block]))
            else:
                out.append(block)
    return out


def renumber_headings(blocks: Sequence[Block], start_level: int = 1) -> None:
    """Compress heading levels so they step by one, in place.

    Documents converted from PDFs often end up with levels like 1, 3, 4 because the
    inference maps font sizes to levels. Writers that build a real table of contents
    (DOCX, HTML) want a gap-free hierarchy.

    >>> from polydoc.model import Heading
    >>> hs = [Heading.of("a", 1), Heading.of("b", 3), Heading.of("c", 4)]
    >>> renumber_headings(hs); [h.level for h in hs]
    [1, 2, 3]
    """
    headings = [b for b in blocks if isinstance(b, Heading)]
    if not headings:
        return
    distinct = sorted({h.level for h in headings})
    mapping = {level: min(6, start_level + index) for index, level in enumerate(distinct)}
    for heading in headings:
        heading.level = mapping[heading.level]


def build_nested_list(markers: Sequence[ListMarker]) -> Optional[ListBlock]:
    """Assemble parsed list markers into a (possibly nested) list block.

    Nesting is derived from each marker's indent width, which is how plain text and
    PDF extraction express it.

    >>> from polydoc.intelligence.heuristics import parse_list_marker
    >>> lines = ["- top", "  - nested", "- second"]
    >>> lst = build_nested_list([parse_list_marker(l) for l in lines])
    >>> len(lst.items), len(lst.items[0].sublists[0].items)
    (2, 1)
    """
    cleaned = [m for m in markers if m is not None]
    if not cleaned:
        return None

    root = ListBlock(
        marker_style=cleaned[0].style,
        start=cleaned[0].number or 1,
    )
    stack: List[tuple] = [(cleaned[0].indent, root)]

    for marker in cleaned:
        # Close any levels deeper than this marker.
        while len(stack) > 1 and marker.indent < stack[-1][0]:
            stack.pop()
        indent, current = stack[-1]

        # A deeper indent opens a sublist hanging off the previous item.
        if marker.indent > indent and current.items:
            sub = ListBlock(
                marker_style=marker.style,
                start=marker.number or 1,
                level=len(stack),
            )
            parent_item = current.items[-1]
            parent_item.content.append(sub)
            parent_item.adopt(sub)
            stack.append((marker.indent, sub))
            current = sub

        # The first item decides the list's marker style.
        if not current.items and marker.style is not current.marker_style:
            current.marker_style = marker.style

        item = ListItem.of(marker.content, checked=marker.checked)
        if marker.style is not ListStyle.BULLET:
            item.marker = marker.marker
        current.items.append(item)
        current.adopt(item)

    return root


# ---------------------------------------------------------------------------
# Code block recovery
# ---------------------------------------------------------------------------


def _is_code_paragraph(block: Block) -> bool:
    """True for a paragraph set entirely in a monospaced face."""
    if not isinstance(block, Paragraph) or not block.content:
        return False
    runs = [node for node in block.content if isinstance(node, Text)]
    if not runs or len(runs) != len(block.content):
        return False
    return all(run.style.is_monospace for run in runs)


def _explicit_code_style(block: Paragraph) -> bool:
    name = str(
        block.attrs.get("docx_style") or block.style.style_name or ""
    ).lower().replace(" ", "")
    return name in ("code", "sourcecode", "htmlpreformatted", "preformattedtext")


def coalesce_code_blocks(blocks: Sequence[Block], min_run: int = 2) -> List[Block]:
    """Rebuild code blocks from runs of monospaced paragraphs.

    Neither Word nor PDF has a code block: a listing arrives as one monospaced
    paragraph (or one positioned line) per source line. Reading those back as prose
    loses the listing entirely, so consecutive monospaced paragraphs are merged.

    ``min_run`` consecutive paragraphs are required, or an explicit code style name.
    A single paragraph containing only an inline code span is genuinely a paragraph,
    and promoting it would be wrong.

    >>> from polydoc.model import Paragraph, Text, TextStyle
    >>> mono = TextStyle(font_family="Consolas")
    >>> out = coalesce_code_blocks([
    ...     Paragraph([Text("def f():", mono)]),
    ...     Paragraph([Text("    return 1", mono)]),
    ... ])
    >>> out[0].type, out[0].code
    ('code_block', 'def f():\\n    return 1')
    """
    out: List[Block] = []
    run: List[Paragraph] = []

    def flush() -> None:
        if not run:
            return
        if len(run) >= min_run or any(_explicit_code_style(p) for p in run):
            language = None
            for paragraph in run:
                language = paragraph.attrs.get("language") or language
            out.append(CodeBlock("\n".join(p.text for p in run), language))
        else:
            out.extend(run)
        run.clear()

    for block in blocks:
        if _is_code_paragraph(block):
            run.append(block)  # type: ignore[arg-type]
            continue
        flush()
        _coalesce_nested(block, min_run)
        out.append(block)
    flush()
    return out


def _coalesce_nested(block: Block, min_run: int = 2) -> None:
    """Recurse into container blocks, applying the same pass in place."""
    if isinstance(block, ListBlock):
        for item in block.items:
            item.content = coalesce_code_blocks(item.content, min_run)
            item.adopt(*item.content)
    elif isinstance(block, Table):
        for row in block.rows:
            for cell in row.cells:
                cell.content = coalesce_code_blocks(cell.content, min_run)
                cell.adopt(*cell.content)
    elif isinstance(block, (Quote, Container, Section)):
        block.content = coalesce_code_blocks(block.content, min_run)
        block.adopt(*block.content)
