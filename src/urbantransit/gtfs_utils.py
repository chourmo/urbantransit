from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from .parsers import GTFSParser, GTFStoParquet
from .transit import Agencies, Stops
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


def _agency_table(agencies: Agencies) -> pd.DataFrame:
    """agency_gid, agency_name, bbox coordinates and route count of Agencies."""
    data = agencies.data
    n_routes = agencies.routes().groupby("agency_gid").size()
    df = pd.DataFrame(
        {
            "agency_gid": data.index.astype(object),
            "agency_name": data["agency_name"].astype(object).values,
        }
    )
    df["n_routes"] = df["agency_gid"].map(n_routes).fillna(0).astype(int).values
    for c in ("xmin", "ymin", "xmax", "ymax"):
        df[c] = data["bbox"].struct.field(c).astype("float64").values
    return df


def match_agency_gids(
    left_dir: Path,
    right_dir: Path,
    output_path: Path | None = None,
    year: int | None = None,
    week: int | None = None,
    min_iou: float = 0.8,
) -> pd.DataFrame:
    """Create a correspondence table between agency_gid of two Agencies parquet files.

    Candidates are first selected with a geopandas spatial join on bounding boxes.
    Two agencies match when their agency_name are identical and the intersection over
    union of their bounding boxes is at least `min_iou`. When several candidates
    match, the one with the closest number of routes is preferred, then the highest
    bounding box IoU. Each agency is matched at most once.

    Parameters
    ----------
    left_dir, right_dir : Path
        Directories containing an agencies.parquet file, read with `Agencies`.
    output_path : Path, optional
        If given, the correspondence table is saved to this parquet file.
    year, week : int, optional
        Passed to `Agencies.from_parquet`.
    min_iou : float
        Minimum bounding box intersection over union to consider a match.

    Returns
    -------
    pandas.DataFrame
        Columns agency_gid_left, agency_gid_right, iou, n_routes_left, n_routes_right.
    """
    left = _agency_table(Agencies.from_parquet(left_dir, year, week)).add_suffix("_left")
    right = _agency_table(Agencies.from_parquet(right_dir, year, week)).add_suffix(
        "_right"
    )

    # spatial join on bounding box rectangles: only intersecting boxes are candidates
    left_gdf = gpd.GeoDataFrame(
        left,
        geometry=shapely.box(
            left["xmin_left"], left["ymin_left"], left["xmax_left"], left["ymax_left"]
        ),
    )
    right_gdf = gpd.GeoDataFrame(
        right,
        geometry=shapely.box(
            right["xmin_right"],
            right["ymin_right"],
            right["xmax_right"],
            right["ymax_right"],
        ),
    )
    pairs = gpd.sjoin(left_gdf, right_gdf, how="inner", predicate="intersects")
    pairs = pd.DataFrame(pairs.drop(columns=["geometry", "index_right"]))
    pairs = pairs[pairs["agency_name_left"] == pairs["agency_name_right"]].copy()

    w = (
        np.minimum(pairs["xmax_left"], pairs["xmax_right"])
        - np.maximum(pairs["xmin_left"], pairs["xmin_right"])
    ).clip(lower=0)
    h = (
        np.minimum(pairs["ymax_left"], pairs["ymax_right"])
        - np.maximum(pairs["ymin_left"], pairs["ymin_right"])
    ).clip(lower=0)
    inter = w * h
    area_l = (pairs["xmax_left"] - pairs["xmin_left"]) * (
        pairs["ymax_left"] - pairs["ymin_left"]
    )
    area_r = (pairs["xmax_right"] - pairs["xmin_right"]) * (
        pairs["ymax_right"] - pairs["ymin_right"]
    )
    union = area_l + area_r - inter
    # zero-area boxes: only identical boxes match
    same = (
        (pairs["xmin_left"] == pairs["xmin_right"])
        & (pairs["xmax_left"] == pairs["xmax_right"])
        & (pairs["ymin_left"] == pairs["ymin_right"])
        & (pairs["ymax_left"] == pairs["ymax_right"])
    )
    pairs["iou"] = np.where(union > 0, inter / union.where(union > 0, 1), same * 1.0)

    pairs = pairs[pairs["iou"] >= min_iou].copy()
    pairs["route_diff"] = (pairs["n_routes_left"] - pairs["n_routes_right"]).abs()
    pairs = pairs.sort_values(
        ["route_diff", "iou"], ascending=[True, False], kind="stable"
    )

    # one-to-one: keep the best pair for each agency on both sides
    pairs = pairs.drop_duplicates("agency_gid_left").drop_duplicates("agency_gid_right")

    result = (
        pairs[
            [
                "agency_gid_left",
                "agency_gid_right",
                "iou",
                "n_routes_left",
                "n_routes_right",
            ]
        ]
        .sort_values("agency_gid_left")
        .reset_index(drop=True)
    )

    if output_path is not None:
        result.to_parquet(output_path, index=False)
    return result


def _stop_table(stops: Stops) -> gpd.GeoDataFrame:
    """stop_gid, agency_gid, stop_name and geometry of Stops."""
    data = stops.data
    geom = data.geometry
    df = pd.DataFrame(
        {
            "stop_gid": data.index.astype(object),
            "agency_gid": data["agency_gid"].astype(object).values,
            "stop_name": data["stop_name"].astype(object).values,
        }
    )
    return gpd.GeoDataFrame(df, geometry=geom.values, crs=geom.crs)


def match_stop_gids(
    left_dir: Path,
    right_dir: Path,
    agency_matches: pd.DataFrame,
    output_path: Path | None = None,
    year: int | None = None,
    week: int | None = None,
    threshold: float = 50.0,
) -> pd.DataFrame:
    """Create a correspondence table between stop_gid of two Stops parquet files.

    Stops are unique within an agency_gid, so only stops of corresponding agencies
    (given by `agency_matches`) are compared. Two stops match when they have the same
    stop_name and are less than `threshold` meters apart. When several candidates
    match, the nearest one is kept. Each stop is matched at most once.

    Parameters
    ----------
    left_dir, right_dir : Path
        Directories containing a stops.parquet file, read with `Stops`.
    agency_matches : pandas.DataFrame
        Correspondence of agencies with agency_gid_left and agency_gid_right columns,
        as returned by `match_agency_gids`.
    output_path : Path, optional
        If given, the correspondence table is saved to this parquet file.
    year, week : int, optional
        Passed to `Stops.from_parquet`.
    threshold : float
        Maximum distance in meters between two stops.

    Returns
    -------
    pandas.DataFrame
        Columns stop_gid_left, stop_gid_right, agency_gid_left, agency_gid_right
        and distance (meters).
    """
    cols = ["stop_gid", "agency_gid", "stop_name"]
    left = _stop_table(Stops.from_parquet(left_dir, year, week))
    right = _stop_table(Stops.from_parquet(right_dir, year, week))
    left = left.rename(columns={c: f"{c}_left" for c in cols})
    right = right.rename(columns={c: f"{c}_right" for c in cols})

    # stops are unique per agency: only compare stops of matched agencies
    amap = agency_matches[["agency_gid_left", "agency_gid_right"]].rename(
        columns={"agency_gid_right": "matched_agency"}
    )
    left = left.merge(amap, on="agency_gid_left")

    # metric projection to measure distances
    center = gpd.GeoSeries([left.geometry.union_all().centroid], crs=left.crs)
    metric_crs = center.estimate_utm_crs()
    left = left.to_crs(metric_crs)
    right = right.to_crs(metric_crs)
    right["geometry_right"] = right.geometry

    pairs = gpd.sjoin(left, right, how="inner", predicate="dwithin", distance=threshold)
    pairs = pairs[
        (pairs["stop_name_left"] == pairs["stop_name_right"])
        & (pairs["matched_agency"] == pairs["agency_gid_right"])
    ]
    pairs = pd.DataFrame(pairs.drop(columns="index_right"))
    pairs["distance"] = left.geometry.loc[pairs.index].distance(
        gpd.GeoSeries(pairs["geometry_right"], crs=metric_crs)
    )

    pairs = pairs.sort_values("distance", kind="stable")
    pairs = pairs.drop_duplicates("stop_gid_left").drop_duplicates("stop_gid_right")

    result = (
        pairs[
            [
                "stop_gid_left",
                "stop_gid_right",
                "agency_gid_left",
                "agency_gid_right",
                "distance",
            ]
        ]
        .sort_values("stop_gid_left")
        .reset_index(drop=True)
    )

    if output_path is not None:
        result.to_parquet(output_path, index=False)
    return result
