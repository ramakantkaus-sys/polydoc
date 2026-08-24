"""The command-line interface."""

from __future__ import annotations

import json

import pytest

from conftest import needs_docx

from polydoc.cli import build_parser, main


@pytest.fixture
def markdown_file(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(
        "# Report\n\nBody for {{client}}.\n\n## Data\n\n| a | b |\n| - | - |\n| 1 | 2 |\n",
        encoding="utf-8",
    )
    return path


class TestParser:
    def test_builds(self):
        assert build_parser().prog == "polydoc"

    def test_no_command_prints_help(self, capsys):
        assert main([]) == 1
        assert "usage" in capsys.readouterr().out

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])
        assert exit_info.value.code == 0
        assert "polydoc" in capsys.readouterr().out


class TestConvert:
    def test_single_file(self, markdown_file, tmp_path, capsys):
        target = tmp_path / "out.txt"
        assert main(["convert", str(markdown_file), str(target)]) == 0
        assert target.exists()
        assert "->" in capsys.readouterr().out

    def test_quiet(self, markdown_file, tmp_path, capsys):
        main(["convert", str(markdown_file), str(tmp_path / "o.txt"), "-q"])
        assert capsys.readouterr().out == ""

    def test_explicit_formats(self, markdown_file, tmp_path):
        target = tmp_path / "out.dat"
        assert main(
            ["convert", str(markdown_file), str(target), "--from", "markdown", "--to", "txt"]
        ) == 0
        assert target.exists()

    def test_replace_option(self, markdown_file, tmp_path):
        target = tmp_path / "out.md"
        main(["convert", str(markdown_file), str(target), "--replace", "{{client}}=Acme"])
        assert "Acme" in target.read_text(encoding="utf-8")

    def test_multiple_replacements(self, markdown_file, tmp_path):
        target = tmp_path / "out.md"
        main([
            "convert", str(markdown_file), str(target),
            "--replace", "{{client}}=Acme",
            "--replace", "Report=Summary",
        ])
        text = target.read_text(encoding="utf-8")
        assert "Acme" in text and "Summary" in text

    def test_regex_replacement(self, markdown_file, tmp_path):
        target = tmp_path / "out.md"
        main([
            "convert", str(markdown_file), str(target),
            "--replace", r"\{\{\w+\}\}=REDACTED", "--regex",
        ])
        assert "REDACTED" in target.read_text(encoding="utf-8")

    def test_batch_with_outdir(self, tmp_path):
        for name in ("a", "b", "c"):
            (tmp_path / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        outdir = tmp_path / "out"
        code = main([
            "convert",
            str(tmp_path / "a.md"), str(tmp_path / "b.md"), str(tmp_path / "c.md"),
            "--to", "txt", "--outdir", str(outdir),
        ])
        assert code == 0
        assert sorted(p.name for p in outdir.glob("*.txt")) == ["a.txt", "b.txt", "c.txt"]

    def test_batch_needs_a_target_format(self, tmp_path, capsys):
        for name in ("a", "b", "c"):
            (tmp_path / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        outdir = tmp_path / "out"
        outdir.mkdir()
        code = main([
            "convert",
            str(tmp_path / "a.md"), str(tmp_path / "b.md"), str(outdir),
        ])
        assert code == 2
        assert "--to" in capsys.readouterr().err

    def test_two_paths_means_source_then_target(self, markdown_file, tmp_path):
        target = tmp_path / "explicit.txt"
        assert main(["convert", str(markdown_file), str(target)]) == 0
        assert target.exists()

    def test_existing_directory_as_last_path_is_an_outdir(self, tmp_path):
        (tmp_path / "a.md").write_text("# a\n", encoding="utf-8")
        outdir = tmp_path / "out"
        outdir.mkdir()
        assert main(["convert", str(tmp_path / "a.md"), str(outdir), "--to", "txt"]) == 0
        assert (outdir / "a.txt").exists()

    def test_single_path_with_to_derives_the_name(self, tmp_path):
        source = tmp_path / "a.md"
        source.write_text("# a\n", encoding="utf-8")
        assert main(["convert", str(source), "--to", "txt"]) == 0
        assert (tmp_path / "a.txt").exists()

    def test_single_path_without_to_is_rejected(self, tmp_path, capsys):
        source = tmp_path / "a.md"
        source.write_text("# a\n", encoding="utf-8")
        assert main(["convert", str(source)]) == 2
        assert "--to" in capsys.readouterr().err

    def test_write_option_is_forwarded(self, markdown_file, tmp_path):
        target = tmp_path / "out.html"
        main(["convert", str(markdown_file), str(target), "--write-opt", "standalone=false"])
        assert not target.read_text(encoding="utf-8").startswith("<!DOCTYPE")

    def test_read_option_is_forwarded(self, tmp_path):
        source = tmp_path / "list.txt"
        source.write_text("- one\n- two\n", encoding="utf-8")
        target = tmp_path / "out.json"
        main([
            "convert", str(source), str(target),
            "--read-opt", "detect_structure=false",
        ])
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["body"][0]["type"] == "paragraph"

    def test_missing_source_reports_cleanly(self, tmp_path, capsys):
        assert main(["convert", str(tmp_path / "nope.md"), str(tmp_path / "o.txt")]) == 2
        assert "No such file" in capsys.readouterr().err

    def test_bad_option_syntax(self, markdown_file, tmp_path, capsys):
        code = main([
            "convert", str(markdown_file), str(tmp_path / "o.txt"), "--read-opt", "novalue",
        ])
        assert code == 2
        assert "key=value" in capsys.readouterr().err

    def test_unsupported_target_reports_cleanly(self, markdown_file, tmp_path, capsys):
        code = main(["convert", str(markdown_file), str(tmp_path / "o.xyz")])
        assert code != 0
        assert capsys.readouterr().err


class TestInspect:
    def test_human_output(self, markdown_file, capsys):
        assert main(["inspect", str(markdown_file)]) == 0
        out = capsys.readouterr().out
        assert "markdown" in out
        assert "Report" in out
        assert "words" in out

    def test_json_output(self, markdown_file, capsys):
        assert main(["inspect", str(markdown_file), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["source_format"] == "markdown"
        assert payload["outline"][0]["title"] == "Report"

    def test_outline_is_shown(self, markdown_file, capsys):
        main(["inspect", str(markdown_file)])
        assert "Data" in capsys.readouterr().out


class TestExtract:
    def test_plain_text(self, markdown_file, capsys):
        assert main(["extract", str(markdown_file)]) == 0
        assert "Body for" in capsys.readouterr().out

    def test_markdown(self, markdown_file, capsys):
        main(["extract", str(markdown_file), "--format", "markdown"])
        assert "# Report" in capsys.readouterr().out

    def test_json(self, markdown_file, capsys):
        main(["extract", str(markdown_file), "--format", "json"])
        assert json.loads(capsys.readouterr().out)["type"] == "document"

    def test_tables_as_csv(self, markdown_file, capsys):
        assert main(["extract", str(markdown_file), "--tables"]) == 0
        assert "a,b" in capsys.readouterr().out

    def test_tables_as_json(self, markdown_file, capsys):
        main(["extract", str(markdown_file), "--tables", "--format", "json"])
        assert json.loads(capsys.readouterr().out) == [[["a", "b"], ["1", "2"]]]

    def test_no_tables_reports_and_fails(self, tmp_path, capsys):
        source = tmp_path / "plain.md"
        source.write_text("# Only prose\n", encoding="utf-8")
        assert main(["extract", str(source), "--tables"]) == 1
        assert "No tables" in capsys.readouterr().err

    def test_outline(self, markdown_file, capsys):
        main(["extract", str(markdown_file), "--outline"])
        out = capsys.readouterr().out
        assert "Report" in out and "Data" in out


class TestEdit:
    def test_replace_and_write(self, markdown_file, tmp_path, capsys):
        target = tmp_path / "edited.md"
        assert main([
            "edit", str(markdown_file), str(target), "--replace", "{{client}}=Acme",
        ]) == 0
        assert "Acme" in target.read_text(encoding="utf-8")
        assert "change" in capsys.readouterr().out

    def test_remove_by_selector(self, markdown_file, tmp_path):
        target = tmp_path / "edited.md"
        main(["edit", str(markdown_file), str(target), "--remove", "table"])
        assert "| a" not in target.read_text(encoding="utf-8")

    def test_reports_change_count(self, markdown_file, tmp_path, capsys):
        main([
            "edit", str(markdown_file), str(tmp_path / "e.md"),
            "--replace", "{{client}}=A",
        ])
        assert "1 change" in capsys.readouterr().out

    @needs_docx
    def test_cross_format_edit(self, markdown_file, tmp_path):
        import polydoc

        target = tmp_path / "edited.docx"
        main(["edit", str(markdown_file), str(target), "--replace", "{{client}}=Acme"])
        assert "Acme" in polydoc.open(target).text


class TestFormats:
    def test_table_output(self, capsys):
        assert main(["formats"]) == 0
        out = capsys.readouterr().out
        assert "markdown" in out and "docx" in out
        assert "read" in out and "write" in out

    def test_json_output(self, capsys):
        assert main(["formats", "--json"]) == 0
        rows = {row["format"]: row for row in json.loads(capsys.readouterr().out)}
        assert rows["markdown"]["read"] is True


class TestDetect:
    def test_reports_resolution(self, markdown_file, capsys):
        assert main(["detect", str(markdown_file)]) == 0
        out = capsys.readouterr().out
        assert "markdown" in out
        assert "resolved" in out

    def test_multiple_files(self, tmp_path, capsys):
        (tmp_path / "a.md").write_text("# a\n", encoding="utf-8")
        (tmp_path / "b.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        assert main(["detect", str(tmp_path / "a.md"), str(tmp_path / "b.csv")]) == 0
        out = capsys.readouterr().out
        assert "markdown" in out and "csv" in out


class TestOptionCoercion:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("true", True),
            ("False", False),
            ("none", None),
            ("42", 42),
            ("-7", -7),
            ("3.5", 3.5),
            ("2-7", (2, 7)),
            ("a,b", ["a", "b"]),
            ("plain", "plain"),
            ('{"k": 1}', {"k": 1}),
            ("[1, 2]", [1, 2]),
        ],
    )
    def test_values(self, given, expected):
        from polydoc.cli import _coerce

        assert _coerce(given) == expected
