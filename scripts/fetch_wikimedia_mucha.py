"""Collect Alphonse Mucha files and provenance from Wikimedia Commons."""

import argparse
import json
from pathlib import Path

from visual_rhetoric_atlas.data.wikimedia import (
    DEFAULT_CATEGORY,
    DEFAULT_CONTACT,
    CommonsClient,
    discover,
    download_records,
    read_manifest,
    write_reports,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "corpus" / "mucha" / "wikimedia"
DEFAULT_EXISTING = PROJECT_ROOT / "corpus" / "mucha"


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("discover", "download", "refresh", "report"))
    value.add_argument("--category", default=DEFAULT_CATEGORY)
    value.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    value.add_argument("--existing-dir", type=Path, default=DEFAULT_EXISTING)
    value.add_argument("--contact", default=DEFAULT_CONTACT,
                       help="Contact URL or email included in the Wikimedia User-Agent.")
    value.add_argument("--limit", type=int, help="Only discover this many files (for testing).")
    value.add_argument("--timeout", type=int, default=60)
    value.add_argument("--retries", type=int, default=4)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    output = args.output_dir.resolve()
    if args.command == "report":
        records = read_manifest(output / "manifest.jsonl")
        if not records:
            raise SystemExit("No manifest found. Run discover first.")
        write_reports(output, records)
    else:
        client = CommonsClient(contact=args.contact, timeout=args.timeout, retries=args.retries)
        if args.command in {"discover", "refresh"}:
            records = discover(client, output, category=args.category, limit=args.limit,
                               existing_dir=args.existing_dir.resolve(),
                               refresh=args.command == "refresh")
        else:
            records = download_records(client, output)
    counts = {}
    for record in records:
        status = record.get("download_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"output": str(output), "total": len(records), "statuses": counts},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
