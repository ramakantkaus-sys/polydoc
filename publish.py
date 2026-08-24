"""Upload the built artifacts to PyPI.

Reads the API token from .env and never prints it. Separate from ship.py because this
step is irreversible: a version number consumed on PyPI can never be reused, even after
deleting the release.

Usage:
    python publish.py --check     # validate the token and show what would be uploaded
    python publish.py --upload    # actually upload
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"


def read_token() -> str:
    """Extract the PyPI token from .env without echoing it anywhere."""
    if not ENV_FILE.exists():
        raise SystemExit("No .env file found; cannot authenticate.")
    raw = ENV_FILE.read_text(encoding="utf-8").strip()

    # Accept "pypi-...", "Password: pypi-...", "PYPI_TOKEN=pypi-...", or similar.
    match = re.search(r"(pypi-[A-Za-z0-9_\-]+)", raw)
    if not match:
        raise SystemExit(
            "Could not find a token matching 'pypi-...' in .env. "
            "Expected a PyPI API token."
        )
    return match.group(1)


def artifacts() -> list:
    found = sorted((ROOT / "dist").glob("*"))
    if not found:
        raise SystemExit("dist/ is empty. Run 'python ship.py' first.")
    return found


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    token = read_token()
    files = artifacts()

    print("polydoc publish")
    print(f"  token: {len(token)} chars, prefix 'pypi-', suffix '...{token[-4:]}'")
    print("  artifacts to upload:")
    for path in files:
        print(f"    {path.name}  ({path.stat().st_size:,} bytes)")

    # Sanity: refuse to upload anything that is not a wheel or sdist.
    unexpected = [p.name for p in files if p.suffix not in (".whl", ".gz")]
    if unexpected:
        raise SystemExit(f"Unexpected files in dist/: {unexpected}")

    env = {
        **os.environ,
        "TWINE_USERNAME": "__token__",
        "TWINE_PASSWORD": token,
        "TWINE_NON_INTERACTIVE": "1",
    }

    print("\n  validating metadata...")
    result = subprocess.run(
        [sys.executable, "-m", "twine", "check", *[str(p) for p in files]],
        capture_output=True, text=True, cwd=ROOT,
    )
    print("   ", (result.stdout or "").strip().replace("\n", "\n    "))
    if result.returncode != 0:
        raise SystemExit("twine check failed; not uploading.")

    if mode != "--upload":
        print("\n  --check only. Re-run with --upload to publish.")
        return 0

    print("\n  uploading to PyPI (irreversible)...")
    result = subprocess.run(
        [sys.executable, "-m", "twine", "upload", "--non-interactive",
         *[str(p) for p in files]],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    # Scrub the token from any echoed output, belt and braces.
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).replace(token, "<REDACTED>")
    print(output.strip())
    if result.returncode != 0:
        print("\n  UPLOAD FAILED")
        return result.returncode
    print("\n  uploaded successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
