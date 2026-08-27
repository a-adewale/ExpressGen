"""Command-line interface for ExpressGen."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .generator import ExpressGenerator, load_schema
from .prisma import SchemaError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expressgen",
        description="Generate a deterministic Express/Prisma API from JSON.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="Generate a Node.js project.")
    generate.add_argument("schema", type=Path, help="Path to the JSON schema.")
    generate.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("generated-app"),
        help="Output directory (default: generated-app).",
    )
    generate.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing non-empty output directory.",
    )

    validate = commands.add_parser("validate", help="Validate a schema without writing files.")
    validate.add_argument("schema", type=Path, help="Path to the JSON schema.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    generator = ExpressGenerator()

    try:
        payload = load_schema(args.schema)

        if args.command == "validate":
            project = generator.build(payload)
            print(f"Schema is valid: {len(project.models)} model(s).")
            return 0

        result = generator.generate(payload, args.output, force=args.force)
        print(f"Generated {len(result.files)} files in {result.output_dir}")
        return 0
    except (OSError, SchemaError) as exc:
        print(f"expressgen: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
