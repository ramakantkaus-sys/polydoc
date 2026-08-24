"""Show the failing lines from a CI job's log. Local helper."""

import json
import re
import subprocess
import sys

want = sys.argv[1] if len(sys.argv) > 1 else "ubuntu"

listing = subprocess.run(
    ["gh", "run", "list", "--limit", "1", "--json", "databaseId"],
    capture_output=True, text=True, shell=True,
)
run_id = str(json.loads(listing.stdout)[0]["databaseId"])

result = subprocess.run(
    ["gh", "run", "view", run_id, "--json", "jobs"],
    capture_output=True, text=True, shell=True,
)
jobs = json.loads(result.stdout)["jobs"]
target = next(
    (j for j in jobs if want in j["name"] and j.get("conclusion") == "failure"), None
)
if target is None:
    print(f"no failing job matching {want!r}")
    print("available:", [j["name"] for j in jobs])
    sys.exit(1)

print(f"job: {target['name']}\n")
result = subprocess.run(
    ["gh", "run", "view", "--job", str(target["databaseId"]), "--log-failed"],
    capture_output=True, text=True, shell=True,
)
lines = []
for line in result.stdout.splitlines():
    parts = line.split("\t")
    lines.append(parts[-1] if len(parts) > 1 else line)

interesting = [
    line for line in lines
    if re.search(r"^E\s|Error|error:|FAILED|short test summary|assert|ModuleNotFound"
                 r"|ImportError|SyntaxError|-->", line)
]
for line in interesting[:45]:
    print(line[:190])
