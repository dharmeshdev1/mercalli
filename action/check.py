"""Check services for open incidents using only the standard library, for the GitHub Action."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import urllib.request

RANK = {"none": 0, "maintenance": 0, "minor": 1, "major": 2, "critical": 3}


def clean(text: object) -> str:
    """Vendor text on one line, unable to start a workflow command or end an output block."""
    one_line = re.sub(r"\s+", " ", str(text)).strip()
    return one_line.replace("::", ": :")[:300]


def main() -> int:
    api = os.environ.get("MERCALLI_API", "https://dharmeshdev1.github.io/mercalli/api/v1").rstrip("/")
    wanted = [s.strip().lower() for s in os.environ.get("MERCALLI_SERVICES", "").split(",") if s.strip()]
    fail_on = os.environ.get("MERCALLI_FAIL_ON", "major").lower()
    req = urllib.request.Request(f"{api}/overview.json", headers={"User-Agent": "mercalli-action/1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        ov = json.load(resp)
    board = {s["id"]: s for s in ov["services"]}
    incidents = ov["active"] + ov.get("long_running", [])
    lines, worst = [], 0
    for sid in wanted:
        svc = board.get(sid)
        if svc is None:
            print(f"::warning::Mercalli does not watch a service with id '{clean(sid)}'")
            continue
        for inc in (i for i in incidents if i["service_id"] == sid):
            worst = max(worst, RANK.get(inc["impact"], 0))
            f = inc.get("forecast")
            eta = f", likely resolved by {f['eta_p50']}" if f else ""
            state = f"{clean(inc['impact'])}, {clean(inc['status'])}{clean(eta)}"
            lines.append(f"{clean(svc['name'])}: {clean(inc['title'])} ({state})")
    for line in lines:
        print(line)
    if not lines:
        print(f"No open incidents for {', '.join(wanted)} as of {ov['generated_at']}.")
    threshold = RANK.get(fail_on)
    healthy = threshold is None or worst < threshold
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as out:
        out.write(f"healthy={'true' if healthy else 'false'}\n")
        # A random delimiter, so no vendor text can close the block early and inject outputs.
        delimiter = f"MERCALLI_{secrets.token_hex(16)}"
        out.write(f"report<<{delimiter}\n" + "\n".join(lines) + f"\n{delimiter}\n")
    if not healthy:
        print(f"::error::A service you depend on has an open {fail_on} or worse incident.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
