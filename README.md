# polydoc

**One editable document model for every format.** Read a PDF, edit it like a data
structure, write it out as DOCX. Or PPTX. Or Markdown.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

```python
import polydoc

doc = polydoc.open("quarterly.pdf")        # PDF -> universal model
doc.replace_text("FY2024", "FY2025")       # edit, preserving formatting
doc.save("quarterly.docx")                 # model -> DOCX
```

---

## Why this exists

Python has good libraries for *reading* documents. [Docling](https://docling.ai/),
[MarkItDown](https://github.com/microsoft/markitdown), and
[unstructured](https://github.com/Unstructured-IO/unstructured) all convert PDFs and
Office files into Markdown or JSON, and they do it well.

They are all **one-way**. They extract *to* a text representation for something else
(usually an LLM) to consume. There is no document left afterwards — nothing you can
modify and write back out.

polydoc keeps a **mutable document in the middle**, so reading, editing, and writing are
one pipeline instead of three tools:

|  | Docling / MarkItDown / unstructured | pypandoc | polydoc |
| --- | --- | --- | --- |
| Read PDF, DOCX, PPTX, XLSX | yes | partial | yes |
| Write DOCX, PPTX, XLSX, PDF | no | yes | yes |
| Queryable document tree | no | no | yes |
| Edit and write back | no | no | yes |
| Preserves formatting through an edit | n/a | n/a | yes |
| Requires an external binary | no | yes (pandoc) | no |

### The problem it actually solves

Suppose a Word paragraph reads *"Period: **FY**_2024_ Q3"*. Word stores that as several
separate runs, and the phrase you want to replace straddles them. The obvious approach
silently fails:

```python
# python-docx: finds nothing, because no single run contains "FY2024 Q3"
for run in paragraph.runs:
    run.text = run.text.replace("FY2024 Q3", "FY2025 Q1")
```

The usual workarounds either flatten the paragraph to plain text (destroying the bold and
italic) or only handle matches that happen to sit inside one run. polydoc matches against
the paragraph's *continuous* text, so run boundaries are invisible to the search, and the
replacement inherits the formatting of where the match began:

```python
doc.replace_text("FY2024 Q3", "FY2025 Q1")   # works; bold and italic intact
```

---

## Install

```bash
pip install polydoc              # Markdown, HTML, text, JSON, CSV
pip install "polydoc[all]"       # adds PDF, DOCX, PPTX, XLSX
```

The core install is pure Python with no compilation, and already handles Markdown, HTML,
plain text, JSON and CSV. The heavier backends are extras, so you install only what you
need:

```bash
pip install "polydoc[pdf]"       # PyMuPDF, pdfplumber, reportlab
pip install "polydoc[docx]"      # python-docx
pip install "polydoc[pptx]"      # python-pptx
pip install "polydoc[xlsx]"      # openpyxl
pip install "polydoc[html]"      # lxml, for faster and more tolerant HTML parsing
pip install "polydoc[docx,pdf]"  # combine as needed
```

A missing backend produces an actionable error rather than an `ImportError`:

```
MissingDependencyError: Reading DOCX requires the 'python-docx' package.
Install it with: pip install 'polydoc[docx]'
```

---

## Format support

| Format | Read | Write | Notes |
| --- | :---: | :---: | --- |
| Markdown | yes | yes | CommonMark + GFM tables, strikethrough, task lists |
| HTML / XHTML | yes | yes | Inline CSS, semantic output |
| Plain text | yes | yes | Infers headings, lists, and code |
| JSON | yes | yes | Native format; **lossless** round trip |
| CSV / TSV | yes | yes | Delimiter and header sniffing |
| DOCX | yes | yes | Runs, hyperlinks, nested lists, merged cells, footnotes |
| PPTX | yes | yes | Slides, real bullet XML, tables, speaker notes |
| XLSX | yes | yes | Typed cells, number formats, merges, freeze panes |
| PDF | yes | yes | Layout reconstruction on read; bookmark outline on write |

Adding a format is a decorator away — see [Extending](#extending).

---

## Usage

### Convert

```python
import polydoc

polydoc.convert("report.pdf", "report.docx")
polydoc.convert("slides.pptx", "notes.md")
polydoc.convert("data.xlsx", "data.csv")
```

Entirely in memory, for web handlers and pipelines:

```python
pdf_bytes = polydoc.convert_bytes(markdown_text, "markdown", "pdf")
```

### Inspect

```python
doc = polydoc.open("paper.pdf")

doc.metadata.title            # 'Attention Is All You Need'
doc.metadata.authors          # ['Ashish Vaswani', ...]
doc.page_count                # 15
doc.word_count                # 4127

for heading in doc.headings:
    print("  " * heading.level, heading.text)

for table in doc.tables:
    print(table.to_matrix())  # [['Model', 'BLEU'], ['Transformer', '28.4'], ...]
```

`doc.summary()` gives a quick profile, and `doc.outline()` builds a nested section tree
from the heading sequence.

### Query with selectors

Selectors are CSS, because you already know CSS:

```python
doc.find("heading[level=1]")                   # first h1
doc.find_all("table td:contains(Overdue)")     # cells mentioning a word
doc.find_all("list_item[checked=false]")       # unfinished task items
doc.find("section:has(table) > paragraph")     # first para of a section with a table
doc.find_all("paragraph[style.alignment=center]")
doc.find_all("h2, h3")                         # union
```

Supported: type names (`heading`, `table_cell`, ...) and HTML aliases (`p`, `h2`, `li`,
`td`, `pre`, `img`, `a`); `[attr]`, `[attr=v]`, `[attr!=v]`, `^=`, `$=`, `*=`, `>`, `<`,
`>=`, `<=`; `:contains()`, `:matches()`, `:has()`, `:not()`, `:first`, `:last`,
`:nth(n)`, `:empty`, `:root`, `:only`; and the `A B`, `A > B`, `A, B` combinators.

### Edit

```python
from polydoc.edit import replace_text, style_text, remove_all, restyle, insert_after
from polydoc.model import Paragraph

# Formatting-preserving replacement, across run boundaries
doc.replace_text("{{client}}", "Acme Ltd")
doc.replace_text(r"FY(\d{4})", r"fiscal \1", regex=True)
doc.replace_text("total", "TOTAL", selector="table")          # scoped
doc.replace_text(r"\d+", lambda m: str(int(m.group()) * 2), regex=True)

# Restyle just the matched text
style_text(doc, "OVERDUE", bold=True, color="#cc0000")

# Restyle whole blocks
restyle(doc, "heading[level=1]", color="#003366", alignment="center")

# Structural changes
remove_all(doc, "paragraph:empty")
insert_after(doc.find("h1"), Paragraph.of("Revised edition."))
```

### Compose reusable transforms

```python
from polydoc.edit import Pipeline, strip_empty

finalise = (
    Pipeline(name="finalise")
    .replace("{{client}}", "Acme Ltd")
    .style("Acme Ltd", bold=True)
    .remove("paragraph:empty")
    .then(strip_empty)
)

polydoc.convert("template.docx", "offer.pdf", transform=finalise)
```

### Build a document from scratch

```python
from polydoc.model import Document, Heading, Paragraph, Table, Text, TextStyle, Metadata

doc = Document(metadata=Metadata(title="Report", authors=["Ada Lovelace"]))
doc.append(
    Heading.of("Results", 1),
    Paragraph([Text("Revenue rose "), Text("14%", TextStyle(bold=True)), Text(".")]),
    Table.from_rows([["Quarter", "Revenue"], ["Q1", "1200"]], caption="Detail"),
)
doc.save("report.pdf")
```

### Template filling

The pattern this library is best at — one template, many formats:

```python
for client in ["Acme Ltd", "Globex", "Initech"]:
    polydoc.convert(
        "contract-template.docx",
        f"contracts/{client}.pdf",
        transform=lambda d, c=client: d.replace_text("{{client}}", c),
    )
```

---

## Command line

```bash
polydoc convert report.pdf report.docx
polydoc convert *.docx --to markdown --outdir notes/
polydoc convert scan.pdf out.md --read-opt tables=false --read-opt pages=1-5

polydoc inspect contract.pdf              # structure, metadata, outline
polydoc inspect contract.pdf --json

polydoc extract data.pdf --tables         # tables as CSV
polydoc extract paper.pdf --outline

polydoc edit template.docx offer.pdf --replace "{{client}}=Acme Ltd"
polydoc edit report.docx clean.docx --remove "paragraph:empty"

polydoc formats                           # what is supported
polydoc detect mystery-file               # how it would be classified
```

Any reader or writer keyword is reachable via `--read-opt` / `--write-opt`, so the CLI
never lags behind the library.

---

## How PDF reading works

A PDF contains positioned glyphs and nothing else — no paragraphs, no headings, no
reading order. polydoc reconstructs them:

1. **Spans** are collected with their font, size, weight, and colour.
2. **Font sizes are profiled across the whole document**, then ranked into heading
   levels. This is relative, not absolute — 14pt is a heading in a document set in 10pt
   and body text in one set in 14pt.
3. **Columns** are found from whitespace gutters, and lines sorted into reading order, so
   a two-column paper is not read straight across.
4. **Paragraphs** are rebuilt from vertical gaps, indentation changes, and short-line
   endings, with hyphenated words rejoined.
5. **Tables** are located with pdfplumber; text inside a table is removed from the prose
   flow so it is not duplicated.
6. **Running headers, footers, and page numbers** are detected by repetition across pages
   and dropped.
7. **Bullets** are recovered even though PDFs position them rather than delimiting them
   with whitespace, and **code indentation** is rebuilt from x-offsets.

The checks are deliberately conservative: wrongly splitting a page or promoting a list
item to a heading damages a document more than leaving it alone.

---

## The document model

Everything is a `Node` in one tree.

```
Document
├── metadata: Metadata            title, authors, dates, keywords, custom
├── body: list[Block]
│   ├── Heading(content, level)
│   ├── Paragraph(content, style)
│   ├── Table(rows, caption, header_rows, column_widths)
│   │   └── TableRow → TableCell(colspan, rowspan) → Block
│   ├── ListBlock(items, marker_style, start)
│   │   └── ListItem(content, checked) → Block
│   ├── CodeBlock(code, language)
│   ├── Quote, Image, HorizontalRule, PageBreak
│   ├── Container(role)            header, footer, sheet, aside
│   ├── Page(number), Slide(title, notes)
│   └── Section(title, level)      derived by doc.outline()
├── footnotes: list[Footnote]
└── resources: dict[str, bytes]    embedded images
```

Inline content is a flat sequence of `Text` runs carrying a `TextStyle`, plus `Link`,
`LineBreak`, `InlineImage`, `Math`, `FootnoteRef`.

**Styles are sparse.** Every field defaults to `None`, meaning "inherit". Reading a DOCX
run that only sets `bold` records exactly that, rather than inventing a font size that
would then be baked into every other format's output.

**JSON is lossless.** `read → write → read` returns an equal document, which makes JSON
useful for caching an expensive PDF parse or moving documents between processes:

```python
doc = polydoc.open("huge.pdf")
doc.save("huge.json")                  # cache the parse
same = polydoc.open("huge.json")       # instant, and == doc
```

---

## Extending

Registering a format from outside the package is the same work the built-ins do:

```python
from polydoc.formats import Reader, Source, register_reader
from polydoc.model import Document, Paragraph

@register_reader
class RtfReader(Reader):
    format = "rtf"
    extensions = (".rtf",)
    description = "Rich Text Format"

    def read(self, source: Source, **options) -> Document:
        doc = Document([Paragraph.of(my_rtf_parser(source.text()))])
        return self.finalise(doc, source)
```

`polydoc.open("file.rtf")` now works, as does `polydoc convert file.rtf out.md`.

---

## Untrusted input

If your service accepts uploaded documents, read this section.

Office formats are ZIP archives, and ZIP is trivially weaponisable: a few hundred
kilobytes of repetitive XML expands to hundreds of megabytes once parsed. polydoc was
measured against this before the guards existed — a **297 KB DOCX expanded to 268 MB**
of text, a ratio of about 900:1. Scaled to a 30 MB upload that is roughly 27 GB and a
dead process.

polydoc now screens input **before** handing it to a backend, using two cheap checks that
cost no decompression (the sizes are read from the ZIP central directory):

```python
import polydoc

# Defaults: 256 MB expanded, 100:1 ratio, 512 MB input, 10k entries, 256 nesting levels
polydoc.open("upload.docx")                          # bombs raise DocumentTooLargeError

# Tighten for a public endpoint
polydoc.open("upload.docx", max_expanded_bytes=32 * 1024 * 1024)

# Set a process-wide policy once at startup
polydoc.set_default_limits(polydoc.Limits(max_expanded_bytes=32 * 1024 * 1024))

# Opt out for input you produced yourself
polydoc.open("mine.docx", limits=polydoc.Limits.unlimited())
```

Catch `DocumentTooLargeError`, or `PolydocError` for everything:

```python
from polydoc import DocumentTooLargeError, PolydocError

try:
    doc = polydoc.open(upload)
except DocumentTooLargeError as exc:
    return reject(413, str(exc))     # the message is user-safe and actionable
except PolydocError as exc:
    return reject(400, str(exc))
```

### What was verified

`scripts/hostile_input_probe.py` and `scripts/bomb_probe.py` are kept in the repo and run
these cases. Current results:

| Attack | Result |
| --- | --- |
| XXE via DOCX / XLSX / PPTX / HTML (local file read) | **no disclosure** — entities are not resolved |
| `file://` in an `<img src>` | not dereferenced; kept as a reference |
| Decompression bomb, 900:1 in a *valid* package | rejected in under 2 s |
| Billion laughs (10⁹ entity expansion) | rejected by lxml's amplification guard |
| Truncated / corrupt PDF, DOCX, XLSX | clear errors, no crash |
| PDF declaring 10⁹ pages | rejected |
| HTML nested 5000 deep | `DocumentTooLargeError`, not `RecursionError` |
| 24 threads converting concurrently | no errors |
| Damaged PDF trailer | recovers gracefully |

### Still your responsibility

- **Run parsing in a worker with its own memory cap and timeout.** The limits above bound
  declared sizes, not every possible pathological CPU path.
- **Scanned PDFs need OCR first**; polydoc reads text, not pixels.
- **This has not been fuzzed.** The probes cover known attack classes, not the unknown
  ones. Treat 0.1.x as beta and pin the version.

## Limitations

Worth knowing before you rely on it:

- **Scanned PDFs need OCR.** polydoc reads text, not pixels. Run OCR first
  (e.g. OCRmyPDF), then read the result.
- **PDF structure is inferred, not read.** Accuracy is high on typical text documents and
  degrades on complex magazine-style layouts.
- **PDF is not a round-trip format.** Writing a PDF and reading it back recovers the
  structure well, but a PDF is a rendering, not a document — exact visual fidelity is not
  preserved.
- **Charts become their data.** A PPTX chart is read as the table behind it; the rendered
  chart is not reproduced.
- **Footnotes in DOCX are written as an end section**, not into Word's native footnote
  store.
- **Legacy `.doc`, `.xls`, `.ppt` are not supported.** They are detected and reported
  clearly; convert them to the XML formats first.
- **Equations** are carried as LaTeX where available, and are not rendered.
- **Whole files are held in memory.** Format detection needs to inspect content, so a
  source is read once and cached. Peak usage is a small multiple of input size; there is
  no streaming mode. `max_input_bytes` bounds this.
- **A programmatically built tree nested thousands deep** will hit Python's recursion
  limit on serialisation. Parsed input is guarded by `max_nesting_depth`; a tree you
  construct yourself is not.
- **Version 0.1.x.** The API may change before 1.0. Pin it.

---

## Development

```bash
git clone https://github.com/polydoc/polydoc
cd polydoc
pip install -e ".[all,dev]"
pytest                       # 629 tests
pytest --cov=polydoc         # 82% coverage
```

The pytest suite runs against the source tree. `scripts/smoke_test.py` runs against an
*installed* build instead, which is what catches packaging faults and undeclared
dependencies:

```bash
python -m build
python -m venv /tmp/verify
/tmp/verify/bin/pip install "dist/polydoc-0.1.0-py3-none-any.whl[all]"
cd /tmp && /tmp/verify/bin/python /path/to/scripts/smoke_test.py
```

Run it with no optional backends too — it should skip the unavailable formats and still
pass. CI does both.

Contributions welcome. A new format needs a reader, a writer, and round-trip tests
following the pattern in `tests/test_formats_binary.py`.

---

## License

MIT. See [LICENSE](LICENSE).

polydoc builds on [PyMuPDF](https://pymupdf.readthedocs.io/),
[pdfplumber](https://github.com/jsvine/pdfplumber),
[ReportLab](https://www.reportlab.com/),
[python-docx](https://python-docx.readthedocs.io/),
[python-pptx](https://python-pptx.readthedocs.io/),
[openpyxl](https://openpyxl.readthedocs.io/),
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/), and
[markdown-it-py](https://markdown-it-py.readthedocs.io/) — each excellent at its own
format. polydoc is the layer that lets them talk to each other.
