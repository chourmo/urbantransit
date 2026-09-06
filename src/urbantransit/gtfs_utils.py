from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .parsers import GTFSParser, GTFStoParquet
from .utils.logging import transitlog

# This file contains utilities to process GTFS files, including cleaning and parsing them concurrently.
# ---------------------------------------------------
# top level functions


def clean_gtfs_folder(path: Path) -> pd.DataFrame:
    """Validate and repair GTFS archives in a folder.

    Parameters
    ----------
    path : Path
        Directory containing GTFS zip files to inspect.

    Returns
    -------
    pandas.DataFrame
        A dataframe with summary statistics for each successfully processed file.
        The dataframe is empty when no GTFS file could be processed.

    Notes
    -----
    This function is intended for preprocessing: it validates feeds and applies
    small structural repairs such as inverted stop codes or inverted calendar
    date ranges when the parser can correct them safely.
    """

    stats: list[pd.DataFrame] = []

    for filepath in path.iterdir():
        transitlog.info(f"Processing {filepath.name}")

        try:
            parser = GTFSParser(filepath, fix_inner_folder=True)
            st = parser.calendar_statistics()
            st = st.to_frame("stats")
            st["file"] = filepath.stem
            stats.append(st)

            to_clean = {}
            if parser.has_inverted_stopcodes():
                stops = parser.get_stops()
                stops.fix_codes()
                transitlog.info(f"File {filepath.name} has inverted stop codes")
                to_clean["stops.txt"] = stops

            cal = parser.get_calendar()
            if cal is not None and cal.has_inverted_start_end_dates():
                cal.fix_start_end_dates()
                transitlog.info(f"File {filepath.name} has inverted start and end dates")
                to_clean["calendar.txt"] = cal

            if len(to_clean) > 0:
                parser.update(to_clean)

        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            transitlog.warning(f"Failed to process {filepath}: {exc}")

    if not stats:
        return pd.DataFrame(columns=["year", "week", "stats", "file"])

    return pd.concat(stats, ignore_index=True)


def parse_gtfs_folder(
    path: Path,
    crs: int,
    year: int,
    week: int,
    max_workers: int | None = None,
    merge_feeds: bool = True,
):
    """Parse every GTFS archive in a folder and return transit feeds.

    Parameters
    ----------
    path : Path
        Directory containing GTFS zip files.
    crs : int
        Coordinate reference system used by the parsed feed.
    year : int
        Year associated with the transit dataset.
    week : int
        Week identifier associated with the transit dataset.
    max_workers : int | None, optional
        Maximum number of worker threads to use. If None, the default
        ThreadPoolExecutor behavior is used.
    merge_feeds : bool, default=True
        If True, merge all successfully parsed feeds into a single object.
        If False, return one object per archive.

    Returns
    -------
    list[GTFStoParquet] | GTFStoParquet | None
        A list of parsed feeds, a single merged feed, or None if no file could
        be parsed.

    Notes
    -----
    Each archive is validated before conversion. Failures are logged but do not
    interrupt the processing of other feeds in the same directory.
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
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
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
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
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
