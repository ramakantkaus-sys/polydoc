"""Command-line interface for polydoc.

Installed as the ``polydoc`` command::

    polydoc convert report.pdf report.docx
    polydoc convert *.docx --to markdown --outdir notes/
    polydoc inspect contract.pdf
    polydoc extract data.pdf --tables --format csv
    polydoc edit template.docx offer.pdf --replace "{{client}}=Acme Ltd"
    polydoc formats

Reader and writer options are exposed generically through ``--read-opt`` and
``--write-opt``, so any keyword a format accepts is reachable without the CLI needing to
know about it::

    polydoc convert scan.pdf out.md --read-opt tables=false --read-opt pages=1-5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: F401

from . import __version__
from .exceptions import PolydocError

__all__ = ["main"]


# ---------------------------------------------------------------------------
# Option parsing
# ---------------------------------------------------------------------------


def _coerce(value: str) -> Any:
    """Turn a command-line string into a sensible Python value.

    ``true``/``false``/``none`` become their Python equivalents, digits become numbers,
    ``a-b`` becomes a range tuple (for ``pages``), ``a,b`` becomes a list, and anything
    JSON-shaped is parsed as JSON.
    """
    text = value.strip()
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("none", "null", ""):
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        if "." in text:
            return float(text)
    except ValueError:
        pass
    # "2-7" is a page range.
    if "-" in text[1:] and all(part.strip().isdigit() for part in text.split("-", 1)):
        start, end = (part.strip() for part in text.split("-", 1))
        return (int(start), int(end))
    if text[:1] in "[{\"":
        try:
            return json.loads(text)
        except ValueError:
            pass
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def _parse_options(pairs: Optional[Sequence[str]]) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    for pair in pairs or ():
        if "=" not in pair:
            raise PolydocError(f"Options must be key=value; got {pair!r}")
        key, _, raw = pair.partition("=")
        options[key.strip()] = _coerce(raw)
    return options


def _parse_replacements(pairs: Optional[Sequence[str]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for pair in pairs or ():
        if "=" not in pair:
            raise PolydocError(
                f"Replacements must be OLD=NEW; got {pair!r}"
            )
        old, _, new = pair.partition("=")
        out.append((old, new))
    return out


def _expand_sources(patterns: Sequence[str]) -> List[Path]:
    """Resolve arguments to files, expanding globs the shell may not have.

    Uses :mod:`glob` rather than ``Path.glob`` because the latter rejects absolute
    patterns with ``NotImplementedError``, and arguments are routinely absolute.
    """
    import glob as _glob

    found: List[Path] = []
    for pattern in patterns:
        path = Path(pattern)
        if path.exists():
            found.append(path)
            continue
        matches = sorted(Path(match) for match in _glob.glob(pattern))
        if not matches:
            raise PolydocError(f"No such file: {pattern}")
        found.extend(matches)
    return found


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_convert(args: argparse.Namespace) -> int:
    from .api import convert

    read_options = _parse_options(args.read_opt)
    write_options = _parse_options(args.write_opt)
    replacements = _parse_replacements(args.replace)

    transform = None
    if replacements:
        from .edit import replace_text

        def transform(document):  # type: ignore[misc]
            for old, new in replacements:
                replace_text(document, old, new, regex=args.regex)

    sources, targets = _resolve_paths(args)

    failures = 0
    for source, target in zip(sources, targets):
        try:
            written = convert(
                source,
                target,
                source_format=getattr(args, "from"),
                target_format=args.to,
                read_options=read_options,
                write_options=write_options,
                transform=transform,
            )
            if not args.quiet:
                size = written.stat().st_size
                print(f"{source} -> {written}  ({size:,} bytes)")
        except PolydocError as exc:
            failures += 1
            print(f"{source}: {exc}", file=sys.stderr)
        except Exception as exc:  # pragma: no cover - unexpected backend failure
            failures += 1
            print(f"{source}: {type(exc).__name__}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def _resolve_paths(args: argparse.Namespace) -> "Tuple[List[Path], List[Path]]":
    """Split the positional paths into sources and their targets.

    One greedy ``nargs="+"`` positional is used rather than ``SOURCE... [TARGET]``:
    argparse hands every positional to the greedy list and leaves an optional trailing
    one empty, so the split has to happen here. The rules, in order:

    1. ``--outdir`` given: every path is a source.
    2. Two or more paths, last is an existing directory: that is the output directory.
    3. Exactly two paths: the second is the output file.
    4. Three or more paths without ``--outdir``: the last is treated as a directory.
    5. One path: it is the source, and ``--to`` decides the output name.
    """
    from .formats import extension_for

    paths = list(args.paths)
    outdir: Optional[Path] = Path(args.outdir) if args.outdir else None

    if outdir is None and len(paths) >= 2:
        candidate = Path(paths[-1])
        if candidate.is_dir():
            outdir = candidate
            paths = paths[:-1]
        elif len(paths) == 2:
            return (_expand_sources(paths[:1]), [candidate])
        else:
            outdir = candidate
            paths = paths[:-1]

    sources = _expand_sources(paths)
    if not args.to:
        raise PolydocError("Converting without an explicit output file needs --to FORMAT")

    suffix = extension_for(args.to) or f".{args.to}"
    targets = [
        (outdir / (source.stem + suffix)) if outdir is not None
        else source.with_suffix(suffix)
        for source in sources
    ]
    return (sources, targets)


def _cmd_inspect(args: argparse.Namespace) -> int:
    from .api import open_document

    options = _parse_options(args.read_opt)
    document = open_document(args.source, format=getattr(args, "from"), **options)

    if args.json:
        payload = {
            "summary": document.summary(),
            "metadata": document.metadata.to_dict(),
            "outline": [
                {"level": s.level, "title": s.title_text}
                for s in document.outline()
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    summary = document.summary()
    meta = document.metadata

    print(f"{args.source}")
    print(f"  format      {summary['source_format']}")
    print(f"  title       {meta.title or '-'}")
    print(f"  author      {meta.author or '-'}")
    if meta.created:
        print(f"  created     {meta.created}")
    if meta.language:
        print(f"  language    {meta.language}")
    print(f"  pages       {summary['pages']}")
    print(f"  words       {summary['words']:,}")
    print(f"  top blocks  {summary['blocks']}")

    counts = summary["block_counts"]
    if counts:
        print("  content")
        width = max(len(name) for name in counts)
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {name:<{width}}  {count}")

    outline = document.outline()
    if outline:
        print("  outline")
        _print_outline(outline, indent=4)
    return 0


def _print_outline(sections: Sequence[Any], indent: int) -> None:
    for section in sections:
        title = section.title_text or "(untitled)"
        print(f"{' ' * indent}{title[:70]}")
        _print_outline(section.subsections, indent + 2)


def _cmd_extract(args: argparse.Namespace) -> int:
    from .api import open_document

    options = _parse_options(args.read_opt)
    document = open_document(args.source, format=getattr(args, "from"), **options)

    if args.tables:
        tables = document.tables
        if not tables:
            print("No tables found.", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps([t.to_matrix() for t in tables], indent=2))
        else:
            import csv

            writer = csv.writer(sys.stdout, lineterminator="\n")
            for index, table in enumerate(tables):
                if index:
                    writer.writerow([])
                writer.writerows(table.to_matrix())
        return 0

    if args.outline:
        _print_outline(document.outline(), indent=0)
        return 0

    if args.format in ("md", "markdown"):
        sys.stdout.write(document.to_text("markdown"))
    elif args.format == "json":
        sys.stdout.write(document.to_text("json"))
    else:
        sys.stdout.write(document.text + "\n")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    from .api import open_document, save
    from .edit import remove_all, replace_text

    options = _parse_options(args.read_opt)
    document = open_document(args.source, format=getattr(args, "from"), **options)

    changes = 0
    for old, new in _parse_replacements(args.replace):
        changes += replace_text(document, old, new, regex=args.regex)
    for selector in args.remove or ():
        changes += remove_all(document, selector)

    target = Path(args.target)
    written = save(
        document,
        target,
        format=args.to,
        **_parse_options(args.write_opt),
    )
    if not args.quiet:
        print(f"{changes} change(s) -> {written}")
    return 0


def _cmd_formats(args: argparse.Namespace) -> int:
    from .api import supported_formats

    rows = supported_formats()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    headers = ("format", "read", "write", "extensions", "description")
    table: List[Tuple[str, ...]] = [headers]
    for row in rows:
        table.append(
            (
                str(row["format"]),
                "yes" if row["read"] else "-",
                "yes" if row["write"] else "-",
                " ".join(row["extensions"]),  # type: ignore[arg-type]
                str(row["description"]),
            )
        )
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    for index, row in enumerate(table):
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if index == 0:
            print("  ".join("-" * widths[i] for i in range(len(headers))))
    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    from .formats.registry import describe_detection

    for source in _expand_sources(args.sources):
        try:
            info = describe_detection(source)
            print(f"{source}")
            for key in ("resolved", "magic", "by_extension", "suffix", "size"):
                print(f"  {key:<14} {info.get(key)}")
        except PolydocError as exc:
            print(f"{source}: {exc}", file=sys.stderr)
            return 1
    return 0


# ---------------------------------------------------------------------------
# Argument wiring
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser, read: bool = True, write: bool = True) -> None:
    if read:
        parser.add_argument(
            "--from", dest="from", metavar="FORMAT", help="force the input format"
        )
        parser.add_argument(
            "--read-opt",
            action="append",
            metavar="KEY=VALUE",
            help="reader option (repeatable)",
        )
    if write:
        parser.add_argument(
            "--write-opt",
            action="append",
            metavar="KEY=VALUE",
            help="writer option (repeatable)",
        )


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser. Exposed for testing and documentation."""
    parser = argparse.ArgumentParser(
        prog="polydoc",
        description="Read, edit, and convert documents through one universal model.",
        epilog="Run 'polydoc COMMAND --help' for command-specific options.",
    )
    parser.add_argument("--version", action="version", version=f"polydoc {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # convert
    convert = subparsers.add_parser(
        "convert",
        help="convert between formats",
        description="Convert one or many documents to another format.",
    )
    convert.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="input file(s), optionally followed by the output file or directory",
    )
    convert.add_argument("--to", metavar="FORMAT", help="output format")
    convert.add_argument("--outdir", metavar="DIR", help="write outputs into DIR")
    convert.add_argument(
        "--replace",
        action="append",
        metavar="OLD=NEW",
        help="replace text before writing (repeatable)",
    )
    convert.add_argument(
        "--regex", action="store_true", help="treat --replace patterns as regexes"
    )
    convert.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    _add_common(convert)
    convert.set_defaults(func=_cmd_convert)

    # inspect
    inspect = subparsers.add_parser(
        "inspect", help="show a document's structure and metadata"
    )
    inspect.add_argument("source", metavar="SOURCE")
    inspect.add_argument("--json", action="store_true", help="emit JSON")
    _add_common(inspect, write=False)
    inspect.set_defaults(func=_cmd_inspect)

    # extract
    extract = subparsers.add_parser("extract", help="print text, tables, or the outline")
    extract.add_argument("source", metavar="SOURCE")
    extract.add_argument("--tables", action="store_true", help="extract tables only")
    extract.add_argument("--outline", action="store_true", help="print the heading outline")
    extract.add_argument(
        "--format",
        default="text",
        choices=["text", "markdown", "md", "json", "csv"],
        help="output shape (default: text)",
    )
    _add_common(extract, write=False)
    extract.set_defaults(func=_cmd_extract)

    # edit
    edit = subparsers.add_parser("edit", help="apply edits and write the result")
    edit.add_argument("source", metavar="SOURCE")
    edit.add_argument("target", metavar="TARGET")
    edit.add_argument("--to", metavar="FORMAT", help="output format")
    edit.add_argument(
        "--replace", action="append", metavar="OLD=NEW", help="text replacement (repeatable)"
    )
    edit.add_argument(
        "--remove", action="append", metavar="SELECTOR", help="remove matching nodes (repeatable)"
    )
    edit.add_argument("--regex", action="store_true", help="treat patterns as regexes")
    edit.add_argument("-q", "--quiet", action="store_true")
    _add_common(edit)
    edit.set_defaults(func=_cmd_edit)

    # formats
    formats = subparsers.add_parser("formats", help="list supported formats")
    formats.add_argument("--json", action="store_true", help="emit JSON")
    formats.set_defaults(func=_cmd_formats)

    # detect
    detect = subparsers.add_parser("detect", help="report how a file would be classified")
    detect.add_argument("sources", nargs="+", metavar="SOURCE")
    detect.set_defaults(func=_cmd_detect)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    try:
        return int(args.func(args) or 0)
    except PolydocError as exc:
        print(f"polydoc: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"polydoc: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover - piping into head
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
