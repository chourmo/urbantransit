from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .gtfs_to_parquet import GTFStoParquet
from .parsers import GTFSParser
from .utils.logging import transitlog

# This file contains utilities to process GTFS files, including cleaning and parsing them concurrently.
# ---------------------------------------------------
# top level functions


def clean_gtfs_folder(path: Path):
    """Clean a folder of GTFS zip files and return a dataframe of valid week and years for each file"""

    stats = []

    for filepath in path.iterdir():
        transitlog.info(f"Processing {path.name} file ")

        try:
            parser = GTFSParser(filepath, fix_inner_folder=True)
            st = parser.calendar_statistics()
            st = st.to_frame("stats")
            st["file"] = path.stem
            stats.append(st)

            to_clean = {}
            if parser.has_inverted_stopcodes():
                stops = parser.get_stops()
                stops.fix_codes()
                transitlog.info(f"File {path.name} has inverted stop codes")
                to_clean["stops.txt"] = stops

            cal = parser.get_calendar()
            if cal.has_inverted_start_end_dates():
                cal.fix_start_end_dates()
                transitlog.info(f"File {path.name} has inverted start and end dates")
                to_clean["calendar.txt"] = cal

            if len(to_clean) > 0:
                parser.update(to_clean)

        except Exception as e:
            transitlog.info(f"Failed to process {path}: {e}")

    return pd.concat(stats)


def parse_gtfs_folder(
    path: Path,
    crs: int,
    year: int,
    week: int,
    max_workers: int | None = None,
    merge_feeds: bool = True,
):
    """
    Parse all GTFS files in a folder concurrently.

    Returns:
        - list[GTFStoParquet] if merge_feeds=False
        - merged GTFStoParquet | None if merge_feeds=True
    """

    files = [p for p in path.iterdir() if p.is_file() and p.suffix == ".zip"]
    if len(files) == 0:
        transitlog.warning(f"No GTFS zip files found in {path}")
        return [] if not merge_feeds else None

    def _parse_one(filepath: Path):
        transitlog.info(f"Parsing {filepath.name}")
        # quick validation + optional zip normalization
        try:
            GTFSParser(filepath, fix_inner_folder=True)
        except Exception as e:
            transitlog.error(f"Failed to validate {filepath.name}: {e}")

        return GTFStoParquet(filepath, crs=crs, week=week, year=year)

    feeds = []
    errors = {}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_parse_one, fp): fp for fp in files}
        for future in as_completed(futures):
            fp = futures[future]
            try:
                feeds.append(future.result())
            except Exception as e:
                errors[fp.name] = str(e)
                transitlog.error(f"Failed to parse {fp.name}: {e}")

    transitlog.info(f"Parsed {len(feeds)} / {len(files)} files")
    if errors:
        transitlog.warning(f"Errors on {len(errors)} files: {errors}")

    if not merge_feeds:
        return feeds

    if len(feeds) == 0:
        return None

    if len(feeds) == 1:
        return feeds[0]

    merged = feeds[0]
    for feed in feeds[1:]:
        merged.merge(feed)
    return merged
