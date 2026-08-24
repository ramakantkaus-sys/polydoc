# Changelog

All notable changes to polydoc are recorded here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-08-24

Fixes a crash affecting one of the most common conversions there is: a Word document
containing a logo, converted to PDF.

### Fixed

- **DOCX → PDF crashed when an image sat inline beside text.** The PDF writer passed
  `id(reader)` — a Python object's memory address — as ReportLab's image `src`, so
  ReportLab tried to open it as a filename and raised
  `OSError: Cannot open resource "2542527337408"`. Inline images are now embedded as
  `data:` URIs, which ReportLab resolves natively. A logo beside text is precisely how
  letterheads and certifications are laid out, so this affected a lot of real documents.
- **Standalone figures were recentred on export.** ReportLab centres image flowables by
  default, so a left-aligned figure in Word drifted to the middle of the PDF page. The
  DOCX reader now carries the paragraph's alignment onto the `Image` block, and the PDF
  writer honours it, defaulting to left rather than centre.
- **An unreadable image no longer costs the whole document.** Corrupt or unsupported
  image data degrades to the alt text instead of aborting the conversion.
- Inline images preserve aspect ratio when only one dimension is known, and are capped
  to the printable width so an oversized image cannot overflow the frame.

### Added

- 34 image tests (`tests/test_images.py`) covering all three structural positions —
  standalone figure, inline beside text, and inside a table cell — across DOCX, PDF,
  HTML, Markdown, JSON, and PPTX. This area previously had **no** tests, which is why
  the crash shipped.
- Verification includes rendering the output PDF and checking the logo's colours are
  actually present in the rasterised page, rather than only asserting an image object
  exists.

## [0.1.1] - 2026-08-24

Packaging and portability fixes found by CI on Linux and macOS, which the initial
release could not have caught (it was verified on Windows only).

### Changed

- **`markdown-it-py` and `beautifulsoup4` are now core dependencies.** Both are pure
  Python and small, so `pip install polydoc` needs no compilation and genuinely works
  out of the box for Markdown, HTML, text, JSON and CSV. Previously a bare install
  could read almost nothing.
- **`lxml` is now optional even for HTML.** BeautifulSoup falls back to the stdlib
  parser; `polydoc[html]` installs lxml for speed and better tolerance of malformed
  markup.
- `polydoc[markdown]` is kept as an empty extra so existing pins keep working.

### Fixed

- Test suite failed on Linux and macOS but passed on Windows: libxml2 enforces a 10 MB
  text-node ceiling that the Windows lxml build does not, and one limits test built a
  64 MB node. The fixture is now 4 MB, which exercises the guard identically and is
  portable.
- Lint configuration now pins its rule set explicitly instead of inheriting ruff's
  defaults, which vary by version and made CI results depend on the runner's ruff.
- Removed unused imports and dead assignments flagged by lint.

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

[0.1.2]: https://github.com/ramakantkaus-sys/polydoc/releases/tag/v0.1.2
[0.1.1]: https://github.com/ramakantkaus-sys/polydoc/releases/tag/v0.1.1
[0.1.0]: https://github.com/ramakantkaus-sys/polydoc/releases/tag/v0.1.0
