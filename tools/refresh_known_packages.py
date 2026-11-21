"""Rewrite dscforge/known_packages.txt from the public "top PyPI packages by downloads" list.

Usage (needs the network; only the maintainer runs this, the installed package never does):
    python tools/refresh_known_packages.py [--top 1500]

Source: https://hugovk.dev/top-pypi-packages/ (monthly download counts from PyPI's public data).
Only names that look like a normal package name are kept. Names are normalised like pip does
(lowercase, runs of - _ . become -).
"""
import argparse
import datetime
import json
import re
import urllib.request
from pathlib import Path

URL = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
OUT = Path(__file__).resolve().parent.parent / "dscforge" / "known_packages.txt"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=1500, help="how many of the most downloaded packages to keep")
    args = ap.parse_args()
    with urllib.request.urlopen(URL, timeout=60) as resp:
        data = json.load(resp)
    names = []
    for row in data["rows"]:
        name = re.sub(r"[-_.]+", "-", str(row.get("project", ""))).lower()
        if NAME.match(name) and name not in names:
            names.append(name)
        if len(names) == args.top:
            break
    header = [
        "# Packages that --run may install when a project declares no requirements and only its imports hint at them.",
        f"# The {len(names)} most downloaded PyPI packages, most downloaded first. Source: {URL}",
        f"# (data of {data.get('last_update', '?')}), written by tools/refresh_known_packages.py on {datetime.date.today()}.",
        "# One name per line, lowercase. Lines starting with # are ignored.",
    ]
    OUT.write_text("\n".join(header + names) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} names to {OUT}")


if __name__ == "__main__":
    main()
