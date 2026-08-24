# Security Policy

## Supported versions

polydoc is at `0.1.x`. Security fixes land on the latest release only. Pin your version
and upgrade deliberately.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use GitHub's private reporting: **Security → Report a vulnerability** on
<https://github.com/ramakantkaus-sys/polydoc>. Include the input that triggers it (a
minimal file is ideal), the versions of polydoc and Python, and what you observed.

Expect an acknowledgement within a week. Since this is a volunteer project, please allow
90 days before public disclosure.

## Threat model

polydoc parses documents, so **its input is the attack surface**. If you accept uploads
from users, assume every file is hostile.

### What polydoc defends against

Verified by `scripts/hostile_input_probe.py` and `scripts/bomb_probe.py`:

- **Decompression bombs.** Expanded size, compression ratio, and archive entry count are
  screened from the ZIP central directory *before* any backend parses the data, so a bomb
  is rejected without being expanded.
- **XML external entities (XXE).** Verified that DOCX, XLSX, PPTX, and HTML do not resolve
  external entities, so a poisoned document cannot read local files.
- **Entity-expansion (billion laughs).** Blocked by lxml's amplification limit.
- **Unbounded nesting.** Markup nested past `max_nesting_depth` raises
  `DocumentTooLargeError` instead of a bare `RecursionError`.
- **Malformed input.** Truncated and corrupt PDFs, DOCX, and XLSX produce clear errors
  rather than crashes.
- **`file://` references.** An image `src` pointing at a local path is recorded as a
  reference and never dereferenced.

### What polydoc does not defend against

Handle these at the deployment layer:

- **CPU exhaustion.** Limits bound declared sizes, not every pathological parse path.
  Run conversions in a worker with a wall-clock timeout.
- **Memory ceilings.** A source is read fully into memory; there is no streaming mode.
  Set `max_input_bytes` and give the worker its own memory cap (cgroups, container limit,
  or `resource.setrlimit`).
- **Backend vulnerabilities.** polydoc delegates to PyMuPDF, pdfplumber, python-docx,
  python-pptx, openpyxl, lxml, and ReportLab. Keep them patched; subscribe to their
  advisories.
- **Malicious content that is *valid*.** A document containing a phishing link or a macro
  is parsed faithfully. polydoc does not execute macros, but it does not strip them from
  content you pass through either.

### Recommended deployment shape

```python
import polydoc

# Once at startup: a policy appropriate for public uploads.
polydoc.set_default_limits(polydoc.Limits(
    max_input_bytes=25 * 1024 * 1024,
    max_expanded_bytes=64 * 1024 * 1024,
    max_compression_ratio=50,
))
```

Then parse in a subprocess or task worker with a timeout and a memory cap, and catch
`polydoc.PolydocError` at the boundary.

## Not fuzzed

polydoc has not been subjected to coverage-guided fuzzing. The probes cover known attack
classes; they cannot cover unknown ones. Fuzzing contributions are very welcome.
