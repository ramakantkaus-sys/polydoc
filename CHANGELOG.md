# Changelog

All notable changes to polydoc are recorded here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-24

First release.

### Added

**Universal document model** (`polydoc.model`)

- `Document` root with `Metadata`, embedded `resources`, and footnotes.
- Block content: `Heading`, `Paragraph`, `Table`/`TableRow`/`TableCell`,
  `ListBlock`/`ListItem`, `CodeBlock`, `Quote`, `Image`, `HorizontalRule`,
  `PageBreak`, `Container`, `Section`, `Page`, `Slide`, `Footnote`.
- Inline content: styled `Text` runs, `Link`, `LineBreak`, `InlineImage`, `Math`,
  `FootnoteRef`, `DynamicField`.
- Sparse `TextStyle`/`ParagraphStyle` where `None` means "inherit", so reading a
  document records only what it actually specifies.
- `BBox`/`PageGeometry` for page-based formats.
- Lossless JSON serialisation: `read -> write -> read` returns an equal document.

**Formats** (`polydoc.formats`)

| Format | Read | Write |
| --- | --- | --- |
| Markdown (CommonMark + GFM) | yes | yes |
| HTML / XHTML | yes | yes |
| Plain text | yes | yes |
| JSON (native, lossless) | yes | yes |
| CSV / TSV | yes | yes |
| DOCX | yes | yes |
| PPTX | yes | yes |
| XLSX | yes | yes |
| PDF | yes | yes |

- Format detection from magic bytes, ZIP member inspection, extension, and content
  heuristics, in that order of precedence.
- A plugin registry: `@register_reader` / `@register_writer` add formats from outside
  the package.
- Optional backends fail with an actionable install hint rather than an `ImportError`.

**Structure inference** (`polydoc.intelligence`)

- Heading levels ranked from document-wide font statistics, not absolute sizes.
- Column detection from whitespace gutters, and reading-order sorting.
- Paragraph reconstruction from vertical gaps, indentation, and short-line endings,
  with hyphenation repair.
- List-marker parsing (bullet, decimal, alpha, roman, task checkboxes) and nesting
  from indentation.
- Running header/footer and page-number removal.
- Code-block recovery from runs of monospaced paragraphs.

**Editing** (`polydoc.edit`)

- CSS-like selectors: types, attributes with comparison operators, `:contains`,
  `:matches`, `:has`, `:not`, `:first`/`:last`/`:nth`, descendant/child/union.
- `replace_text` preserves character formatting and matches **across run boundaries**,
  the case that defeats naive `python-docx` replacement.
- `style_text` splits matched spans into their own runs to restyle just the match.
- Structural operations: `insert_before`/`insert_after`, `move`, `wrap`/`unwrap`,
  `remove_all`, `restyle`, `shift_heading_levels`, `strip_empty`,
  `merge_adjacent_paragraphs`.
- `Pipeline` for composing reusable transforms.

**Hardening for untrusted input** (`polydoc.formats.limits`)

- Resource ceilings applied *before* a backend sees the data: expanded size, compression
  ratio, absolute input size, archive entry count, and markup nesting depth.
- `DocumentTooLargeError` with an actionable message naming the option to override.
- Configurable per call (`max_expanded_bytes=...`), or process-wide via
  `polydoc.set_default_limits()`. `Limits.unlimited()` opts out for trusted input.
- Closes a measured decompression-bomb vector: a 297 KB DOCX previously expanded to
  268 MB of text unchecked.
- Deeply nested markup now raises `DocumentTooLargeError` rather than a bare
  `RecursionError` from inside the parser.
- Verified against XXE (DOCX/XLSX/PPTX/HTML), billion-laughs, zip bombs, truncated
  archives, and concurrent use. See `scripts/hostile_input_probe.py`.

**Interfaces**

- `polydoc.open`, `loads`, `save`, `dumps`, `convert`, `convert_bytes`, `detect`.
- A `polydoc` command with `convert`, `inspect`, `extract`, `edit`, `formats`,
  and `detect` subcommands.
- PEP 561 typed (`py.typed`).

[0.1.0]: https://github.com/polydoc/polydoc/releases/tag/v0.1.0
