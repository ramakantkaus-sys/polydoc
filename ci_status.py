"""Summarise the latest CI run. Local helper; not part of the package."""

import json
import subprocess
import sys

listing = subprocess.run(
    ["gh", "run", "list", "--limit", "3", "--json",
     "databaseId,headSha,status,conclusion,displayTitle"],
    capture_output=True, text=True, shell=True,
)
runs = json.loads(listing.stdout)
for entry in runs:
    print(f"{entry['status']:12} {str(entry['conclusion']):8} "
          f"{entry['headSha'][:8]}  {entry['displayTitle'][:56]}")

run_id = sys.argv[1] if len(sys.argv) > 1 else str(runs[0]["databaseId"])
result = subprocess.run(
    ["gh", "run", "view", run_id, "--json", "jobs"],
    capture_output=True, text=True, shell=True,
)
jobs = json.loads(result.stdout)["jobs"]
print()
tally = {}
for job in sorted(jobs, key=lambda j: j["name"]):
    state = job.get("conclusion") or job.get("status")
    tally[state] = tally.get(state, 0) + 1
    print(f"  {str(state):12} {job['name']}")
print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
