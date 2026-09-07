"""Command-line interface for urbantransit."""

import argparse
from pathlib import Path
from typing import Sequence

from .gtfs_parser import GTFSParser
from .gtfs_utils import clean_gtfs_folder, parse_gtfs_folder


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: {path}")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="urbantransit",
        description="Validate, clean and convert GTFS feeds to Parquet.",
    )
    parser.add_argument("--version", action="version", version="urbantransit 0.1.2")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a GTFS feed or folder")
    validate.add_argument("input", type=_path, help="GTFS zip file or directory")
    validate.set_defaults(handler=_validate)

    clean = commands.add_parser("clean", help="clean all GTFS zip files in a directory")
    clean.add_argument("input", type=_path, help="directory containing GTFS zip files")
    clean.set_defaults(handler=_clean)

    parse = commands.add_parser("parse", help="convert GTFS zip files to Parquet")
    parse.add_argument("input", type=_path, help="directory containing GTFS zip files")
    parse.add_argument("output", type=Path, help="destination directory")
    parse.add_argument("--crs", default="EPSG:3857", help="projected CRS (default: EPSG:3857)")
    parse.add_argument("--year", type=int, required=True, help="data year")
    parse.add_argument("--week", type=int, required=True, help="ISO week number")
    parse.add_argument("--workers", type=int, default=None, help="maximum parsing workers")
    parse.set_defaults(handler=_parse)

    return parser


def _validate(args: argparse.Namespace) -> int:
    paths = (
        sorted(args.input.glob("*.zip"))
        if args.input.is_dir()
        else [args.input]
    )
    if not paths:
        raise ValueError(f"no GTFS zip files found in {args.input}")

    for path in paths:
        GTFSParser(path)
        print(f"valid: {path}")
    return 0


def _clean(args: argparse.Namespace) -> int:
    if not args.input.is_dir():
        raise ValueError(f"clean input must be a directory: {args.input}")
    stats = clean_gtfs_folder(args.input)
    print(stats.to_string())
    return 0


def _parse(args: argparse.Namespace) -> int:
    if not args.input.is_dir():
        raise ValueError(f"parse input must be a directory: {args.input}")
    args.output.mkdir(parents=True, exist_ok=True)
    feed = parse_gtfs_folder(
        args.input,
        crs=args.crs,
        year=args.year,
        week=args.week,
        max_workers=args.workers,
    )
    if feed is None:
        raise ValueError(f"no GTFS feed could be parsed from {args.input}")
    feed.to_parquet(args.output)
    print(f"Parquet data written to {args.output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    main()
