"""Final acceptance: install polydoc from PyPI as any user would, and exercise it.

Tests both paths:
  1. `pip install polydoc`        - core only, must handle Markdown/HTML/text/JSON/CSV
  2. `pip install polydoc[all]`   - every format

Nothing here touches the local source tree; PYTHONPATH is scrubbed for every child.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FAILURES: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def clean_env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def make_venv(label: str, spec: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"polydoc_final_{label}_"))
    venv = root / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                   capture_output=True, env=clean_env())
    python = venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    print(f"\n  installing '{spec}' from PyPI...", flush=True)
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", spec],
        capture_output=True, text=True, env=clean_env(),
    )
    check(f"pip install {spec}", result.returncode == 0,
          (result.stderr or "").strip().splitlines()[-1][:100] if result.returncode else "")
    return python


def run_py(python: Path, code: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(python), "-c", code], capture_output=True, text=True,
                          cwd=cwd, env=clean_env())


work = Path(tempfile.mkdtemp(prefix="polydoc_final_work_"))
print(f"polydoc final acceptance check\n  working in {work}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 68)
print("A. Core install: pip install polydoc")
print("=" * 68)

core = make_venv("core", "polydoc")

result = run_py(core, "import polydoc; print(polydoc.__version__)", work)
check("version is 0.1.1", result.stdout.strip() == "0.1.1", result.stdout.strip())

result = run_py(core, "import polydoc; print(','.join(polydoc.formats('read')))", work)
check("all 9 formats registered", result.stdout.strip().count(",") == 8, result.stdout.strip())

# The whole point of 0.1.1: these must work with no extras installed.
CORE_EXERCISE = """
import polydoc
doc = polydoc.loads('# Title\\n\\nBody **bold** text.\\n\\n| a | b |\\n| - | - |\\n| 1 | 2 |\\n', 'markdown')
assert doc.headings[0].text == 'Title', doc.headings
assert doc.tables[0].to_matrix() == [['a','b'],['1','2']], doc.tables[0].to_matrix()
doc.replace_text('Body', 'Content')
assert 'Content' in doc.text
html = polydoc.loads('<h1>T</h1><p>x <b>y</b></p>', 'html')
assert html.headings[0].text == 'T'
for fmt in ('markdown', 'html', 'txt', 'json', 'csv'):
    assert polydoc.dumps(doc, fmt), fmt
print('core formats all work')
"""
result = run_py(core, CORE_EXERCISE, work)
check("Markdown + HTML + text + JSON + CSV work with no extras",
      result.returncode == 0,
      result.stdout.strip() or (result.stderr or "").strip().splitlines()[-1][:110])

# And the absent backends must fail with an actionable message, not a crash.
ABSENT = """
import polydoc
from polydoc import MissingDependencyError
try:
    polydoc.loads('x', 'docx')
except MissingDependencyError as exc:
    assert 'pip install' in str(exc), exc
    print('actionable:', str(exc)[:60])
except Exception as exc:
    raise SystemExit(f'wrong error type: {type(exc).__name__}: {exc}')
"""
result = run_py(core, ABSENT, work)
check("absent backend gives an actionable install hint", result.returncode == 0,
      result.stdout.strip() or (result.stderr or "").strip()[:110])

# ---------------------------------------------------------------------------
print("\n" + "=" * 68)
print("B. Full install: pip install polydoc[all]")
print("=" * 68)

full = make_venv("all", "polydoc[all]")

result = run_py(full, "import polydoc; print(polydoc.__version__)", work)
check("version is 0.1.1", result.stdout.strip() == "0.1.1", result.stdout.strip())

REAL_TASK = """
import polydoc
from pathlib import Path

# Fill a template, render to PDF, read it back, and pull the table out again.
Path('contract.md').write_text(
    '# Agreement with {{client}}\\n\\n'
    'Terms for {{client}} are set out below.\\n\\n'
    '| Item | Cost |\\n| --- | --- |\\n| Setup | 500 |\\n| Monthly | 120 |\\n',
    encoding='utf-8')

polydoc.convert('contract.md', 'agreement.pdf',
                transform=lambda d: d.replace_text('{{client}}', 'Acme Ltd'))
back = polydoc.open('agreement.pdf')
assert 'Acme Ltd' in back.text, back.text[:120]
assert '{{client}}' not in back.text
assert back.tables, 'no table recovered from the PDF'
assert back.tables[0].to_matrix()[0] == ['Item', 'Cost'], back.tables[0].to_matrix()

# Round-trip through every writer.
for fmt, ext in [('markdown','.md'),('html','.html'),('txt','.txt'),('json','.json'),
                 ('csv','.csv'),('docx','.docx'),('pptx','.pptx'),('xlsx','.xlsx'),
                 ('pdf','.pdf')]:
    out = f'rt{ext}'
    back.save(out, format=fmt)
    assert Path(out).stat().st_size > 0, fmt

# The differentiator: replacement across styled run boundaries.
from polydoc.model import Document, Paragraph, Text, TextStyle
d = Document([Paragraph([Text('Period: '), Text('FY', TextStyle(bold=True)),
                         Text('2024', TextStyle(bold=True, italic=True)), Text(' Q3.')])])
assert d.replace_text('FY2024 Q3', 'FY2025 Q1') == 1
assert d.text == 'Period: FY2025 Q1.', d.text
assert any(r.style.bold and 'FY2025' in r.text for r in d.body[0].content)

# Resource limits are active by default.
from polydoc import DocumentTooLargeError
try:
    polydoc.open(b'x' * 100, format='txt', max_input_bytes=10)
    raise SystemExit('limit not enforced')
except DocumentTooLargeError:
    pass

print('OK')
"""
result = run_py(full, REAL_TASK, work)
check("end-to-end template -> PDF -> read back -> all writers",
      result.returncode == 0 and "OK" in result.stdout,
      result.stdout.strip() or (result.stderr or "").strip().splitlines()[-1][:130])

console = full.parent / ("polydoc.exe" if os.name == "nt" else "polydoc")
check("console script present", console.exists(), console.name)
if console.exists():
    result = subprocess.run([str(console), "--version"], capture_output=True, text=True,
                            cwd=work, env=clean_env())
    check("polydoc --version", "0.1.1" in result.stdout, result.stdout.strip())
    result = subprocess.run([str(console), "convert", "contract.md", "out.docx"],
                            capture_output=True, text=True, cwd=work, env=clean_env())
    check("polydoc convert", result.returncode == 0, result.stdout.strip()[:80])

# ---------------------------------------------------------------------------
print("\n" + "=" * 68)
if FAILURES:
    print(f"{len(FAILURES)} FAILED:")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)
print("ALL CHECKS PASSED - polydoc 0.1.1 is installable and working from PyPI")
shutil.rmtree(work, ignore_errors=True)
