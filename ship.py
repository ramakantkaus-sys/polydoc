"""Release helper: verify, build, and confirm the artifacts are clean.

Kept deliberately verbose because this runs immediately before an irreversible upload.
Publishing is a separate, explicit step -- this script never uploads.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
FAILURES: list = []


def step(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def run(
    args: list,
    env_extra: dict | None = None,
    cwd: Path | None = None,
    clean_path: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command, capturing output.

    ``clean_path=True`` strips PYTHONPATH from the child environment. That matters a
    great deal here: with ``PYTHONPATH=src`` inherited from a development shell, a venv's
    python happily imports the *source tree* instead of the installed package, and the
    whole point of this step is to test the installed artifact.
    """
    env = {**os.environ, **(env_extra or {})}
    if clean_path:
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd or ROOT)


# ---------------------------------------------------------------------------
step("1. Test suite (against the source tree)")
result = run(
    [sys.executable, "-m", "pytest", "tests/", "-o", "addopts=", "-q", "--tb=short",
     "-p", "no:cacheprovider"],
    {"PYTHONPATH": "src"},
)
summary = [ln for ln in result.stdout.splitlines() if "passed" in ln or "failed" in ln]
check("all tests pass", result.returncode == 0, summary[-1] if summary else "")
if result.returncode != 0:
    print(result.stdout[-3000:])

# ---------------------------------------------------------------------------
step("2. Clean the build directories")
for name in ("dist", "build"):
    target = ROOT / name
    if target.exists():
        import shutil

        shutil.rmtree(target, ignore_errors=True)
for egg in ROOT.glob("src/*.egg-info"):
    import shutil

    shutil.rmtree(egg, ignore_errors=True)
check("dist/ and build/ removed", not (ROOT / "dist").exists())

# ---------------------------------------------------------------------------
step("3. Build sdist and wheel")
result = run([sys.executable, "-m", "build"])
built = sorted(p.name for p in (ROOT / "dist").glob("*")) if (ROOT / "dist").exists() else []
check("build succeeded", result.returncode == 0 and len(built) == 2, ", ".join(built))
if result.returncode != 0:
    print(result.stdout[-2500:])
    print(result.stderr[-2500:])

# ---------------------------------------------------------------------------
step("4. Validate metadata (twine check)")
result = run([sys.executable, "-m", "twine", "check", "dist/*"])
output = result.stdout + result.stderr
check("twine check passes", "PASSED" in output and "FAILED" not in output,
      output.strip().splitlines()[-1] if output.strip() else "")

# ---------------------------------------------------------------------------
step("5. Confirm no secrets in the artifacts")
FORBIDDEN_NAMES = (".env", ".pypirc", "secrets")
FORBIDDEN_CONTENT = (b"pypi-", b"gho_", b"ghp_")

for archive_path in sorted((ROOT / "dist").glob("*")):
    if archive_path.suffix == ".whl":
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            readers = {n: archive.read(n) for n in names if n.endswith((".py", ".txt", ".md"))}
    else:
        with tarfile.open(archive_path) as archive:
            names = archive.getnames()
            readers = {}
            for member in archive.getmembers():
                if member.isfile() and member.name.endswith((".py", ".txt", ".md")):
                    handle = archive.extractfile(member)
                    if handle:
                        readers[member.name] = handle.read()

    leaked_names = [n for n in names if any(bad in Path(n).name.lower() for bad in FORBIDDEN_NAMES)]
    check(f"{archive_path.name}: no secret filenames", not leaked_names, str(leaked_names))

    leaked_content = [
        name for name, data in readers.items()
        if any(token in data for token in FORBIDDEN_CONTENT)
    ]
    check(f"{archive_path.name}: no token-like strings", not leaked_content, str(leaked_content[:3]))

    check(f"{archive_path.name}: py.typed included",
          any("py.typed" in n for n in names))
    check(f"{archive_path.name}: LICENSE included",
          any("LICENSE" in n for n in names))

# ---------------------------------------------------------------------------
step("6. Install the wheel into a clean venv and smoke test")
import tempfile

venv_dir = Path(tempfile.mkdtemp(prefix="polydoc_release_")) / "venv"
print(f"  venv: {venv_dir}")
result = run([sys.executable, "-m", "venv", str(venv_dir)])
check("venv created", result.returncode == 0)

python_bin = venv_dir / ("Scripts" if os.name == "nt" else "bin") / (
    "python.exe" if os.name == "nt" else "python"
)
wheel = next((ROOT / "dist").glob("*.whl"), None)
if wheel and python_bin.exists():
    print(f"  installing {wheel.name}[all] (pulls ~20 packages, please wait)...", flush=True)
    result = run(
        [str(python_bin), "-m", "pip", "install", "--no-input",
         "--disable-pip-version-check", f"{wheel}[all]"],
        clean_path=True,
    )
    tail = (result.stderr or result.stdout or "").strip().splitlines()
    check("pip install returns success", result.returncode == 0,
          tail[-1][:110] if tail else "")

    site_packages = list(venv_dir.glob("**/site-packages/polydoc"))
    check("package present in site-packages", bool(site_packages),
          str(site_packages[0]) if site_packages else "NOT FOUND")

    dist_info = list(venv_dir.glob("**/site-packages/polydoc-*.dist-info"))
    check("dist-info present", bool(dist_info),
          dist_info[0].name if dist_info else "NOT FOUND")

    # Import from a neutral directory with PYTHONPATH scrubbed, then assert the module
    # actually resolved inside the venv rather than to the source tree.
    neutral = Path(tempfile.mkdtemp(prefix="polydoc_neutral_"))
    installed = run(
        [str(python_bin), "-c",
         "import polydoc, pathlib; print(pathlib.Path(polydoc.__file__).parent)"],
        cwd=neutral,
        clean_path=True,
    )
    resolved = installed.stdout.strip()
    check("polydoc imports in the venv", installed.returncode == 0,
          resolved or (installed.stderr or "").strip()[:120])
    check("import resolves INSIDE the venv, not the source tree",
          bool(resolved) and str(venv_dir).lower() in resolved.lower(),
          resolved)

    # Run the smoke test from a directory that is not the source tree.
    workdir = Path(tempfile.mkdtemp(prefix="polydoc_smoke_"))
    result = run(
        [str(python_bin), str(ROOT / "scripts" / "smoke_test.py")],
        cwd=workdir,
        clean_path=True,
    )
    tail = [ln for ln in result.stdout.splitlines() if ln.strip()][-1:] if result.stdout else []
    check("smoke test passes on the installed wheel", result.returncode == 0,
          tail[0] if tail else "(no output)")
    if result.returncode != 0:
        print("  --- smoke stdout ---")
        print((result.stdout or "")[-2500:])
        print("  --- smoke stderr ---")
        print((result.stderr or "")[-2500:])

    console = venv_dir / ("Scripts" if os.name == "nt" else "bin") / (
        "polydoc.exe" if os.name == "nt" else "polydoc"
    )
    check("console script installed", console.exists(), console.name)
    if console.exists():
        result = run([str(console), "--version"], cwd=neutral, clean_path=True)
        check("console script runs", result.returncode == 0, result.stdout.strip())
        result = run([str(console), "formats"], cwd=neutral, clean_path=True)
        check("console script lists 9 formats",
              result.returncode == 0 and result.stdout.count("yes   yes") >= 9,
              f"{result.stdout.count('yes')} capability cells")

# ---------------------------------------------------------------------------
step("Result")
if FAILURES:
    print(f"\n{len(FAILURES)} check(s) FAILED -- do not publish:")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)

print("\nAll release checks passed. Artifacts in dist/:")
for path in sorted((ROOT / "dist").glob("*")):
    print(f"  {path.name}  ({path.stat().st_size:,} bytes)")
print("\nPublishing is a separate step; this script does not upload.")
