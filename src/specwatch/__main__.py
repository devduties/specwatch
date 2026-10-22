"""Offline compatibility demo for a pair of OpenAPI documents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from specwatch.diffing import compare_specs
from specwatch.specs import parse_spec


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two OpenAPI 3.x documents.")
    parser.add_argument(
        "before", nargs="?", default="examples/before.json", help="Earlier JSON/YAML spec"
    )
    parser.add_argument(
        "after", nargs="?", default="examples/after.json", help="Later JSON/YAML spec"
    )
    args = parser.parse_args()
    before = parse_spec(Path(args.before).read_bytes()).document
    after = parse_spec(Path(args.after).read_bytes()).document
    findings = [item.to_dict() for item in compare_specs(before, after)]
    print(json.dumps(findings, indent=2))
    return 1 if any(item["severity"] == "breaking" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
