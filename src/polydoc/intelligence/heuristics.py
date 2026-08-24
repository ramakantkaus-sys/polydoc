"""Low-level text heuristics shared by several readers.

These are the small, testable judgements that turn shapeless text into structure:
"is this line a list item?", "is this line a heading?". They are deliberately
conservative -- a false positive rewrites the user's document incorrectly, which is
worse than leaving a paragraph as a paragraph.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from ..model.style import ListStyle

__all__ = [
    "ListMarker",
    "is_all_caps",
    "is_sentence_like",
    "looks_like_code",
    "looks_like_heading",
    "normalise_whitespace",
    "parse_list_marker",
    "roman_to_int",
]

#: Bullet glyphs seen in real documents, including the ones PDF extraction produces.
#: En and em dashes are deliberately excluded: they open attributions
#: ("\u2014 Dijkstra") and dialogue far more often than they open list items, and a
#: false positive rewrites the document incorrectly.
_BULLET_CHARS = "-*+\u2022\u2023\u2043\u25aa\u25cf\u25e6\u2219\u00b7\u25a0\u25cb\uf0b7"

_BULLET_RE = re.compile(rf"^(\s*)([{re.escape(_BULLET_CHARS)}])\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d{1,3})[.)\]]\s+(.*)$")
_ALPHA_RE = re.compile(r"^(\s*)([a-zA-Z])[.)\]]\s+(.*)$")
_ROMAN_RE = re.compile(r"^(\s*)((?=[ivxlcdmIVXLCDM]{1,7}[.)\]])[ivxlcdm]+|[IVXLCDM]+)[.)\]]\s+(.*)$")
_CHECKBOX_RE = re.compile(r"^\[([ xX])\]\s+(.*)$")

_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

#: A heading rarely ends with these.
_TERMINAL_PUNCT = ".,;:!?"

_CODE_HINTS = re.compile(
    r"(;\s*$|^\s*(def|class|import|from|function|const|let|var|public|private|#include)\s"
    r"|=>|::|\{\s*$|\}\s*$|^\s*</?\w+[^>]*>\s*$)"
)


class ListMarker(NamedTuple):
    """A parsed list marker.

    ``indent`` is the leading whitespace width, used to reconstruct nesting.
    """

    style: ListStyle
    marker: str
    content: str
    indent: int
    number: Optional[int] = None
    checked: Optional[bool] = None


def roman_to_int(text: str) -> Optional[int]:
    """Convert a Roman numeral to an int, or ``None`` if invalid.

    >>> roman_to_int("xiv")
    14
    """
    text = text.strip().lower()
    if not text or any(ch not in _ROMAN_VALUES for ch in text):
        return None
    total = 0
    previous = 0
    for char in reversed(text):
        value = _ROMAN_VALUES[char]
        total += -value if value < previous else value
        previous = max(previous, value)
    return total or None


def parse_list_marker(line: str) -> Optional[ListMarker]:
    """Recognise a list item and split off its marker.

    Ordered styles are distinguished so ``a)`` and ``iv.`` survive a round trip
    instead of collapsing into ``1.``.

    >>> parse_list_marker("  - Buy milk").style
    <ListStyle.BULLET: 'bullet'>
    >>> parse_list_marker("3. Third").number
    3
    >>> parse_list_marker("Not a list") is None
    True
    """
    if not line or not line.strip():
        return None

    match = _BULLET_RE.match(line)
    if match:
        indent, marker, content = match.groups()
        checked: Optional[bool] = None
        box = _CHECKBOX_RE.match(content)
        if box:
            checked = box.group(1).lower() == "x"
            content = box.group(2)
        return ListMarker(ListStyle.BULLET, marker, content, len(indent), checked=checked)

    match = _ORDERED_RE.match(line)
    if match:
        indent, number, content = match.groups()
        return ListMarker(
            ListStyle.ORDERED, number, content, len(indent), number=int(number)
        )

    # Roman before alpha: "i." and "v." are valid for both, and roman is the
    # stronger signal in outline numbering.
    match = _ROMAN_RE.match(line)
    if match:
        indent, numeral, content = match.groups()
        value = roman_to_int(numeral)
        if value is not None:
            style = ListStyle.UPPER_ROMAN if numeral.isupper() else ListStyle.LOWER_ROMAN
            return ListMarker(style, numeral, content, len(indent), number=value)

    match = _ALPHA_RE.match(line)
    if match:
        indent, letter, content = match.groups()
        style = ListStyle.UPPER_ALPHA if letter.isupper() else ListStyle.LOWER_ALPHA
        number = ord(letter.lower()) - ord("a") + 1
        return ListMarker(style, letter, content, len(indent), number=number)

    return None


def is_all_caps(text: str, min_letters: int = 3) -> bool:
    """True when the text is upper-case and long enough for that to mean something.

    >>> is_all_caps("EXECUTIVE SUMMARY")
    True
    >>> is_all_caps("OK")
    False
    """
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < min_letters:
        return False
    return all(ch.isupper() for ch in letters)


def is_sentence_like(text: str) -> bool:
    """True when the text reads as prose rather than a label.

    Used as a *negative* signal for heading detection.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[-1] in _TERMINAL_PUNCT and not stripped.endswith("?"):
        return True
    # Several sentence-ending marks inside suggests a paragraph.
    return stripped.count(". ") >= 1


def looks_like_heading(
    text: str,
    max_words: int = 14,
    followed_by_blank: bool = True,
) -> bool:
    """Heuristic heading detection for formats with no explicit heading markup.

    Requires the line to be short, free of terminal punctuation, and either
    title-cased, all-caps, or numbered like ``2.1 Scope``.

    >>> looks_like_heading("Executive Summary")
    True
    >>> looks_like_heading("This paragraph explains the results in detail.")
    False
    """
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return False
    words = stripped.split()
    if len(words) > max_words:
        return False
    if parse_list_marker(stripped) is not None:
        return False
    if stripped[-1] in _TERMINAL_PUNCT and not stripped.endswith(":"):
        return False
    if is_sentence_like(stripped):
        return False
    if not followed_by_blank:
        return False

    if is_all_caps(stripped):
        return True
    # Numbered outline heading: "3." / "3.1" / "IV." followed by words.
    if re.match(r"^(\d+(\.\d+)*|[IVXLCDM]+)[.)]?\s+\S", stripped):
        return True
    # Title Case: most significant words capitalised.
    significant = [w for w in words if len(w) > 3]
    if significant and sum(1 for w in significant if w[0].isupper()) / len(significant) >= 0.75:
        return True
    return False


def looks_like_code(text: str) -> bool:
    """Rough detection of source code, used when reading unstructured text.

    >>> looks_like_code("def main():")
    True
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    hits = sum(1 for line in lines if _CODE_HINTS.search(line))
    indented = sum(1 for line in lines if line.startswith(("    ", "\t")))
    return hits >= max(1, len(lines) // 3) or indented == len(lines) > 1


def normalise_whitespace(text: str, collapse: bool = True) -> str:
    """Tidy whitespace: normalise line endings, strip trailing spaces, optionally
    collapse runs of spaces.

    >>> normalise_whitespace("a\\r\\n  b   c  ")
    'a\\n b c'
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    if collapse:
        text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()
