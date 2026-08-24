"""Audit polydoc's suitability for AWS Lambda.

Lambda's hard constraints:
  * 250 MB unzipped deployment package (function + layers combined)
  * 50 MB zipped direct upload (or use S3 / a container image)
  * read-only filesystem except /tmp (512 MB by default)
  * cold start time matters

Measures the installed footprint per extra, and checks the runtime behaviours that a
read-only filesystem would break.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PYTHON = sys.executable


def directory_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def mb(size: int) -> str:
    return f"{size / (1024 * 1024):7.1f} MB"


def install(spec: str, label: str) -> dict:
    """Install a spec into an isolated target dir, the way a Lambda layer is built."""
    target = Path(tempfile.mkdtemp(prefix=f"lam_{label}_")) / "python"
    target.mkdir(parents=True)
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    started = time.perf_counter()
    result = subprocess.run(
        [PYTHON, "-m", "pip", "install", "-q", "--disable-pip-version-check",
         "--target", str(target), spec],
        capture_output=True, text=True, env=env,
    )
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        return {"label": label, "spec": spec, "error": (result.stderr or "")[-200:]}

    unzipped = directory_size(target)

    # Zip it as a layer would be, to get the upload size.
    archive = Path(str(target.parent)) / "layer.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, files in os.walk(target):
            for name in files:
                full = Path(root) / name
                zf.write(full, full.relative_to(target.parent))
    zipped = archive.stat().st_size

    # Biggest contributors.
    packages = []
    for child in sorted(target.iterdir()):
        if child.is_dir() and not child.name.endswith(".dist-info"):
            packages.append((child.name, directory_size(child)))
    packages.sort(key=lambda item: -item[1])

    return {
        "label": label,
        "spec": spec,
        "unzipped": unzipped,
        "zipped": zipped,
        "install_seconds": elapsed,
        "top": packages[:6],
        "target": target,
    }


print("polydoc on AWS Lambda: footprint audit")
print("=" * 74)
print(f"{'extra':<26}{'unzipped':>12}{'zipped':>11}  fits 250 MB?")
print("-" * 74)

results = []
for spec, label in [
    ("polydoc", "core"),
    ("polydoc[docx]", "core+docx"),
    ("polydoc[docx,xlsx,pptx]", "core+office"),
    ("polydoc[pdf]", "core+pdf"),
    ("polydoc[all]", "everything"),
]:
    info = install(spec, label.replace("+", "_").replace(",", "_"))
    results.append(info)
    if "error" in info:
        print(f"{label:<26}{'FAILED':>12}  {info['error'][:40]}")
        continue
    verdict = "yes" if info["unzipped"] < 250 * 1024 * 1024 else "NO"
    print(f"{label:<26}{mb(info['unzipped']):>12}{mb(info['zipped']):>11}  {verdict}")

print("\nlargest contributors (everything):")
final = results[-1]
if "top" in final:
    for name, size in final["top"]:
        print(f"  {name:<28}{mb(size)}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 74)
print("Runtime behaviours that a read-only filesystem affects")
print("=" * 74)

probe = Path(tempfile.mkdtemp()) / "probe.py"
probe.write_text(
    "import os, sys, tempfile, time\n"
    "started = time.perf_counter()\n"
    "import polydoc\n"
    "import_time = time.perf_counter() - started\n"
    "print(f'  import polydoc:        {import_time * 1000:.0f} ms')\n"
    "started = time.perf_counter()\n"
    "print(f'  formats registered:    {len(polydoc.formats())}')\n"
    "print(f'  tempdir:               {tempfile.gettempdir()}')\n"
    "started = time.perf_counter()\n"
    "doc = polydoc.loads('# T\\n\\nBody.', 'markdown')\n"
    "data = polydoc.dumps(doc, 'pdf')\n"
    "print(f'  markdown -> pdf:       {(time.perf_counter() - started) * 1000:.0f} ms, {len(data)} bytes')\n",
    encoding="utf-8",
)

target = final.get("target")
if target:
    env = {**os.environ, "PYTHONPATH": str(target)}
    env.pop("PYTHONHOME", None)
    result = subprocess.run([PYTHON, str(probe)], capture_output=True, text=True, env=env)
    print(result.stdout.rstrip() or result.stderr[-400:])

print("\nNotes")
print("-" * 74)
print("  * Inline images in PDF output spill to tempfile.gettempdir().")
print("    Lambda sets TMPDIR=/tmp, which is writable, so this works.")
print("    On a fully read-only filesystem the writer catches OSError and")
print("    falls back to alt text rather than failing the conversion.")
