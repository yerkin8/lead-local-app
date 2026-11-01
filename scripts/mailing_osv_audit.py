"""Check pinned requirements against the OSV database (same data source as pip-audit).

Usage: python scripts/mailing_osv_audit.py requirements-exe-lock.txt
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

URL = "https://api.osv.dev/v1/querybatch"


def pins(path: str) -> list[tuple[str, str]]:
    items = []
    for line in open(path, encoding="utf-8"):
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s;#]+)", line)
        if match:
            items.append((match.group(1), match.group(2)))
    return items


def main(path: str) -> int:
    packages = pins(path)
    body = {"queries": [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
                        for name, version in packages]}
    request = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        results = json.load(response)["results"]
    found = 0
    for (name, version), result in zip(packages, results):
        ids = [item["id"] for item in result.get("vulns", [])]
        if ids:
            found += 1
            print(f"VULN {name}=={version}: {', '.join(ids)}")
    print(f"checked={len(packages)} vulnerable_packages={found} source=api.osv.dev")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
