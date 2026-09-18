"""
ReLink Studio CLI (section 17) -- covers the commands that operate purely
on local files and the standalone modules added in batches 1-5, without
touching routes.py/db.py (that wiring is still pending -- see README).

Usage:
    python -m app.cli_tools profile <file>
    python -m app.cli_tools validate-config <config.json>
    python -m app.cli_tools schema-diff <old_schema.json> <new_schema.json>
    python -m app.cli_tools check-capabilities

Every command prints human-readable output by default; pass --json for
machine-readable output. Exit codes: 0 success, 1 validation/user error,
2 unexpected failure. No credentials are ever printed (see
connectors.redact_connection_config).
"""
import argparse
import json
import sys

from .parsers import parse_file, ParseError
from .profiling import profile_dataset
from .schema_tools import discover_schema, diff_schema
from .connectors import list_capabilities

REQUIRED_CONFIG_KEYS = ("project_name", "source", "target", "methods")


def cmd_profile(args):
    try:
        parsed = parse_file(args.file, args.file)
    except ParseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    result = profile_dataset(parsed["columns"], parsed["rows"])
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"{args.file}: {result['row_count']} rows, {result['column_count']} columns, "
              f"{result['duplicate_row_count']} duplicate row(s)")
        for col in result["columns"]:
            flag = " ⚠" if col["warnings"] else ""
            print(f"  {col['column']:<24} null={col['null_pct']:>5}%  distinct={col['distinct_count']:<6}{flag}")
            for w in col["warnings"]:
                print(f"    - {w}")
    return 0


def cmd_validate_config(args):
    try:
        with open(args.config) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read {args.config}: {e}", file=sys.stderr)
        return 1
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        print(f"invalid: missing required key(s): {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"valid: {args.config} has all required keys ({', '.join(REQUIRED_CONFIG_KEYS)})")
    return 0


def cmd_schema_diff(args):
    try:
        with open(args.old) as f:
            old_parsed = json.load(f)
        with open(args.new) as f:
            new_parsed = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    old_schema = discover_schema(old_parsed["columns"], old_parsed["rows"], old_parsed.get("geometry"))
    new_schema = discover_schema(new_parsed["columns"], new_parsed["rows"], new_parsed.get("geometry"))
    diff = diff_schema(old_schema, new_schema)
    if args.json:
        print(json.dumps(diff, indent=2))
    elif diff["has_drift"]:
        print("DRIFT DETECTED:")
        if diff["added_fields"]:
            print(f"  added: {diff['added_fields']}")
        if diff["removed_fields"]:
            print(f"  removed: {diff['removed_fields']}")
        if diff["type_changes"]:
            print(f"  type changes: {diff['type_changes']}")
    else:
        print("no drift detected")
    return 1 if diff["has_drift"] else 0


def cmd_check_capabilities(args):
    caps = list_capabilities()
    if args.json:
        print(json.dumps(caps, indent=2))
    else:
        for name, cap in caps.items():
            status = "available" if cap["available"] else f"unavailable ({cap['reason']})"
            print(f"  {name:<12} {status}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="relink-cli", description="ReLink Studio local CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profile", help="Profile a local CSV/XLSX/GeoJSON file")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("validate-config", help="Validate a matching-config JSON file's shape")
    p.add_argument("config")
    p.set_defaults(func=cmd_validate_config)

    p = sub.add_parser("schema-diff", help="Compare two parsed-file JSON payloads for schema drift")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_schema_diff)

    p = sub.add_parser("check-capabilities", help="Show which optional connectors are installed")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check_capabilities)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # last-resort guard so the CLI never dumps a raw traceback
        print(f"unexpected error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
