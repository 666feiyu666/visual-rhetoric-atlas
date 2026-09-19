"""Command line interface for the Data-Information-Knowledge corpus pipeline."""

import argparse
import json
from pathlib import Path

from .data.wikimedia import DEFAULT_CATEGORY, DEFAULT_CONTACT, CommonsClient, discover, download_records, read_manifest, write_reports
from .information import build_information


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = PROJECT_ROOT / "corpus" / "mucha" / "wikimedia"
DEFAULT_INFORMATION = PROJECT_ROOT / "corpus" / "mucha" / "information"
DEFAULT_EXISTING = PROJECT_ROOT / "corpus" / "mucha"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    stages = parser.add_subparsers(dest="stage", required=True)

    data = stages.add_parser("data", help="Acquire and verify raw source material.")
    data_commands = data.add_subparsers(dest="command", required=True)
    for name in ("discover", "refresh", "download", "report"):
        command = data_commands.add_parser(name)
        command.add_argument("--output-dir", type=Path, default=DEFAULT_DATA)
        if name in {"discover", "refresh"}:
            command.add_argument("--category", default=DEFAULT_CATEGORY)
            command.add_argument("--existing-dir", type=Path, default=DEFAULT_EXISTING)
            command.add_argument("--limit", type=int)
            command.add_argument("--contact", default=DEFAULT_CONTACT)

    information = stages.add_parser("information", help="Encode raw data as comparable information.")
    information_commands = information.add_subparsers(dest="command", required=True)
    build = information_commands.add_parser("build")
    build.add_argument("--manifest", type=Path, default=DEFAULT_DATA / "manifest.jsonl")
    build.add_argument("--output-dir", type=Path, default=DEFAULT_INFORMATION)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.stage == "information":
        summary = build_information(args.manifest, args.output_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    output = args.output_dir.resolve()
    if args.command in {"discover", "refresh"}:
        client = CommonsClient(contact=args.contact)
        records = discover(client, output, category=args.category, limit=args.limit,
                           existing_dir=args.existing_dir.resolve())
    elif args.command == "download":
        records = download_records(CommonsClient(), output)
    else:
        records = read_manifest(output / "manifest.jsonl")
        write_reports(output, records)
    statuses = {}
    for record in records:
        status = record.get("download_status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    print(json.dumps({"output": str(output), "total": len(records), "statuses": statuses},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
